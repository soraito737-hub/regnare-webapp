"""
レグナレ Streamlit版
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

import json
import re
from pathlib import Path
from collections import Counter

import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google import genai
from google.genai import types
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# ============ 設定 ============
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

st.set_page_config(page_title="レグナレ", page_icon="🛡️", layout="centered")

CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "嫉妬型"]
THRESHOLD_MATCH = 0.60
THRESHOLD_GRAY = 0.45

# ============ 動画選択・APIクォータ設定 ============
VIDEOS_PAGE_SIZE = 10          # 動画一覧の1ページあたり取得件数
DEFAULT_SELECTED_VIDEOS = 5    # デフォルトでチェックを入れる最新動画数
MAX_VIDEOS_PER_RUN = 5         # 一度に処理できる動画数の上限
MAX_COMMENTS_PER_VIDEO = 200   # 1動画あたりのコメント取得上限

# ============ 悪質被害スコア計算 ============
INTERCEPT = -4.329
BETA_EXPOSURE = 0.471
BETA_ASSERTION = 1.216
BETA_CHANGE = 0.436

CATEGORY_MAPPING = {
    "assertion": ["活動クオリティ", "モラル・マナー説教"],
    "exposure": ["外見"],
    "change": ["人間性", "嫉妬型"],
}
FACTOR_LABELS = {
    "assertion": "主張・攻撃系の発信がリスク要因になりやすい傾向です",
    "exposure": "露出系(顔出し・プライベート開示)の要素がリスク要因になりやすい傾向です",
    "change": "活動スタイルの変化がリスク要因になりやすい傾向です",
}

def diagnose(answers: dict) -> dict:
    exposure_score = (answers["face"] + answers["private"] + answers["area"]) / 3
    assertion_score = (answers["criticism"] + answers["harsh"] + answers["opinion"]) / 3
    change_score = answers["change"]

    logit = (
        INTERCEPT
        + BETA_EXPOSURE * exposure_score
        + BETA_ASSERTION * assertion_score
        + BETA_CHANGE * change_score
    )
    probability = 1 / (1 + np.exp(-logit))

    if probability < 0.25:
        risk_level = "低"
    elif probability < 0.5:
        risk_level = "注意"
    elif probability < 0.75:
        risk_level = "やや高め"
    else:
        risk_level = "高"

    contributions = {
        "assertion": BETA_ASSERTION * assertion_score,
        "exposure": BETA_EXPOSURE * exposure_score,
        "change": BETA_CHANGE * change_score,
    }
    dominant = max(contributions, key=contributions.get)

    return {
        "probability": probability,
        "risk_level": risk_level,
        "dominant_label": FACTOR_LABELS[dominant],
        "suggested_categories": CATEGORY_MAPPING[dominant],
    }

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

# ============ 判定モジュール(Gemini Embedding) ============
def load_training_data():
    path = Path(__file__).parent / "training_examples.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

@st.cache_data(show_spinner=False)
def embed_text(text: str) -> list[float]:
    client = get_gemini_client()
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
    )
    return result.embeddings[0].values

def build_category_vectors():
    training = load_training_data()
    vectors = {}
    for category, examples in training.items():
        vectors[category] = [embed_text(ex) for ex in examples]
    return vectors

def classify_comment(text: str) -> dict:
    training = load_training_data()
    category_vectors = build_category_vectors()
    comment_vector = embed_text(text)
    best_category, best_similarity, best_example = None, -1.0, None
    for category, vectors in category_vectors.items():
        sims = cosine_similarity([comment_vector], vectors)[0]
        idx = sims.argmax()
        if sims[idx] > best_similarity:
            best_similarity = sims[idx]
            best_category = category
            best_example = training[category][idx]

    if best_category == "非該当":
        judgement = "非該当"
    elif best_similarity >= THRESHOLD_MATCH:
        judgement = "該当"
    elif best_similarity >= THRESHOLD_GRAY:
        judgement = "グレー"
    else:
        judgement = "非該当"
    return {
        "category": best_category,
        "similarity": float(best_similarity),
        "judgement": judgement,
        "matched_example": best_example,
    }

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

def fetch_comments(credentials, video_id: str, max_results: int = 20) -> list[dict]:
    service = build("youtube", "v3", credentials=credentials)
    comments = []
    page_token = None
    while len(comments) < max_results:
        request = service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=min(max_results - len(comments), 100),
            textFormat="plainText",
            pageToken=page_token,
        )
        response = request.execute()
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
    service = build("youtube", "v3", credentials=credentials)
    service.comments().setModerationStatus(id=comment_id, moderationStatus="heldForReview").execute()


def reply_to_comment_on_youtube(credentials, comment_id: str, reply_text: str) -> None:
    """指定コメントに返信を投稿する。"""
    service = build("youtube", "v3", credentials=credentials)
    service.comments().insert(
        part="snippet",
        body={"snippet": {"parentId": comment_id, "textOriginal": reply_text}},
    ).execute()


def get_channel_info(credentials) -> dict | None:
    """ログインユーザー自身のチャンネルID・アップロード済み動画プレイリストIDを取得"""
    service = build("youtube", "v3", credentials=credentials)
    resp = service.channels().list(part="contentDetails", mine=True).execute()
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
    service = build("youtube", "v3", credentials=credentials)
    resp = service.playlistItems().list(
        part="snippet",
        playlistId=playlist_id,
        maxResults=max_results,
        pageToken=page_token,
    ).execute()
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
    service = build("youtube", "v3", credentials=credentials)
    resp = service.search().list(
        part="snippet",
        channelId=channel_id,
        q=query,
        type="video",
        order="date",
        maxResults=max_results,
    ).execute()
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

# ============ セッション状態の初期化 ============
if "step" not in st.session_state:
    st.session_state.step = "diagnosis"
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

st.title("🛡️ レグナレ")
st.caption("安全な受信トレイ — Regnare")

# ============ OAuthコールバック処理 ============
query_params = st.query_params
if "code" in query_params and st.session_state.credentials is None:
    flow = get_flow()
    flow.code_verifier = st.session_state.get("code_verifier")
    flow.fetch_token(code=query_params["code"])
    st.session_state.credentials = flow.credentials

    # OAuthリダイレクトでsession_stateがリセットされるケースに備え、
    # stateパラメータに乗せておいたselected_categoriesを復元する
    if "state" in query_params:
        try:
            restored = json.loads(query_params["state"])
            if isinstance(restored, list):
                st.session_state.selected_categories = restored
        except (json.JSONDecodeError, TypeError):
            pass

    st.query_params.clear()
    st.session_state.step = "inbox"
    st.rerun()

# ============ STEP 1: 診断 ============
if st.session_state.step == "diagnosis":
    st.subheader("STEP 1 / 3  発信スタイル診断")
    st.write("7つの質問に、1(あてはまらない)〜5(よくあてはまる)で答えてください。")

    with st.form("diagnosis_form"):
        face = st.slider("顔出しでの配信や、容姿・服装がはっきり分かる投稿をしているか", 1, 5, 3)
        private = st.slider("本名・年齢・日常の出来事など、プライベートな情報を開示しているか", 1, 5, 3)
        area = st.slider("居住地域や活動エリアが推測できる情報を出しているか", 1, 5, 3)
        criticism = st.slider("他者の行動や話題に対し、批判的・否定的な意見を発信することがあるか", 1, 5, 3)
        harsh = st.slider("あえて強い言葉(毒舌・皮肉・煽り)を使うスタイルをとっているか", 1, 5, 3)
        opinion = st.slider("賛否が分かれるテーマについて、自分の主張を強く打ち出しているか", 1, 5, 3)
        change = st.slider("最近、活動の方向性・キャラクターを大きく変える予定があるか", 1, 5, 3)

        submitted = st.form_submit_button("診断結果を見る →", use_container_width=True)

    if submitted:
        answers = {
            "face": face, "private": private, "area": area,
            "criticism": criticism, "harsh": harsh, "opinion": opinion, "change": change,
        }
        st.session_state.diagnosis_result = diagnose(answers)
        st.session_state.selected_categories = list(
            st.session_state.diagnosis_result["suggested_categories"]
        )
        st.session_state.step = "category"
        st.rerun()

# ============ STEP 2: カテゴリ提案 ============
elif st.session_state.step == "category":
    st.subheader("STEP 2 / 3  診断結果")
    result = st.session_state.diagnosis_result

    risk_colors = {"低": "green", "注意": "orange", "やや高め": "orange", "高": "red"}
    st.metric("悪質被害リスク", result["risk_level"], f"{result['probability']*100:.1f}%")
    st.write(result["dominant_label"])
    st.caption("※ 本スコアは統計モデルに基づく傾向の目安であり、将来を確定的に予測するものではありません。")

    st.subheader("見たくないカテゴリを選ぶ")
    st.write("診断結果から、以下を初期提案としてチェックしています。調整して確定してください。")

    selected = []
    for cat in CATEGORIES:
        default = cat in result["suggested_categories"]
        checked = st.checkbox(
            f"{cat}" + (" 🟢 おすすめ" if default else ""),
            value=default,
            key=f"cat_{cat}",
        )
        if checked:
            selected.append(cat)
    st.session_state.selected_categories = selected

    if st.button("この設定で連携する →", use_container_width=True):
        st.session_state.step = "connect"
        st.rerun()

# ============ STEP 2.5: YouTube連携 ============
elif st.session_state.step == "connect":
    st.subheader("STEP 3 / 3  YouTubeと連携")
    st.write("下のボタンから、ご自身のYouTubeアカウントでログインしてください。")
    st.info("「このアプリはGoogleで確認されていません」という警告が出ますが、テスト段階のアプリのため正常な表示です。「続行」を押して進めてください。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        state=json.dumps(st.session_state.selected_categories),
    )
    st.session_state.code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True)

# ============ STEP 4: 安全受信トレイ ============
elif st.session_state.step == "inbox":
    st.subheader("安全受信トレイ")
    st.caption(f"見たくない設定中のカテゴリ: {', '.join(st.session_state.selected_categories) or '(未選択)'}")

    # --- チャンネル情報の初回取得 ---
    if st.session_state.uploads_playlist_id is None:
        with st.spinner("チャンネル情報を取得しています..."):
            info = get_channel_info(st.session_state.credentials)
            if info is None:
                st.error("チャンネル情報を取得できませんでした。ログインしたアカウントにYouTubeチャンネルがあるか確認してください。")
                st.stop()
            st.session_state.channel_id = info["channel_id"]
            st.session_state.uploads_playlist_id = info["uploads_playlist_id"]

    # --- 動画一覧の初回取得(最新10件)。この時点でまだ取得済みかどうかを覚えておく ---
    videos_already_loaded = bool(st.session_state.videos)
    if not videos_already_loaded:
        with st.spinner("動画一覧を取得しています..."):
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

    with st.expander("対象動画を変更する(検索・過去動画の読み込み・選び直し)", expanded=False):
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
            if st.button("過去の動画をさらに読み込む", use_container_width=True):
                with st.spinner("読み込んでいます..."):
                    more_videos, next_token = list_channel_videos(
                        st.session_state.credentials,
                        st.session_state.uploads_playlist_id,
                        page_token=st.session_state.videos_next_page_token,
                    )
                    st.session_state.videos.extend(more_videos)
                    st.session_state.videos_next_page_token = next_token
                st.rerun()

        selected_count = len(st.session_state.selected_video_ids)
        st.caption(f"選択中: {selected_count} / 最大{MAX_VIDEOS_PER_RUN}本")
        if selected_count > MAX_VIDEOS_PER_RUN:
            st.warning(f"一度に処理できる動画は最大{MAX_VIDEOS_PER_RUN}本までです。選択を減らしてください。")

        process_disabled = selected_count == 0 or selected_count > MAX_VIDEOS_PER_RUN
        manual_trigger = st.button(
            "この設定で再取得・判定する", use_container_width=True, disabled=process_disabled
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
            except HttpError as e:
                skipped_videos.append(v["title"])
                progress_bar.progress(idx / total)
                continue
            classified = []
            for i, c in enumerate(comments):
                result = classify_comment(c["text"])
                classified.append({
                    **c, **result,
                    "comment_key": f"{v['video_id']}_{i}_{c['text'][:30]}",
                    "video_id": v["video_id"],
                    "video_title": v["title"],
                })
            results_by_video[v["video_id"]] = {"title": v["title"], "classified": classified}
            progress_bar.progress(idx / total)
        status_text.text(f"完了しました({total}本中{len(results_by_video)}本処理)")
        if skipped_videos:
            st.warning(
                "以下の動画はコメント欄が無効になっているか取得できなかったためスキップしました: "
                + "、".join(skipped_videos)
            )
        st.session_state.results_by_video = results_by_video
        st.session_state.hidden_comment_ids = set()
        st.session_state.ok_comment_ids = set()

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
                return "確認待ち"
            if c["judgement"] == "該当" and c["category"] in selected_categories:
                return "見たくない"
            return "通常"

        total_counts = {"通常": 0, "確認待ち": 0, "見たくない": 0}
        per_video_tabs = {}
        for vid, data in st.session_state.results_by_video.items():
            tabs = {"通常": [], "確認待ち": [], "見たくない": []}
            for c in data["classified"]:
                bucket = route_comment(c)
                tabs[bucket].append(c)
                total_counts[bucket] += 1
            per_video_tabs[vid] = tabs

        st.markdown("### 全体サマリー")
        s1, s2, s3 = st.columns(3)
        s1.metric("通常", total_counts["通常"])
        s2.metric("確認待ち", total_counts["確認待ち"])
        s3.metric("見たくない", total_counts["見たくない"])

        st.markdown("#### 動画ごとの件数内訳")
        for vid, data in st.session_state.results_by_video.items():
            t = per_video_tabs[vid]
            st.write(
                f"**{data['title']}** — "
                f"通常:{len(t['通常'])} / 確認待ち:{len(t['確認待ち'])} / 見たくない:{len(t['見たくない'])}"
            )

        st.markdown("### 動画ごとの詳細")
        for vid, data in st.session_state.results_by_video.items():
            tabs = per_video_tabs[vid]
            with st.expander(data["title"]):
                tab1, tab2, tab3 = st.tabs([
                    f"通常 ({len(tabs['通常'])})",
                    f"確認待ち ({len(tabs['確認待ち'])})",
                    f"見たくない ({len(tabs['見たくない'])})",
                ])
                for tab, key_name in zip([tab1, tab2, tab3], ["通常", "確認待ち", "見たくない"]):
                    with tab:
                        if not tabs[key_name]:
                            st.write("このタブにコメントはありません")
                        for c in tabs[key_name]:
                            with st.container(border=True):
                                st.write(f"**{c['author']}**")
                                if key_name == "見たくない":
                                    with st.expander("▶ コメント本文を表示する"):
                                        st.warning(
                                            "本当に表示してよろしいでしょうか。"
                                            "見ずに非表示にすることをおすすめします。"
                                        )
                                        st.write(c["text"])
                                else:
                                    st.write(c["text"])
                                if c["category"]:
                                    st.caption(f"カテゴリ: {c['category']} / 類似度: {c['similarity']:.2f}")
                                if key_name == "確認待ち":
                                    col1, col2 = st.columns(2)
                                    if col1.button("見たくない", key=f"want_{c['comment_key']}"):
                                        st.session_state.hidden_comment_ids.add(c["comment_key"])
                                        st.rerun()
                                    if col2.button("問題ない", key=f"ok_{c['comment_key']}"):
                                        st.session_state.ok_comment_ids.add(c["comment_key"])
                                        st.rerun()

                                # --- YouTube上での操作(非表示・返信) すべてのコメントに表示 ---
                                comment_id = c.get("comment_id")
                                if comment_id:
                                    yt_hidden = comment_id in st.session_state.youtube_hidden_comment_ids
                                    yt_replied = comment_id in st.session_state.youtube_replied_comment_ids

                                    hide_col, status_col = st.columns([1, 2])
                                    if yt_hidden:
                                        status_col.caption("✅ YouTube上で非表示済み(保留中・取消可)")
                                    else:
                                        if hide_col.button(
                                            "YouTube上で非表示にする", key=f"ythide_{c['comment_key']}"
                                        ):
                                            try:
                                                hide_comment_on_youtube(
                                                    st.session_state.credentials, comment_id
                                                )
                                                st.session_state.youtube_hidden_comment_ids.add(comment_id)
                                                st.success("YouTube上で非表示にしました")
                                                st.rerun()
                                            except Exception as e:
                                                st.error(f"非表示にできませんでした: {e}")

                                    with st.expander("返信する" + ("(返信済み)" if yt_replied else "")):
                                        reply_text = st.text_area(
                                            "返信内容", key=f"replytext_{c['comment_key']}"
                                        )
                                        if st.button("返信を投稿する", key=f"replybtn_{c['comment_key']}"):
                                            if reply_text.strip():
                                                try:
                                                    reply_to_comment_on_youtube(
                                                        st.session_state.credentials, comment_id, reply_text
                                                    )
                                                    st.session_state.youtube_replied_comment_ids.add(comment_id)
                                                    st.success("返信を投稿しました")
                                                    st.rerun()
                                                except Exception as e:
                                                    st.error(f"返信を投稿できませんでした: {e}")
                                            else:
                                                st.warning("返信内容を入力してください")
