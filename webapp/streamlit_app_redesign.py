"""
Regskip Streamlit版(redesign) — パーソナライズ判定レイヤー対応の新UI
======================================================================
regskip_実装仕様_for_claude_code.md のUI仕様(画面構成)に対応する、
新しい画面遷移の実装。既存の webapp/streamlit_app.py(本番稼働中)とは
別ファイルとして用意し、redesignブランチでのみ動作を検証する。

画面遷移: ①ログイン → ②診断(準備中) → ③初期設定 → ④ホーム(動画一覧)
          → ⑤ローディング → ⑥コメント画面
"""

import concurrent.futures
import html
import json
import sys
import time
from pathlib import Path

import streamlit as st
from google_auth_oauthlib.flow import Flow
import httplib2
import google_auth_httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))
from personal_classifier import (
    Category, EmergencyType, PersonalJudgmentClassifier, SurfaceLevel, TatemaePattern,
)
from personal_profile import (
    DisplayAction, PatternSetting, PersonalAction, PersonalProfile, PersonalSimilarityList,
    find_flagged_authors, is_emergency, resolve_display_action, similarity_tier,
)

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
VIDEOS_PAGE_SIZE = 50
MAX_COMMENTS_PER_VIDEO = 200

st.set_page_config(page_title="Regskip", page_icon="🛡️", layout="wide")


# ============ YouTube OAuth / API ヘルパー(既存streamlit_app.pyから流用) ============

def get_flow():
    client_config = {
        "web": {
            "client_id": st.secrets["GOOGLE_CLIENT_ID"],
            "client_secret": st.secrets["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [st.secrets["REDIRECT_URI"]],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=st.secrets["REDIRECT_URI"], autogenerate_code_verifier=False
    )


def build_youtube_service(credentials, timeout: int = 30):
    authed_http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))
    return build("youtube", "v3", http=authed_http, cache_discovery=False)


def execute_with_retry(request, max_retries: int = 4):
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def fetch_comments(credentials, video_id: str, max_results: int = 20) -> list[dict]:
    service = build_youtube_service(credentials)
    comments = []
    page_token = None
    for _ in range(50):
        if len(comments) >= max_results:
            break
        request = service.commentThreads().list(
            part="snippet", videoId=video_id,
            maxResults=min(max_results - len(comments), 100),
            textFormat="plainText", pageToken=page_token,
        )
        response = execute_with_retry(request)
        for item in response.get("items", []):
            top_comment = item["snippet"]["topLevelComment"]
            snippet = top_comment["snippet"]
            comments.append({
                "author": snippet["authorDisplayName"],
                "author_channel_id": snippet.get("authorChannelId", {}).get("value"),
                "text": snippet["textDisplay"],
                "comment_id": top_comment["id"],
                "published_at": snippet.get("publishedAt", ""),
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return comments[:max_results]


def hide_comment_on_youtube(credentials, comment_id: str) -> None:
    service = build_youtube_service(credentials)
    execute_with_retry(service.comments().setModerationStatus(id=comment_id, moderationStatus="heldForReview"))


def ban_author_on_youtube(credentials, comment_id: str) -> None:
    service = build_youtube_service(credentials)
    execute_with_retry(
        service.comments().setModerationStatus(id=comment_id, moderationStatus="rejected", banAuthor=True)
    )


def get_channel_info(credentials) -> dict | None:
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.channels().list(part="contentDetails", mine=True))
    items = resp.get("items", [])
    if not items:
        return None
    return {
        "channel_id": items[0]["id"],
        "uploads_playlist_id": items[0]["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def list_channel_videos(credentials, playlist_id: str, page_token: str | None = None,
                         max_results: int = VIDEOS_PAGE_SIZE) -> tuple[list[dict], str | None]:
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.playlistItems().list(
        part="snippet", playlistId=playlist_id, maxResults=max_results, pageToken=page_token,
    ))
    videos = []
    for item in resp.get("items", []):
        sn = item["snippet"]
        resource_id = sn.get("resourceId", {})
        if resource_id.get("kind") != "youtube#video":
            continue
        videos.append({
            "video_id": resource_id["videoId"],
            "title": sn.get("title", "(タイトル取得不可)"),
            "thumbnail": sn.get("thumbnails", {}).get("default", {}).get("url", ""),
            "published_at": sn.get("publishedAt", ""),
        })
    return videos, resp.get("nextPageToken")


@st.cache_resource(show_spinner=False)
def get_classifier() -> PersonalJudgmentClassifier:
    return PersonalJudgmentClassifier(api_key=st.secrets["GEMINI_API_KEY"])


@st.cache_resource(show_spinner=False)
def get_similarity_list() -> PersonalSimilarityList:
    return PersonalSimilarityList(api_key=st.secrets["GEMINI_API_KEY"])


@st.cache_data(show_spinner=False)
def classify_comment_cached(text: str):
    return get_classifier().classify(text)


def _process_single_comment(comment: dict, user_id: str) -> dict:
    """1件のコメントを判定し、タブ振り分け先まで確定する。"""
    text = comment["text"]
    judgment = classify_comment_cached(text)

    if is_emergency(judgment):
        display = DisplayAction(hide=True, escalate_to_youtube=False, reason="emergency")
        sim_score = 0.0
        tab = "privacy"
    else:
        similarity_list = get_similarity_list()
        matched_action, sim_score = similarity_list.check_similarity(user_id, text)
        profile = get_profile()
        display = resolve_display_action(judgment, profile, similarity_action=matched_action)
        if not display.hide:
            tab = "new"
        elif display.escalate_to_youtube:
            tab = "hidden_youtube"
        else:
            tab = "hidden_regskip"

    return {
        "comment": comment,
        "judgment": judgment,
        "display": display,
        "similarity_score": sim_score,
        "tab": tab,
    }


def process_video_comments(video: dict, status_text=None) -> dict:
    """動画のコメントを取得し、全件判定してタブ別に振り分ける。"""
    comments = fetch_comments(
        st.session_state.rd_credentials, video["video_id"], max_results=MAX_COMMENTS_PER_VIDEO
    )
    user_id = get_user_id()

    results = [None] * len(comments)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_idx = {
            executor.submit(_process_single_comment, c, user_id): i for i, c in enumerate(comments)
        }
        done = 0
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "comment": comments[idx],
                    "judgment": None,
                    "display": DisplayAction(hide=True, escalate_to_youtube=False, reason="error"),
                    "similarity_score": 0.0,
                    "tab": "hidden_regskip",
                }
            done += 1
            if status_text is not None:
                status_text.text(f"判定中…({done}/{len(comments)}件)")

    tabs: dict[str, list] = {"new": [], "hidden_regskip": [], "hidden_youtube": [], "privacy": []}
    for r in results:
        tabs[r["tab"]].append(r)

    flagged_authors = find_flagged_authors(user_id, get_similarity_list())

    return {"tabs": tabs, "flagged_authors": flagged_authors}


def _render_flagged_author_body(author: dict, video_id: str, banner: bool) -> None:
    """要注意ユーザーのカード本体(バナー・タブ共通)。
    アバター・投稿者名・直近◯回の見出しとカテゴリ×建前パターンの内訳は常時表示、
    「コメントを見る」ボタンで該当コメントの本文を展開表示する。"""
    author_id = author["author_channel_id"]
    st.markdown(f"**{author_id}**  \n直近{author['count']}回、見たくない/非表示に分類")
    breakdown_parts = [
        f"{cat.value}×{pat.value} {count}件" for (cat, pat), count in author["breakdown"].items()
    ]
    st.caption("、".join(breakdown_parts))

    reveal_key = f"rd_flagged_reveal_{video_id}_{author_id}"
    if st.button("コメントを見る", key=f"rd_flagged_btn_{video_id}_{author_id}_{banner}"):
        st.session_state[reveal_key] = not st.session_state.get(reveal_key, False)
    if st.session_state.get(reveal_key):
        data = st.session_state.rd_video_comments.get(video_id, {})
        all_entries = [e for entries in data.get("tabs", {}).values() for e in entries]
        for entry in all_entries:
            if entry["comment"].get("author_channel_id") == author_id:
                judgment = entry["judgment"]
                cat_label = judgment.category.value if judgment else ""
                pat_label = judgment.tatemae_pattern.value if judgment else ""
                st.caption(f"{cat_label} / {pat_label}")
                st.write(entry["comment"]["text"])

    btn_cols = st.columns(2)
    if btn_cols[0].button("ユーザーを非表示にする", key=f"rd_flagged_ban_{video_id}_{author_id}_{banner}", type="primary"):
        data = st.session_state.rd_video_comments.get(video_id, {})
        all_entries = [e for entries in data.get("tabs", {}).values() for e in entries]
        for entry in all_entries:
            if entry["comment"].get("author_channel_id") == author_id:
                try:
                    ban_author_on_youtube(st.session_state.rd_credentials, entry["comment"]["comment_id"])
                except Exception:
                    pass
        st.session_state.setdefault("rd_dismissed_authors", set()).add(author_id)
        st.rerun()
    if btn_cols[1].button("このままにする", key=f"rd_flagged_keep_{video_id}_{author_id}_{banner}"):
        st.session_state.setdefault("rd_dismissed_authors", set()).add(author_id)
        st.rerun()


@st.cache_data(show_spinner=False)
def rephrase_comment_cached(text: str) -> str:
    """『言い換えて見る』ボタン用: 攻撃的な言葉を使わずに要点だけ伝える言い換えをオンデマンド生成する。"""
    client = get_classifier().client
    prompt = (
        "このコメントの言いたいことを、攻撃的な言葉を使わずに伝えてください。"
        "伝えるべき内容が特になければ、その旨を伝えてください。\n\n"
        f"コメント:「{text}」"
    )
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    return response.text.strip()


# ============ セッション状態の初期化 ============
_defaults = {
    "rd_step": "diagnosis_placeholder",
    "rd_credentials": None,
    "rd_code_verifier": None,
    "rd_channel_id": None,
    "rd_uploads_playlist_id": None,
    "rd_videos": [],
    "rd_videos_next_page_token": None,
    "rd_profile": None,
    "rd_selected_video": None,
    "rd_video_comments": {},  # video_id -> list[{comment_dict, judgment, display_action}]
}
for key, default in _defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default


def get_user_id() -> str:
    """PersonalProfile / PersonalSimilarityList のキーとして使うID。チャンネルIDを使う。"""
    return st.session_state.rd_channel_id or "unknown"


def get_profile() -> PersonalProfile:
    if st.session_state.rd_profile is None:
        st.session_state.rd_profile = PersonalProfile(user_id=get_user_id())
    return st.session_state.rd_profile


# ============ OAuthコールバック処理 ============
query_params = st.query_params
if "code" in query_params and st.session_state.rd_credentials is None:
    restored = {}
    if "state" in query_params:
        try:
            parsed = json.loads(query_params["state"])
            if isinstance(parsed, dict):
                restored = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    flow = get_flow()
    flow.code_verifier = restored.get("code_verifier") or st.session_state.get("rd_code_verifier")

    try:
        flow.fetch_token(code=query_params["code"])
    except Exception:
        st.error("Googleとの連携に失敗しました。お手数ですが、最初からもう一度ログインをお試しください。")
        st.query_params.clear()
        if st.button("最初からやり直す", use_container_width=True):
            st.session_state.rd_step = "connect"
            st.rerun()
        st.stop()

    st.session_state.rd_credentials = flow.credentials
    st.query_params.clear()

    # 初期設定(診断・カテゴリ設定)はGoogle連携より前に完了しているはずなので、
    # ここではチャンネル情報を取得してそのままホームへ進む
    with st.spinner("チャンネル情報を確認しています…"):
        info = get_channel_info(st.session_state.rd_credentials)
    if info is None:
        st.error("チャンネル情報を取得できませんでした。YouTubeチャンネルがあるアカウントでログインしているか確認してください。")
        st.stop()
    st.session_state.rd_channel_id = info["channel_id"]
    st.session_state.rd_uploads_playlist_id = info["uploads_playlist_id"]
    get_profile().user_id = info["channel_id"]
    st.session_state.rd_step = "home"
    st.rerun()


# ============ ヘッダー ============
def render_header(show_back: bool = False):
    cols = st.columns([1, 8, 1])
    with cols[0]:
        if show_back:
            if st.button("←", key="rd_back_btn"):
                st.session_state.rd_step = "home"
                st.rerun()
    with cols[1]:
        st.markdown("### 🛡️ Regskip")
    with cols[2]:
        st.markdown("👤")


# ============ ③ ログイン(診断・初期設定の後に移動) ============
# Google連携できなくても、診断・初期設定は誰でも試せるようにするため、
# ログインは「この設定で始める」を押した後に行う形に変更した。
if st.session_state.rd_step == "connect":
    st.title("Regskip")
    st.write("設定ありがとうございます。続けるには、YouTubeアカウントと連携してください。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent", access_type="offline",
        state=json.dumps({"code_verifier": flow.code_verifier}),
    )
    st.session_state.rd_code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True, type="primary")

# ============ ① 診断画面(準備中プレースホルダー) ============
elif st.session_state.rd_step == "diagnosis_placeholder":
    render_header(show_back=False)
    st.write("")
    st.markdown(
        "<div style='text-align:center; padding: 3rem 0;'>"
        "<div style='font-size:3rem;'>🔧</div>"
        "<h3>診断機能は只今準備中です</h3>"
        "<p style='color:#5B6B6A;'>あなたに合った設定をおすすめする機能を準備しています。<br>今回は初期設定から進めてください。</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("初期設定に進む", use_container_width=True, type="primary"):
        st.session_state.rd_step = "initial_settings"
        st.rerun()

# ============ ② 初期設定画面(PersonalProfileの手動編集UI) ============
elif st.session_state.rd_step == "initial_settings":
    render_header(show_back=(bool(st.session_state.rd_channel_id)))
    st.subheader("初期設定")
    st.caption("攻撃的な言い方をされた時、どう扱ってほしいかをカテゴリごとに選んでください。あとからいつでも変更できます。")

    profile = get_profile()

    # カテゴリごとに文言・具体例を変えたラベル(4カテゴリ×7パターン=28通り)。
    # (アイコン, チェックボックスの短いラベル, ホバー表示用の具体例)
    PATTERN_UI = {
        Category.APPEARANCE: {
            TatemaePattern.OTHER_COMPARISON: ("👤", "他の人と比べられる", "「〇〇さんの方が可愛いのに」"),
            TatemaePattern.FAN_DEPARTURE: ("✨", "ファンをやめると言われる", "「見た目が無理になったので見なくなります」"),
            TatemaePattern.RHETORICAL_QUESTION: ("💬", "若さに関する質問", "「その若作り、いつまで続けるの?」"),
            TatemaePattern.BACKHANDED_COMPLIMENT: ("🩹", "皮肉っぽい褒め言葉", "「本当に自然な仕上がりですね(笑)」"),
            TatemaePattern.FALSE_CONSENSUS: ("❝", "「みんな」を主語にされる", "「みんな見た目のこと気にしてるよ」"),
            TatemaePattern.POLITE_INTERROGATION: ("😟", "丁寧な言葉で長々問い詰められる", "「差し支えなければ、なぜそのメイクなのか教えてください」"),
            TatemaePattern.FAKE_ADVICE: ("💭", "アドバイスのふりをした指摘", "「老婆心ながら、そのメイクは正直……」"),
        },
        Category.PERSONALITY: {
            TatemaePattern.OTHER_COMPARISON: ("👤", "他の人と性格を比べられる", "「〇〇さんの方が気さくで好き」"),
            TatemaePattern.FAN_DEPARTURE: ("✨", "呆れて応援をやめると言われる", "「性格知って冷めたので離れます」"),
            TatemaePattern.RHETORICAL_QUESTION: ("💬", "本性を疑うような質問", "「それが本当のあなたなんですか?」"),
            TatemaePattern.BACKHANDED_COMPLIMENT: ("🩹", "遠回しに人間性を疑う褒め言葉", "「良い人ぶるの上手だよね」"),
            TatemaePattern.FALSE_CONSENSUS: ("❝", "「みんな」に嫌われてると言われる", "「みんな陰で言ってるよ、あなたのこと」"),
            TatemaePattern.POLITE_INTERROGATION: ("😟", "丁寧な言葉で人格を長々問い詰められる", "「なぜそのような態度を取られたのか、ご説明いただけますか」"),
            TatemaePattern.FAKE_ADVICE: ("💭", "説教のような助言", "「経験上、そういう性格はいずれ壁にぶつかりますよ」"),
        },
        Category.ACTIVITY_QUALITY: {
            TatemaePattern.OTHER_COMPARISON: ("👤", "他の配信者と実力を比べられる", "「〇〇さんの方が編集うまいよね」"),
            TatemaePattern.FAN_DEPARTURE: ("✨", "つまらないから見るのをやめると言われる", "「最近の企画つまらないので見なくなりました」"),
            TatemaePattern.RHETORICAL_QUESTION: ("💬", "企画の意図を疑うような質問", "「その企画、もう擦り切れてない?」"),
            TatemaePattern.BACKHANDED_COMPLIMENT: ("🩹", "皮肉っぽく編集や企画を褒められる", "「尺稼ぎ上手だね」"),
            TatemaePattern.FALSE_CONSENSUS: ("❝", "「みんな」がつまらないと思ってると言われる", "「みんな飽きてると思うよ、この企画」"),
            TatemaePattern.POLITE_INTERROGATION: ("😟", "丁寧な言葉で構成を長々問い詰められる", "「なぜあのような構成にされたのか気になっております」"),
            TatemaePattern.FAKE_ADVICE: ("💭", "上から目線の改善アドバイス", "「編集の学校行った方がいいかもね」"),
        },
        Category.MORAL_LECTURE: {
            TatemaePattern.OTHER_COMPARISON: ("👤", "他の配信者はちゃんとしてると比べられる", "「他の配信者はもっと気をつけてるよ」"),
            TatemaePattern.FAN_DEPARTURE: ("✨", "モラルがないから離れると言われる", "「その考え方が無理で応援やめます」"),
            TatemaePattern.RHETORICAL_QUESTION: ("💬", "常識を疑うような質問", "「スポンサーさんは大丈夫なんですか、これ」"),
            TatemaePattern.BACKHANDED_COMPLIMENT: ("🩹", "皮肉っぽく行儀の良さを褒められる", "「ここまで情報出しといて特定されないと思ってるならおめでたいね」"),
            TatemaePattern.FALSE_CONSENSUS: ("❝", "「みんな」が呆れてると言われる", "「界隈全体がドン引きしてると思います」"),
            TatemaePattern.POLITE_INTERROGATION: ("😟", "丁寧な言葉でマナーを長々問い詰められる", "「配慮に欠けると思うのですが、いかがお考えでしょうか」"),
            TatemaePattern.FAKE_ADVICE: ("💭", "説教くさい注意", "「一応言っておくけど、それ結構際どいよ」"),
        },
    }

    for category in Category:
        if category == Category.NONE:
            continue
        with st.container(border=True):
            st.markdown(f"**{category.value}**")

            current_attack = profile.get_attack_action(category)
            attack_checked = st.checkbox(
                "攻撃的な言い方を隠す",
                value=(current_attack != PersonalAction.NORMAL),
                key=f"rd_attack_action_{category.value}",
                help="例:「ブスすぎて無理」",
            )
            profile.attack_action[category] = (
                PersonalAction.HIDE_REGSKIP if attack_checked else PersonalAction.NORMAL
            )
            st.caption("※該当する項目がない場合は、その他を選択してください")

            st.caption("軽い言い方でも、これは無理というものがあれば選んでください")
            for pattern in TatemaePattern:
                if pattern == TatemaePattern.NONE:
                    continue
                icon, label, example = PATTERN_UI[category][pattern]
                key = (category, pattern)
                currently_on = profile.pattern_action.get(key, PatternSetting(action=PersonalAction.NORMAL)).action != PersonalAction.NORMAL
                checked = st.checkbox(
                    f"{icon} {label}",
                    value=currently_on,
                    key=f"rd_pattern_{category.value}_{pattern.value}",
                    help=f"例:{example}",
                )
                if checked:
                    profile.pattern_action[key] = PatternSetting(action=PersonalAction.HIDE_REGSKIP)
                elif key in profile.pattern_action:
                    del profile.pattern_action[key]

            # 「その他」: 7パターンのどれにも当てはまらないが軽い言い方でも隠したい場合の受け皿。
            # カテゴリ全体の「該当なし」建前(軽度)として扱う。
            other_key = (category, TatemaePattern.NONE)
            other_on = profile.pattern_action.get(other_key, PatternSetting(action=PersonalAction.NORMAL)).action != PersonalAction.NORMAL
            other_checked = st.checkbox(
                "🔘 その他(軽い言い方でも無理なもの)",
                value=other_on,
                key=f"rd_pattern_{category.value}_other",
            )
            if other_checked:
                profile.pattern_action[other_key] = PatternSetting(action=PersonalAction.HIDE_REGSKIP)
            elif other_key in profile.pattern_action:
                del profile.pattern_action[other_key]

    with st.container(border=True):
        st.markdown("**プライバシー**")

    st.divider()
    button_label = "この設定で始める" if st.session_state.rd_credentials is None else "この設定を保存する"
    if st.button(button_label, use_container_width=True, type="primary"):
        if st.session_state.rd_credentials is None:
            # まだGoogle連携していない(初回設定フロー) -> ここで初めてログインへ進む
            st.session_state.rd_step = "connect"
            st.rerun()
        else:
            # すでに連携済み(ハンバーガーメニューから設定を編集しに来たケース) -> そのままホームへ戻る
            st.session_state.rd_step = "home"
            st.rerun()

# ============ ④ ホーム画面(動画一覧) ============
elif st.session_state.rd_step == "home":
    render_header(show_back=False)

    if not st.session_state.rd_videos:
        with st.spinner("動画一覧を読み込んでいます…"):
            videos, next_token = list_channel_videos(
                st.session_state.rd_credentials, st.session_state.rd_uploads_playlist_id
            )
            st.session_state.rd_videos = videos
            st.session_state.rd_videos_next_page_token = next_token

    hamburger_cols = st.columns([1, 1, 6])
    with hamburger_cols[0]:
        with st.popover("☰"):
            if st.button("初期設定", key="rd_menu_settings", use_container_width=True):
                st.session_state.rd_step = "initial_settings"
                st.rerun()
            st.button("診断(準備中)", key="rd_menu_diagnosis", use_container_width=True, disabled=True)

    st.write("")
    cols_per_row = 4
    videos = st.session_state.rd_videos
    for row_start in range(0, len(videos), cols_per_row):
        row_videos = videos[row_start:row_start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, video in zip(cols, row_videos):
            with col:
                if video["thumbnail"]:
                    st.image(video["thumbnail"], use_container_width=True)
                new_count = len(st.session_state.rd_video_comments.get(video["video_id"], {}).get("new", []))
                title_line = video["title"]
                if new_count > 0:
                    title_line = f"🔴{new_count} {title_line}"
                if st.button(title_line, key=f"rd_video_{video['video_id']}", use_container_width=True):
                    st.session_state.rd_selected_video = video
                    st.session_state.rd_step = "loading"
                    st.rerun()

    if st.session_state.rd_videos_next_page_token:
        if st.button("過去の動画をさらに読み込む", use_container_width=True):
            with st.spinner("読み込んでいます…"):
                more, next_token = list_channel_videos(
                    st.session_state.rd_credentials, st.session_state.rd_uploads_playlist_id,
                    page_token=st.session_state.rd_videos_next_page_token,
                )
                st.session_state.rd_videos.extend(more)
                st.session_state.rd_videos_next_page_token = next_token
            st.rerun()

# ============ ⑤ ローディング画面 ============
elif st.session_state.rd_step == "loading":
    render_header(show_back=True)
    video = st.session_state.rd_selected_video
    row = st.columns([1, 5])
    with row[0]:
        if video and video["thumbnail"]:
            st.image(video["thumbnail"])
    with row[1]:
        st.write(f"**{video['title']}**" if video else "")

    status_text = st.empty()
    with st.spinner("コメントを読み込み中です…"):
        result = process_video_comments(video, status_text=status_text)
    st.session_state.rd_video_comments[video["video_id"]] = result
    st.session_state.rd_step = "comments"
    st.rerun()


# ============ ⑥ コメント画面 ============
elif st.session_state.rd_step == "comments":
    render_header(show_back=True)
    video = st.session_state.rd_selected_video
    video_id = video["video_id"]
    data = st.session_state.rd_video_comments.get(video_id, {"tabs": {"new": [], "hidden_regskip": [], "hidden_youtube": [], "privacy": []}, "flagged_authors": []})

    row = st.columns([1, 5])
    with row[0]:
        if video["thumbnail"]:
            st.image(video["thumbnail"])
    with row[1]:
        st.write(f"**{video['title']}**")

    # --- 要注意ユーザーの常時バナー ---
    dismissed = st.session_state.setdefault("rd_dismissed_authors", set())
    for author in data["flagged_authors"]:
        author_id = author["author_channel_id"]
        if author_id in dismissed:
            continue
        with st.container(border=True):
            _render_flagged_author_body(author, video_id, banner=True)

    def _move_comment(tab_key: str, comment_id: str, new_action: PersonalAction):
        entries = data["tabs"][tab_key]
        idx = next((i for i, e in enumerate(entries) if e["comment"]["comment_id"] == comment_id), None)
        if idx is None:
            return
        entry = entries.pop(idx)
        judgment = entry["judgment"]
        get_similarity_list().mark_comment(
            user_id=get_user_id(), comment=entry["comment"]["text"], action=new_action,
            category=judgment.category, tatemae_pattern=judgment.tatemae_pattern,
            surface_level=judgment.surface_level,
            author_channel_id=entry["comment"].get("author_channel_id"),
        )
        entry["display"] = DisplayAction(
            hide=(new_action != PersonalAction.NORMAL),
            escalate_to_youtube=(new_action == PersonalAction.HIDE_YOUTUBE),
            reason="personal_similarity",
        )
        new_tab = "new" if new_action == PersonalAction.NORMAL else (
            "hidden_youtube" if new_action == PersonalAction.HIDE_YOUTUBE else "hidden_regskip"
        )
        entry["tab"] = new_tab
        data["tabs"][new_tab].append(entry)
        if new_action == PersonalAction.HIDE_YOUTUBE:
            try:
                hide_comment_on_youtube(st.session_state.rd_credentials, comment_id)
            except Exception:
                pass

    def _render_open_comment(entry: dict, tab_key: str):
        c = entry["comment"]
        comment_id = c["comment_id"]
        with st.container(border=True):
            top = st.columns([5, 1])
            with top[0]:
                st.write(f"**{c['author']}**  \n:gray[{c.get('published_at', '')[:10]}]")
            with top[1]:
                with st.popover("⋮"):
                    if st.button("ユーザーを非表示にする", key=f"rd_ban_{comment_id}"):
                        try:
                            ban_author_on_youtube(st.session_state.rd_credentials, comment_id)
                            st.success("このユーザーの今後のコメントを拒否するよう設定しました。")
                        except Exception as e:
                            st.error(f"操作に失敗しました: {e}")
            st.write(c["text"])
            if entry["similarity_score"] and similarity_tier(entry["similarity_score"]) == "grey":
                st.caption("⚠️ 似たコメントに反応したことがあります")
            btn_cols = st.columns(2)
            if btn_cols[0].button("見たくない", key=f"rd_want_{tab_key}_{comment_id}"):
                _move_comment(tab_key, comment_id, PersonalAction.HIDE_REGSKIP)
                st.rerun()
            if btn_cols[1].button("非表示にしたい", key=f"rd_hide_{tab_key}_{comment_id}", type="primary"):
                _move_comment(tab_key, comment_id, PersonalAction.HIDE_YOUTUBE)
                st.rerun()

    def _render_hidden_comment(entry: dict, tab_key: str):
        c = entry["comment"]
        comment_id = c["comment_id"]
        judgment = entry["judgment"]
        with st.container(border=True):
            st.write(f"**{c['author']}**")
            if judgment:
                st.caption(f"カテゴリ: {judgment.category.value}")
            reveal_key = f"rd_rephrase_{comment_id}"
            if st.button("言い換えて見る", key=f"rd_rephrase_btn_{comment_id}"):
                st.session_state[reveal_key] = rephrase_comment_cached(c["text"])
            if reveal_key in st.session_state:
                st.info(st.session_state[reveal_key])

    tab_new, tab_want, tab_hidden, tab_privacy, tab_flagged = st.tabs([
        f"新着コメント ({len(data['tabs']['new'])})",
        f"見たくない ({len(data['tabs']['hidden_regskip'])})",
        f"非表示 ({len(data['tabs']['hidden_youtube'])})",
        f"🔴 プライバシー ({len(data['tabs']['privacy'])})",
        f"要注意ユーザー ({len(data['flagged_authors'])})",
    ])

    with tab_new:
        for entry in data["tabs"]["new"]:
            _render_open_comment(entry, "new")
    with tab_want:
        for entry in data["tabs"]["hidden_regskip"]:
            _render_open_comment(entry, "hidden_regskip")
    with tab_hidden:
        for entry in data["tabs"]["hidden_youtube"]:
            _render_hidden_comment(entry, "hidden_youtube")
    with tab_privacy:
        for entry in data["tabs"]["privacy"]:
            c = entry["comment"]
            with st.container(border=True):
                st.write(f"**{c['author']}**")
                st.caption(f"緊急区分: {entry['judgment'].emergency.value}")
                st.write(c["text"])
    with tab_flagged:
        for author in data["flagged_authors"]:
            with st.container(border=True):
                _render_flagged_author_body(author, video_id, banner=False)
