"""
Regskip Streamlit版
======================
本人がブラウザで直接使えるWebアプリ。

【デプロイ方法】
1. このファイル一式をGitHubリポジトリにアップロード
2. https://share.streamlit.io でデプロイ(無料)
3. デプロイ後のURL(例: https://regnare-xxxx.streamlit.app)を確認
4. Google Cloud Consoleで「Webアプリケーション」タイプのOAuthクライアントを新規作成し、
   承認済みのリダイレクトURIに上記URLを設定
5. StreamlitのSecrets設定に、GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
   REDIRECT_URI / GEMINI_API_KEY を設定
"""

import html
import json
import math
import re
import sys
import time
import concurrent.futures
from pathlib import Path
from collections import Counter

import streamlit as st
from google_auth_oauthlib.flow import Flow
import httplib2
import google_auth_httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google import genai
from google.genai import types
import altair as alt
import pandas as pd

# modulus/ 配下の判定モジュール(LLMベース)をimportできるようにする
sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))
from hybrid_classifier import HybridClassifier
from user_feedback import save_feedback_entry

# ============ 設定 ============
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

_current_step = st.session_state.get("step", "landing")
st.set_page_config(
    page_title="Regskip", page_icon="🛡️",
    layout="wide" if _current_step == "inbox" else "centered",
)

st.markdown("""
<style>
h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
[data-testid="stCaptionContainer"] { color: #5B6B6A; }
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 10px;
    padding: 0.25rem 0.25rem;
}
[data-testid="stExpander"] { border-radius: 10px; }
[data-testid="stMetric"] {
    background: #F1F4F3;
    border-radius: 10px;
    padding: 0.75rem 1rem;
}
.stButton > button, .stFormSubmitButton > button, .stLinkButton > a {
    border-radius: 8px;
    font-weight: 500;
}
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.speech-bubble-wrap {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin: 4px 0 8px;
}
.speech-avatar {
    font-size: 30px;
    line-height: 1;
    flex-shrink: 0;
    margin-top: 2px;
}
.speech-bubble {
    position: relative;
    background: #FFF6DA;
    border: 1px solid #F0DFA0;
    border-radius: 18px;
    border-top-left-radius: 4px;
    padding: 16px 20px;
    font-size: 0.95rem;
    line-height: 1.8;
    color: #4a4020;
}
.speech-bubble b { color: #2F6F62; }
</style>
""", unsafe_allow_html=True)

_STEP_ORDER = [
    ("category", "① カテゴリ選択"),
    ("connect", "② 連携"),
]


def render_step_indicator(current_step: str) -> None:
    """3ステップウィザードの進捗を表示する(見た目のみ、状態は変更しない)。"""
    if current_step not in {s for s, _ in _STEP_ORDER}:
        return
    pills = []
    for step_key, label in _STEP_ORDER:
        active = step_key == current_step
        color = "#2F6F62" if active else "#9AA6A5"
        weight = "700" if active else "400"
        bg = "#E7F1EE" if active else "transparent"
        pills.append(
            f'<span style="color:{color};font-weight:{weight};background:{bg};'
            f'padding:2px 10px;border-radius:12px;margin-right:6px;font-size:0.9rem;">{label}</span>'
        )
    st.markdown(" ".join(pills), unsafe_allow_html=True)


_RISK_BADGE_COLORS = {"低": "#2F6F55", "注意": "#8F5F0C", "やや高め": "#8F5F0C", "高": "#A13327"}


def render_risk_badge(level: str) -> str:
    """診断結果のリスクレベルを色付きバッジのHTMLとして返す(表示専用)。"""
    color = _RISK_BADGE_COLORS.get(level, "#5B6B6A")
    return (
        f'<span style="background:{color}22;color:{color};padding:2px 10px;'
        f'border-radius:12px;font-weight:600;">{level}</span>'
    )


CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "プライバシー"]


CATEGORY_COLORS = {
    "外見": "#e07a5f",
    "人間性": "#f2994a",
    "活動クオリティ": "#e0b243",
    "モラル・マナー説教": "#9b6b9e",
    "プライバシー": "#1F7A5C",
    "非該当": "#3d8361",
}


def display_category_label(key: str) -> str:
    """内部カテゴリキーを画面表示用のラベルに変換する(データ・判定ロジック上のキーは「非該当」のまま扱う)。"""
    return "肯定的なコメント" if key == "非該当" else key


def render_category_bar_chart(counts: dict, buckets: list[str]) -> None:
    """カテゴリ別件数を、色分けした横棒グラフで表示する(表示専用)。"""
    total = sum(counts.values()) or 1
    df = pd.DataFrame([
        {
            "カテゴリ": display_category_label(b),
            "件数": counts[b],
            "割合": counts[b] / total * 100,
            "color_key": b,
            "ラベル": f"{counts[b] / total * 100:.1f}%({counts[b]}件)",
        }
        for b in buckets
    ])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("割合:Q", title="割合(%)", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("カテゴリ:N", sort="-x", title=None),
        color=alt.Color(
            "color_key:N",
            scale=alt.Scale(domain=list(CATEGORY_COLORS.keys()), range=list(CATEGORY_COLORS.values())),
            legend=None,
        ),
        tooltip=[alt.Tooltip("カテゴリ:N"), alt.Tooltip("件数:Q"), alt.Tooltip("割合:Q", format=".1f")],
    )
    text = bars.mark_text(align="left", dx=5, color="#666").encode(text="ラベル:N")
    st.altair_chart((bars + text).properties(height=34 * len(df)), use_container_width=True)


def render_speech_bubble(text: str, avatar: str = "🛡️") -> None:
    """AIからのメッセージを、吹き出し風のHTMLで表示する(表示専用)。
    テキストはHTMLエスケープしたうえで整形するため、任意のHTMLは注入されない。"""
    escaped = html.escape(text)
    escaped = re.sub(r"【(.+?)】", r"<b>【\1】</b>", escaped)
    escaped = escaped.replace("\n", "<br>")
    st.markdown(
        f'<div class="speech-bubble-wrap">'
        f'<div class="speech-avatar">{avatar}</div>'
        f'<div class="speech-bubble">{escaped}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ============ 動画選択・APIクォータ設定 ============
VIDEOS_PAGE_SIZE = 10          # 動画一覧の1ページあたり取得件数
DEFAULT_SELECTED_VIDEOS = 5    # デフォルトでチェックを入れる最新動画数
MAX_VIDEOS_PER_RUN = 5         # 一度に処理できる動画数の上限
MAX_COMMENTS_PER_VIDEO = 200   # 1動画あたりのコメント取得上限

# ============ 動画ID抽出 ============
def extract_video_id(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    patterns = [
        r"(?:v=|/videos/|embed/|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)

    candidate = text.split("&")[0].split("?")[0]
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        return candidate

    return text

# ============ 判定モジュール(modulus/のLLMベース判定を利用) ============
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])


GENERATION_MODEL = "gemini-3.6-flash"



ACTION_SUGGESTIONS_PROMPT = """\
以下はYouTube動画に寄せられたコメント群です。
この中から、動画制作者にとって参考になる次の2種類の情報だけを抽出してください。

1. 次に見たい・作ってほしい企画やコンテンツのリクエスト
2. 攻撃的でない、建設的な改善提案(編集・構成・音質など)

個人攻撃・誹謗中傷・単なる感想やあいさつは無視してください。
該当する内容がなければ、その見出しの下に「特にありませんでした」と書いてください。
個々のコメントをそのまま引用せず、傾向として日本語で簡潔にまとめてください。

文体について: 事務的な報告書のような硬い言葉遣いではなく、
仲の良いスタッフや友人が気さくに、時々ユーモアを交えながら
話しかけてくるような温かいトーンで書いてください。絵文字を1〜2個使ってもかまいません。
ただし内容の実用性・具体性は損なわないでください。

必ず次の見出し構成で出力してください:
【次にしてほしいこと】
(箇条書き)

【改善提案】
(箇条書き)

コメント一覧:
{comments}"""


def extract_action_suggestions(comments_texts: list[str]) -> str:
    """コメント群から「次にしてほしい企画」「建設的な改善提案」だけをAIに抽出させる。
    個人攻撃や単なる感想は除外する。"""
    if not comments_texts:
        return "コメントがありませんでした。"
    client = get_gemini_client()
    joined = "\n".join(f"- {t}" for t in comments_texts[:200])
    prompt = ACTION_SUGGESTIONS_PROMPT.format(comments=joined)
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
    )
    return response.text

@st.cache_resource(show_spinner=False)
def get_hybrid_classifier() -> HybridClassifier:
    """LLM判定＋ユーザーフィードバック記憶を組み合わせた判定器を、プロセス内で使い回す。"""
    classifier = HybridClassifier(api_key=st.secrets["GEMINI_API_KEY"])
    classifier.fit()
    return classifier


@st.cache_data(show_spinner=False)
def classify_comment(text: str) -> dict:
    """同じコメント文はキャッシュを使い回し、再判定のたびにAPI課金・待ち時間が発生しないようにする。"""
    result = get_hybrid_classifier().classify(text)
    return {
        "category": result.category,
        "judgement": result.judgement,
        "reason": result.reason,
        "source": result.source,
    }


def classify_comment_safe(text: str) -> dict:
    """classify_commentを実行し、リトライしても失敗した場合は「グレー」に倒して処理を止めない
    (通信エラー1件で全体がクラッシュしないようにするため)。"""
    try:
        return classify_comment(text)
    except Exception as e:
        return {
            "category": None,
            "judgement": "グレー",
            "reason": f"判定中にエラーが発生したため、確認のためグレーゾーンに振り分けました({e})",
            "source": "error",
        }


def classify_comments_parallel(texts: list[str], status_text=None, max_workers: int = 6) -> list[dict]:
    """複数コメントを並列に判定する(逐次実行より数倍速い)。順序はtextsと対応させて返す。"""
    results: list[dict] = [{}] * len(texts)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(classify_comment_safe, t): i for i, t in enumerate(texts)}
        done = 0
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done += 1
            if status_text is not None:
                status_text.text(f"判定中...（{done}/{len(texts)}件）")
    return results

# ============ YouTube OAuth (Web版) ============
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
    """タイムアウト付きでYouTube APIクライアントを作る。
    google-api-python-clientの標準経路(build(credentials=...))は内部のhttplib2に
    タイムアウトが設定されず、通信が詰まると画面が「処理中」のまま無限に固まるため。"""
    authed_http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))
    return build("youtube", "v3", http=authed_http, cache_discovery=False)


def execute_with_retry(request, max_retries: int = 4):
    """通信の一時的な失敗(タイムアウト・5xx・レート制限)に対して指数バックオフで再試行する。
    1回失敗しただけで動画をスキップしてしまわないようにするため。"""
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except OSError:
            # socket.timeout・ssl.SSLErrorなど、通信が詰まった/切れた場合
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def fetch_comments(credentials, video_id: str, max_results: int = 20) -> list[dict]:
    service = build_youtube_service(credentials)
    comments = []
    page_token = None
    for _ in range(50):  # ページ送りの上限(1ページ最大100件なので通常は数ページで足りる、無限ループ防止の保険)
        if len(comments) >= max_results:
            break
        request = service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=min(max_results - len(comments), 100),
            textFormat="plainText",
            pageToken=page_token,
        )
        response = execute_with_retry(request)
        for item in response.get("items", []):
            top_comment = item["snippet"]["topLevelComment"]
            snippet = top_comment["snippet"]
            comments.append({
                "author": snippet["authorDisplayName"],
                "text": snippet["textDisplay"],
                "comment_id": top_comment["id"],
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return comments[:max_results]


def hide_comment_on_youtube(credentials, comment_id: str) -> None:
    """指定コメントをYouTube上で保留(heldForReview)にする。
    一般公開のコメント欄からは見えなくなるが、YouTube Studio上で本人がいつでも取り消せる。"""
    service = build_youtube_service(credentials)
    execute_with_retry(service.comments().setModerationStatus(id=comment_id, moderationStatus="heldForReview"))


def reply_to_comment_on_youtube(credentials, comment_id: str, reply_text: str) -> None:
    """指定コメントに返信を投稿する。"""
    service = build_youtube_service(credentials)
    # 返信投稿は再試行すると二重投稿になりうるため、あえてリトライしない
    service.comments().insert(
        part="snippet",
        body={"snippet": {"parentId": comment_id, "textOriginal": reply_text}},
    ).execute()


def get_channel_info(credentials) -> dict | None:
    """ログインユーザー自身のチャンネルID・アップロード済み動画プレイリストIDを取得"""
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
    """アップロード済み動画一覧を新しい順に取得(低クォータ)"""
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.playlistItems().list(
        part="snippet",
        playlistId=playlist_id,
        maxResults=max_results,
        pageToken=page_token,
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


def search_channel_videos(credentials, channel_id: str, query: str,
                           max_results: int = VIDEOS_PAGE_SIZE) -> list[dict]:
    """タイトルキーワードで動画を検索(高クォータのため明示検索時のみ使用)"""
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.search().list(
        part="snippet",
        channelId=channel_id,
        q=query,
        type="video",
        order="date",
        maxResults=max_results,
    ))
    videos = []
    for item in resp.get("items", []):
        sn = item["snippet"]
        videos.append({
            "video_id": item["id"]["videoId"],
            "title": sn.get("title", "(タイトル取得不可)"),
            "thumbnail": sn.get("thumbnails", {}).get("default", {}).get("url", ""),
            "published_at": sn.get("publishedAt", ""),
        })
    return videos

def render_comment_card(c: dict, key_name: str) -> None:
    """コメント1件をカードとして表示する(見たくないタブの伏せ字表示、グレーゾーンの判断ボタン、
    YouTube上での非表示・返信操作を含む)。"""
    with st.container(border=True):
        st.write(f"**{c['author']}**")
        if key_name == "見たくない":
            revealed = c["comment_key"] in st.session_state.revealed_comment_keys
            if not revealed:
                st.warning("内容を確認しますか？見ずに非表示にすることもできます。")
                if st.button(
                    "👁 はい、本文を表示する", key=f"reveal_{c['comment_key']}",
                    type="primary",
                ):
                    st.session_state.revealed_comment_keys.add(c["comment_key"])
                    st.rerun()
            else:
                st.write(c["text"])
                if st.button("🙈 本文を隠す", key=f"hide_reveal_{c['comment_key']}"):
                    st.session_state.revealed_comment_keys.discard(c["comment_key"])
                    st.rerun()
        else:
            st.write(c["text"])
        if c["category"]:
            st.caption(f"カテゴリ: {c['category']}")
        if c.get("reason") and c["judgement"] != "非該当":
            st.caption(f"💭 {c['reason']}")
        if key_name == "グレーゾーン":
            st.caption("この投稿の振り分け(次回以降、似た文言は自動で同じ判断になります)")
            col1, col2 = st.columns(2)
            if col1.button("🙅 見たくない", key=f"want_{c['comment_key']}"):
                st.session_state.hidden_comment_ids.add(c["comment_key"])
                save_feedback_entry(c["text"], "見たくない", c["category"])
                get_hybrid_classifier.clear()
                st.rerun()
            if col2.button("✅ 問題ない", key=f"ok_{c['comment_key']}", type="primary"):
                st.session_state.ok_comment_ids.add(c["comment_key"])
                save_feedback_entry(c["text"], "問題ない", c["category"])
                get_hybrid_classifier.clear()
                st.rerun()
            st.divider()

        # --- YouTube上での操作(非表示・返信) すべてのコメントに表示 ---
        comment_id = c.get("comment_id")
        if comment_id:
            st.caption("YouTube上の操作")
            yt_hidden = comment_id in st.session_state.youtube_hidden_comment_ids
            yt_replied = comment_id in st.session_state.youtube_replied_comment_ids

            hide_col, status_col = st.columns([1, 2])
            if yt_hidden:
                status_col.caption("✅ YouTube上で非表示済み(保留中・取消可)")
            else:
                if hide_col.button("🚫 YouTube上で非表示にする", key=f"ythide_{c['comment_key']}"):
                    try:
                        hide_comment_on_youtube(st.session_state.credentials, comment_id)
                        st.session_state.youtube_hidden_comment_ids.add(comment_id)
                        st.success("YouTube上で非表示にしました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"非表示にできませんでした: {e}")

            with st.expander("💬 返信する" + ("(返信済み)" if yt_replied else "")):
                reply_text = st.text_area("返信内容", key=f"replytext_{c['comment_key']}")
                if st.button("返信を投稿する", key=f"replybtn_{c['comment_key']}", type="primary"):
                    if reply_text.strip():
                        try:
                            reply_to_comment_on_youtube(st.session_state.credentials, comment_id, reply_text)
                            st.session_state.youtube_replied_comment_ids.add(comment_id)
                            st.success("返信を投稿しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"返信を投稿できませんでした: {e}")
                    else:
                        st.warning("返信内容を入力してください")


def render_grouped_comments(items: list[dict], key_name: str, key_prefix: str) -> None:
    """コメント一覧を、カテゴリ(5分類＋肯定的なコメント)のピル型フィルターで絞り込んで表示する。"""
    if not items:
        st.write("このタブにコメントはありません")
        return

    buckets: dict[str, list] = {}
    for c in items:
        label = c["category"] if c.get("category") else "非該当"
        buckets.setdefault(label, []).append(c)

    ordered_labels = [cat for cat in CATEGORIES if cat in buckets]
    if "非該当" in buckets:
        ordered_labels.append("非該当")
    for label in buckets:
        if label not in ordered_labels:
            ordered_labels.append(label)

    pill_options = ["すべて"] + ordered_labels
    selected_label = st.pills(
        "カテゴリ", pill_options, default="すべて",
        format_func=lambda k: "すべて" if k == "すべて" else display_category_label(k),
        key=f"catfilter_{key_prefix}_{key_name}",
    )
    if selected_label is None:
        selected_label = "すべて"

    labels_to_show = ordered_labels if selected_label == "すべて" else [selected_label]

    for label in labels_to_show:
        group = buckets[label]
        heading = f"💬 {display_category_label(label)}" if label == "非該当" else label
        st.markdown(f"**{heading}**（{len(group)}件）")
        for c in group:
            render_comment_card(c, key_name)
        st.write("")


# ============ セッション状態の初期化 ============
if "step" not in st.session_state:
    st.session_state.step = "landing"
if "selected_categories" not in st.session_state:
    st.session_state.selected_categories = []
if "credentials" not in st.session_state:
    st.session_state.credentials = None
if "hidden_comment_ids" not in st.session_state:
    st.session_state.hidden_comment_ids = set()
if "ok_comment_ids" not in st.session_state:
    st.session_state.ok_comment_ids = set()
if "channel_id" not in st.session_state:
    st.session_state.channel_id = None
if "uploads_playlist_id" not in st.session_state:
    st.session_state.uploads_playlist_id = None
if "videos" not in st.session_state:
    st.session_state.videos = []
if "videos_next_page_token" not in st.session_state:
    st.session_state.videos_next_page_token = None
if "selected_video_ids" not in st.session_state:
    st.session_state.selected_video_ids = set()
if "video_search_query" not in st.session_state:
    st.session_state.video_search_query = ""
if "video_search_results" not in st.session_state:
    st.session_state.video_search_results = None
if "results_by_video" not in st.session_state:
    st.session_state.results_by_video = {}
if "youtube_hidden_comment_ids" not in st.session_state:
    st.session_state.youtube_hidden_comment_ids = set()
if "youtube_replied_comment_ids" not in st.session_state:
    st.session_state.youtube_replied_comment_ids = set()
if "revealed_comment_keys" not in st.session_state:
    st.session_state.revealed_comment_keys = set()
if "analysis_selected_video_ids" not in st.session_state:
    st.session_state.analysis_selected_video_ids = set()
if "analysis_results_by_video" not in st.session_state:
    st.session_state.analysis_results_by_video = {}
if "action_suggestions" not in st.session_state:
    st.session_state.action_suggestions = None

st.title("レグナレ")
st.caption("コメント欄 — Regnare")

# ============ OAuthコールバック処理 ============
query_params = st.query_params
if "code" in query_params and st.session_state.credentials is None:
    # stateパラメータに乗せておいたcode_verifier/selected_categoriesを先に復元する
    restored = {}
    if "state" in query_params:
        try:
            parsed = json.loads(query_params["state"])
            if isinstance(parsed, dict):
                restored = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    flow = get_flow()
    flow.code_verifier = restored.get("code_verifier") or st.session_state.get("code_verifier")

    try:
        flow.fetch_token(code=query_params["code"])
    except Exception:
        st.error(
            "Googleとの連携に失敗しました。認証コードの有効期限切れ、または二重送信の可能性があります。"
            "お手数ですが、最初からもう一度ログインをお試しください。"
        )
        st.query_params.clear()
        if st.button("最初からやり直す", use_container_width=True):
            st.session_state.step = "connect"
            st.rerun()
        st.stop()

    st.session_state.credentials = flow.credentials

    if isinstance(restored.get("selected_categories"), list):
        st.session_state.selected_categories = restored["selected_categories"]

    st.query_params.clear()
    st.session_state.step = "inbox"
    st.rerun()

# ============ STEP -1: ランディングページ ============
if st.session_state.step == "landing":
    st.markdown(
        '<div style="text-align:center; padding: 2.5rem 0 0.5rem;">'
        '<div style="font-size:3rem;">🛡️</div>'
        '<h1 style="font-size:2.6rem; margin:0.3rem 0; letter-spacing:-0.02em;">Regskip</h1>'
        '<p style="font-size:1.15rem; color:#5B6B6A; max-width:480px; margin:0 auto; line-height:1.6;">'
        "コメント欄を、もっと安心できる場所に。<br>見なくて済むから、傷つかない。"
        "</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.write("")
    cols = st.columns(3)
    highlights = [
        ("🙅", "見たくないコメントの\n種類は自分で選べる"),
        ("🤖", "AIが自動で\n通常・グレーゾーン・見たくないに振り分け"),
        ("🔒", "勝手に削除・投稿しない、\nデータも保存しない"),
    ]
    for col, (icon, text) in zip(cols, highlights):
        with col:
            st.markdown(
                f'<div style="text-align:center;font-size:1.8rem;">{icon}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="text-align:center;color:#5B6B6A;font-size:0.9rem;'
                f'white-space:pre-line;">{text}</p>',
                unsafe_allow_html=True,
            )

    st.write("")
    if st.button("はじめる →", use_container_width=True, type="primary"):
        st.session_state.step = "intro"
        st.rerun()

# ============ STEP 0: はじめに ============
elif st.session_state.step == "intro":
    st.subheader("はじめに")

    st.markdown("##### 🛡️ これは何のためのアプリか")
    st.write("YouTubeのアンチコメントで傷つく前に、見なくて済む仕組みを作るWebサイトです。")

    st.markdown("##### 📋 これから何が起きるか")
    st.write("この後、以下の3ステップで進みます(合計1〜2分ほどです)。それぞれで「何が起きて、何が分かるか」をご紹介します。")

    intro_steps = [
        (
            "①", "🙅", "見たくないコメントの種類を選ぶ",
            "外見・人間性・活動クオリティ・モラル/マナー説教・プライバシーの5種類の中から、"
            "自分が見たくないものを選びます。見たくないコメントは人によって違うので、ご自身で判断して選んでください。",
            "→ できること: この設定は後からいつでも変更できます。「人間性」への攻撃は"
            "心理的ダメージが特に大きいことが分析で確認されているため、選択を強くおすすめしています。",
        ),
        (
            "②", "🔑", "Googleでログインする(YouTube連携)",
            "ご自身のYouTubeチャンネルと連携し、実際のコメントを読み取れるようにします。",
            "→ 安心材料: 連携してもコメントを削除したり、勝手に何かを投稿したりすることは一切ありません(詳しくは次の項目)。",
        ),
        (
            "③", "📥", "コメントが自動で振り分けられる",
            "選んだ動画のコメントをAIが判定し、「通常」「グレーゾーン」「見たくない」の3つに自動で仕分けます。",
            "→ わかること: 見たくないカテゴリのコメントは本文が伏せられ、あなたが「見る」を選ぶまで表示されません。"
            "さらに、動画分析タブでは傾向のグラフや、AIによる改善提案も見られます。",
        ),
    ]
    for num, icon, title, body, outcome in intro_steps:
        with st.container(border=True):
            st.markdown(f"**{icon} {num} {title}**")
            st.write(body)
            st.caption(outcome)

    st.markdown("##### 🔒 データの扱いについて")
    st.write("大切にしている「勝手なことはしない」という設計方針を、はじめにお伝えしておきます。")
    with st.container(border=True):
        st.write("・コメントを勝手に削除することはありません(YouTube上での非表示操作は、あなたがボタンを押した時だけ実行されます)")
        st.write("・見たくないコメントは、あなたが「見る」を選ぶまで本文を表示しません")
        st.write("・データは保存されません。取得したコメントや判定結果は、ブラウザのタブを閉じると消えます")

    st.markdown("##### 🔑 何に同意することになるか")
    st.write(
        "Googleでログインすると、あなたのチャンネルのコメント欄にアクセスする許可を求められます。"
        "これは実際にコメントを取得・判定するために必要な連携です。"
        "「YouTube上で非表示にする」「返信を投稿する」といった書き込み操作もできますが、"
        "これらは各コメントのボタンをあなたが押した時だけ実行され、それ以外で勝手に投稿・削除されることはありません。"
    )
    st.info(
        "その際「このアプリはGoogleで確認されていません」という警告画面が表示されますが、"
        "審査前のテスト段階であるための一般的な表示です。"
        "驚かれるかもしれませんが、そういうものだと事前に知っておいていただければと思います。"
        "「詳細」→「(アプリ名)に移動」と進んでいただければ問題ありません。"
    )

    if st.button("始める →", use_container_width=True, type="primary"):
        st.session_state.step = "category"
        st.rerun()

# ============ STEP 1: 見たくないカテゴリを選ぶ ============
elif st.session_state.step == "category":
    render_step_indicator("category")
    st.subheader("STEP 1 / 2  見たくないカテゴリを選ぶ")
    st.write(
        "見たくないコメントの種類は人によって違います。ご自身の判断で、"
        "非表示にしたいカテゴリを選んでください。あとから設定は変更できます。"
    )

    none_selected = st.checkbox(
        "見たくないカテゴリは設定しない(すべて通常タブに表示する)",
        value=False,
        key="cat_none",
    )

    category_descriptions = {
        "外見": "容姿・服装・体型など、見た目に関するコメント",
        "活動クオリティ": "編集・企画内容・トーク力など、活動の質に関するコメント",
        "モラル・マナー説教": "マナー違反やコンプライアンスなどを指摘・説教するコメント",
        "プライバシー": "住所・本名・職場など、個人が特定されうる情報に触れるコメント",
    }

    selected = []
    if not none_selected:
        for cat in CATEGORIES:
            color = CATEGORY_COLORS.get(cat, "#5B6B6A")
            with st.container(border=True):
                st.markdown(
                    f'<span style="display:inline-block;width:10px;height:10px;'
                    f'border-radius:50%;background:{color};margin-right:6px;"></span>'
                    f'<strong style="color:{color};">{cat}</strong>',
                    unsafe_allow_html=True,
                )
                checked = st.checkbox(cat, value=False, key=f"cat_{cat}", label_visibility="collapsed")
                if cat == "人間性":
                    st.caption("人格や性格を否定するコメント")
                    st.markdown(
                        "⚠️ 人格を否定するコメントは、心理的ダメージへの"
                        "**極めて強い影響が統計的に確認されています(p<.001)**。"
                    )
                    st.caption("選択することを強くおすすめします。")
                else:
                    st.caption(category_descriptions[cat])
            if checked:
                selected.append(cat)
    st.session_state.selected_categories = selected

    if st.button("この設定で連携する →", use_container_width=True, type="primary"):
        st.session_state.step = "connect" if "人間性" in selected else "confirm_humanity"
        st.rerun()

# ============ STEP 1.5: 「人間性」を選ばなかった場合の再確認 ============
elif st.session_state.step == "confirm_humanity":
    render_step_indicator("category")
    st.subheader("本当に大丈夫ですか？")
    st.warning(
        "「人間性」を見たくない設定にしていません。人格を否定するコメントは、"
        "傷つきや怒りといった心理的ダメージへの極めて強い影響が統計的に確認されています(p<.001)。"
        "見逃すと、気づかないうちに深く傷ついてしまう可能性があります。"
    )

    col1, col2 = st.columns(2)
    if col1.button("設定を選び直す", use_container_width=True):
        st.session_state.step = "category"
        st.rerun()
    if col2.button("このまま連携する →", use_container_width=True, type="primary"):
        st.session_state.step = "connect"
        st.rerun()

# ============ STEP 2: YouTube連携 ============
elif st.session_state.step == "connect":
    render_step_indicator("connect")
    st.subheader("STEP 2 / 2  YouTubeと連携")
    st.write("下のボタンから、ご自身のYouTubeアカウントでログインしてください。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        # code_verifierもstateに乗せて渡す。session_stateはOAuthリダイレクトで
        # リセットされることがあるため、session_state頼みだとPKCE検証が失敗し
        # InvalidGrantErrorになるケースがあった。
        state=json.dumps({
            "selected_categories": st.session_state.selected_categories,
            "code_verifier": flow.code_verifier,
        }),
    )
    st.session_state.code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True, type="primary")

    with st.expander("この確認画面について"):
        st.write(
            "「このアプリはGoogleで確認されていません」という画面が表示されることがありますが、"
            "審査前の段階であるための一般的な表示です。ご自身のチャンネル以外の情報にはアクセスしませんので、"
            "ご安心のうえ「詳細」→「(アプリ名)に移動」と進んでください。"
        )

# ============ STEP 4: コメント欄 ============
elif st.session_state.step == "inbox":
    st.subheader("コメント欄")

    # --- チャンネル情報の初回取得 ---
    if st.session_state.uploads_playlist_id is None:
        with st.spinner("あなたのチャンネル情報を確認しています…"):
            info = get_channel_info(st.session_state.credentials)
            if info is None:
                st.error("チャンネル情報を取得できませんでした。ログインしたアカウントにYouTubeチャンネルがあるか確認してください。")
                st.stop()
            st.session_state.channel_id = info["channel_id"]
            st.session_state.uploads_playlist_id = info["uploads_playlist_id"]

    # --- 動画一覧の初回取得(最新10件)。この時点でまだ取得済みかどうかを覚えておく ---
    videos_already_loaded = bool(st.session_state.videos)
    if not videos_already_loaded:
        with st.spinner("最新の動画一覧を読み込んでいます…"):
            videos, next_token = list_channel_videos(
                st.session_state.credentials, st.session_state.uploads_playlist_id
            )
            st.session_state.videos = videos
            st.session_state.videos_next_page_token = next_token
            st.session_state.selected_video_ids = {
                v["video_id"] for v in videos[:DEFAULT_SELECTED_VIDEOS]
            }

    # 初回(=まだ動画一覧を取得していなかった)かつ、まだ一度も判定していない場合は
    # ボタンを押さずに自動でコメント取得・判定まで実行する
    auto_run = (not videos_already_loaded) and not st.session_state.results_by_video

    main_tab2, main_tab1 = st.tabs(
        ["📊 動画分析", "📥 コメント欄(振り分け)"]
    )

    with main_tab2:
        st.info("こちらは判定・振り分けとは完全に独立した分析専用のコメント取得です。選んだ動画のコメントを分析します。")

        analysis_display_videos = st.session_state.videos
        col_aa, col_bb = st.columns(2)
        if col_aa.button("表示中をすべて選択", key="analysis_select_all", use_container_width=True):
            for v in analysis_display_videos:
                st.session_state.analysis_selected_video_ids.add(v["video_id"])
            st.rerun()
        if col_bb.button("表示中の選択を解除", key="analysis_deselect_all", use_container_width=True):
            for v in analysis_display_videos:
                st.session_state.analysis_selected_video_ids.discard(v["video_id"])
            st.rerun()

        for v in analysis_display_videos:
            row = st.columns([1, 4])
            with row[0]:
                if v["thumbnail"]:
                    st.image(v["thumbnail"])
            with row[1]:
                checked = st.checkbox(
                    f"{v['title']}  \n:gray[{v['published_at'][:10]}]",
                    value=v["video_id"] in st.session_state.analysis_selected_video_ids,
                    key=f"analysis_vid_{v['video_id']}",
                )
                if checked:
                    st.session_state.analysis_selected_video_ids.add(v["video_id"])
                else:
                    st.session_state.analysis_selected_video_ids.discard(v["video_id"])

        analysis_selected_count = len(st.session_state.analysis_selected_video_ids)
        st.caption(f"分析対象として選択中: {analysis_selected_count} / 最大{MAX_VIDEOS_PER_RUN}本")
        analysis_disabled = (
            analysis_selected_count == 0 or analysis_selected_count > MAX_VIDEOS_PER_RUN
        )

        if st.button(
            "分析用にコメントを取得する", key="analysis_fetch_button",
            use_container_width=True, disabled=analysis_disabled, type="primary",
        ):
            loaded_by_id = {v["video_id"]: v for v in st.session_state.videos}
            analysis_target_videos = [
                loaded_by_id[vid] for vid in st.session_state.analysis_selected_video_ids
                if vid in loaded_by_id
            ]
            progress_bar = st.progress(0)
            status_text = st.empty()
            analysis_results = {}
            analysis_skipped = []
            total = len(analysis_target_videos)
            for idx, v in enumerate(analysis_target_videos, start=1):
                status_text.text(f"{idx}/{total} 本目の動画を分析用に取得中... 「{v['title']}」")
                try:
                    comments = fetch_comments(
                        st.session_state.credentials, v["video_id"], max_results=MAX_COMMENTS_PER_VIDEO
                    )
                except (HttpError, OSError):
                    analysis_skipped.append(v["title"])
                    progress_bar.progress(idx / total)
                    continue
                results = classify_comments_parallel([c["text"] for c in comments], status_text=status_text)
                classified = [{**c, **result} for c, result in zip(comments, results)]
                analysis_results[v["video_id"]] = {"title": v["title"], "classified": classified}
                progress_bar.progress(idx / total)
            status_text.text(f"処理が完了しました({total}本中{len(analysis_results)}本を確認しました)")
            if analysis_skipped:
                st.warning(
                    "以下の動画はコメント欄が無効になっているか取得できなかったためスキップしました: "
                    + "、".join(analysis_skipped)
                )
            st.session_state.analysis_results_by_video = analysis_results
            st.session_state.action_suggestions = None

        if st.session_state.analysis_results_by_video:
            ALL_BUCKETS = CATEGORIES + ["非該当"]
            category_texts = {b: [] for b in ALL_BUCKETS}
            total_by_category = {b: 0 for b in ALL_BUCKETS}
            per_video_category_counts = {}
            for vid, data in st.session_state.analysis_results_by_video.items():
                counts_this_video = {b: 0 for b in ALL_BUCKETS}
                for c in data["classified"]:
                    if c["judgement"] in ("該当", "グレー") and c["category"] in CATEGORIES:
                        bucket = c["category"]
                    elif c["judgement"] == "非該当":
                        bucket = "非該当"
                    else:
                        continue
                    category_texts[bucket].append(c["text"])
                    total_by_category[bucket] += 1
                    counts_this_video[bucket] += 1
                per_video_category_counts[data["title"]] = counts_this_video

            # --- 傾向レポート ---
            st.divider()
            st.markdown("### 📊 傾向レポート")
            st.caption("コメント本文は表示しません。数字の傾向だけを確認できます。")

            st.markdown("#### ① 今回処理した動画のカテゴリ内訳(肯定的なコメント含む)")
            st.caption("選択した動画すべてを合算した内訳です")
            render_category_bar_chart(total_by_category, ALL_BUCKETS)

            if len(per_video_category_counts) >= 2:
                st.markdown("##### 動画ごとに分けて見る")
                for video_title, counts in per_video_category_counts.items():
                    with st.expander(video_title):
                        render_category_bar_chart(counts, ALL_BUCKETS)

            st.divider()
            st.markdown("#### ② 動画ごとの推移")
            if len(per_video_category_counts) >= 2:
                video_order = []
                trend_rows = []
                for video_title, counts in per_video_category_counts.items():
                    short_title = video_title if len(video_title) <= 18 else video_title[:18] + "…"
                    video_order.append(short_title)
                    for b in ALL_BUCKETS:
                        trend_rows.append({
                            "動画": short_title,
                            "full_title": video_title,
                            "カテゴリ": display_category_label(b),
                            "color_key": b,
                            "件数": counts[b],
                        })
                trend_df = pd.DataFrame(trend_rows)
                trend_chart = alt.Chart(trend_df).mark_bar().encode(
                    x=alt.X("動画:N", title=None, sort=video_order, axis=alt.Axis(labelAngle=-30)),
                    y=alt.Y("件数:Q", title="件数"),
                    color=alt.Color(
                        "color_key:N",
                        scale=alt.Scale(domain=list(CATEGORY_COLORS.keys()), range=list(CATEGORY_COLORS.values())),
                        legend=alt.Legend(title=None),
                    ),
                    order=alt.Order("color_key:N"),
                    tooltip=[alt.Tooltip("full_title:N", title="動画"), alt.Tooltip("カテゴリ:N"), alt.Tooltip("件数:Q")],
                ).properties(height=320)
                st.altair_chart(trend_chart, use_container_width=True)
            else:
                st.caption("推移を見るには2本以上の動画を分析してください。")

            # --- 次のアクション(リクエスト・改善提案の抽出) ---
            st.divider()
            st.markdown("### 💡 次のアクション")
            st.caption("コメントの中から「次にしてほしい企画」「建設的な改善提案」だけをAIが抽出します。個人攻撃や単なる感想は無視されます。")

            all_comment_texts = [t for texts in category_texts.values() for t in texts]

            if st.button("次のアクションを抽出する", key="gen_action_suggestions", type="primary"):
                with st.spinner("AIがコメントからリクエスト・改善提案を探しています…"):
                    try:
                        st.session_state.action_suggestions = extract_action_suggestions(all_comment_texts)
                    except Exception as e:
                        st.session_state.action_suggestions = f"抽出に失敗しました: {e}"

            if st.session_state.action_suggestions:
                render_speech_bubble(st.session_state.action_suggestions, avatar="💡")

    with main_tab1:

        with st.expander("🙅 見たくないカテゴリを変更する", expanded=False):
            none_selected_inbox = st.checkbox(
                "見たくないカテゴリは設定しない(すべて通常タブに表示する)",
                value=not st.session_state.selected_categories,
                key="inbox_cat_none",
            )
            new_selected_categories: list[str] = []
            if not none_selected_inbox:
                for cat in CATEGORIES:
                    checked = st.checkbox(
                        cat,
                        value=cat in st.session_state.selected_categories,
                        key=f"inbox_cat_{cat}",
                    )
                    if checked:
                        new_selected_categories.append(cat)
            st.session_state.selected_categories = new_selected_categories

        st.caption(f"見たくない設定中のカテゴリ: {', '.join(st.session_state.selected_categories) or '(なし)'}")

        with st.expander("🔍 対象動画を変更する", expanded=False):
            st.caption("検索・過去動画の読み込み・選び直しができます。")
            search_query = st.text_input(
                "動画タイトルで検索(キーワード絞り込み)", value=st.session_state.video_search_query
            )
            if search_query != st.session_state.video_search_query:
                st.session_state.video_search_query = search_query
                if search_query:
                    with st.spinner("検索しています..."):
                        st.session_state.video_search_results = search_channel_videos(
                            st.session_state.credentials, st.session_state.channel_id, search_query
                        )
                else:
                    st.session_state.video_search_results = None
                st.rerun()

            display_videos = (
                st.session_state.video_search_results
                if st.session_state.video_search_results is not None
                else st.session_state.videos
            )

            col_a, col_b = st.columns(2)
            if col_a.button("表示中をすべて選択", use_container_width=True):
                for v in display_videos:
                    st.session_state.selected_video_ids.add(v["video_id"])
                st.rerun()
            if col_b.button("表示中の選択を解除", use_container_width=True):
                for v in display_videos:
                    st.session_state.selected_video_ids.discard(v["video_id"])
                st.rerun()

            if not display_videos:
                st.write("該当する動画が見つかりませんでした。")

            for v in display_videos:
                row = st.columns([1, 4])
                with row[0]:
                    if v["thumbnail"]:
                        st.image(v["thumbnail"])
                with row[1]:
                    checked = st.checkbox(
                        f"{v['title']}  \n:gray[{v['published_at'][:10]}]",
                        value=v["video_id"] in st.session_state.selected_video_ids,
                        key=f"vid_{v['video_id']}",
                    )
                    if checked:
                        st.session_state.selected_video_ids.add(v["video_id"])
                    else:
                        st.session_state.selected_video_ids.discard(v["video_id"])

            if st.session_state.video_search_results is None and st.session_state.videos_next_page_token:
                load_col1, load_col2 = st.columns(2)
                if load_col1.button("過去の動画をさらに読み込む", use_container_width=True):
                    with st.spinner("読み込んでいます..."):
                        more_videos, next_token = list_channel_videos(
                            st.session_state.credentials,
                            st.session_state.uploads_playlist_id,
                            page_token=st.session_state.videos_next_page_token,
                        )
                        st.session_state.videos.extend(more_videos)
                        st.session_state.videos_next_page_token = next_token
                    st.rerun()
                if load_col2.button("投稿した動画をすべて読み込む", use_container_width=True):
                    status = st.empty()
                    next_token = st.session_state.videos_next_page_token
                    while next_token:
                        status.text(f"読み込み中...({len(st.session_state.videos)}件)")
                        more_videos, next_token = list_channel_videos(
                            st.session_state.credentials,
                            st.session_state.uploads_playlist_id,
                            page_token=next_token,
                        )
                        st.session_state.videos.extend(more_videos)
                        st.session_state.videos_next_page_token = next_token
                    status.empty()
                    st.rerun()

            selected_count = len(st.session_state.selected_video_ids)
            st.caption(f"選択中: {selected_count} / 最大{MAX_VIDEOS_PER_RUN}本")
            if selected_count > MAX_VIDEOS_PER_RUN:
                st.warning(f"一度に処理できる動画は最大{MAX_VIDEOS_PER_RUN}本までです。選択を減らしてください。")

            process_disabled = selected_count == 0 or selected_count > MAX_VIDEOS_PER_RUN
            manual_trigger = st.button(
                "この設定で再取得・判定する", use_container_width=True,
                disabled=process_disabled, type="primary"
            )

        if auto_run or manual_trigger:
            loaded_by_id = {v["video_id"]: v for v in st.session_state.videos}
            if st.session_state.video_search_results:
                for v in st.session_state.video_search_results:
                    loaded_by_id.setdefault(v["video_id"], v)
            target_videos = [
                loaded_by_id[vid] for vid in st.session_state.selected_video_ids if vid in loaded_by_id
            ]

            progress_bar = st.progress(0)
            status_text = st.empty()
            results_by_video = {}
            skipped_videos = []
            total = len(target_videos)
            for idx, v in enumerate(target_videos, start=1):
                status_text.text(f"{idx}/{total} 本目の動画を処理中... 「{v['title']}」")
                try:
                    comments = fetch_comments(
                        st.session_state.credentials, v["video_id"], max_results=MAX_COMMENTS_PER_VIDEO
                    )
                except (HttpError, OSError) as e:
                    # OSErrorはsocket.timeout/ssl.SSLErrorなど、通信が詰まって
                    # タイムアウトした場合を含む(build_youtube_serviceのtimeout設定により発生しうる)
                    skipped_videos.append(v["title"])
                    progress_bar.progress(idx / total)
                    continue
                results = classify_comments_parallel([c["text"] for c in comments], status_text=status_text)
                classified = [
                    {
                        **c, **result,
                        "comment_key": f"{v['video_id']}_{i}_{c['text'][:30]}",
                        "video_id": v["video_id"],
                        "video_title": v["title"],
                    }
                    for i, (c, result) in enumerate(zip(comments, results))
                ]
                results_by_video[v["video_id"]] = {"title": v["title"], "classified": classified}
                progress_bar.progress(idx / total)
            status_text.text(f"処理が完了しました({total}本中{len(results_by_video)}本を確認しました)")
            if skipped_videos:
                st.warning(
                    "以下の動画はコメント欄が無効になっているか取得できなかったためスキップしました: "
                    + "、".join(skipped_videos)
                )
            st.session_state.results_by_video = results_by_video
            st.session_state.hidden_comment_ids = set()
            st.session_state.ok_comment_ids = set()
            st.session_state.pop("growth_summary", None)

        # --- 判定結果の表示 ---
        if st.session_state.results_by_video:
            selected_categories = st.session_state.selected_categories
            hidden_ids = st.session_state.hidden_comment_ids
            ok_ids = st.session_state.ok_comment_ids

            def route_comment(c):
                key = c["comment_key"]
                if key in hidden_ids:
                    return "見たくない"
                if key in ok_ids:
                    return "通常"
                if c["judgement"] == "グレー":
                    return "グレーゾーン"
                if c["judgement"] == "該当" and c["category"] in selected_categories:
                    return "見たくない"
                return "通常"

            total_counts = {"通常": 0, "グレーゾーン": 0, "見たくない": 0}
            per_video_tabs = {}
            for vid, data in st.session_state.results_by_video.items():
                tabs = {"通常": [], "グレーゾーン": [], "見たくない": []}
                for c in data["classified"]:
                    bucket = route_comment(c)
                    tabs[bucket].append(c)
                    total_counts[bucket] += 1
                per_video_tabs[vid] = tabs

            st.divider()
            st.markdown("### 全体サマリー")
            s1, s2, s3 = st.columns(3)
            s1.metric("✅ 通常", total_counts["通常"])
            s2.metric("⏳ グレーゾーン", total_counts["グレーゾーン"])
            s3.metric("🙈 見たくない", total_counts["見たくない"])

            st.markdown("#### 動画ごとの件数内訳")
            for vid, data in st.session_state.results_by_video.items():
                t = per_video_tabs[vid]
                st.write(
                    f"**{data['title']}** — "
                    f"✅ 通常:{len(t['通常'])} / ⏳ グレーゾーン:{len(t['グレーゾーン'])} / 🙈 見たくない:{len(t['見たくない'])}"
                )

            st.divider()
            st.markdown("### 動画ごとの詳細")
            for vid, data in st.session_state.results_by_video.items():
                tabs = per_video_tabs[vid]
                with st.expander(data["title"]):
                    tab1, tab2, tab3 = st.tabs([
                        f"✅ 通常 ({len(tabs['通常'])})",
                        f"⏳ グレーゾーン ({len(tabs['グレーゾーン'])})",
                        f"🙈 見たくない ({len(tabs['見たくない'])})",
                    ])
                    for tab, key_name in zip([tab1, tab2, tab3], ["通常", "グレーゾーン", "見たくない"]):
                        with tab:
                            render_grouped_comments(tabs[key_name], key_name, key_prefix=vid)

