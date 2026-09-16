"""
Regskip Streamlit版(redesign) — パーソナライズ判定レイヤー対応の新UI
======================================================================
regskip_実装仕様_for_claude_code.md のUI仕様(画面構成)に対応する、
新しい画面遷移の実装。既存の webapp/streamlit_app.py(本番稼働中)とは
別ファイルとして用意し、redesignブランチでのみ動作を検証する。

画面遷移: ①ログイン → ②診断(準備中) → ③初期設定 → ④ホーム(動画一覧)
          → ⑤ローディング → ⑥コメント画面
"""

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
    PatternSetting, PersonalAction, PersonalProfile, PersonalSimilarityList,
    find_flagged_authors, is_emergency, resolve_display_action,
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


# ============ セッション状態の初期化 ============
_defaults = {
    "rd_step": "connect",
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
    st.session_state.rd_step = "diagnosis_placeholder"
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


# ============ ① ログイン(接続前) ============
if st.session_state.rd_step == "connect":
    st.title("Regskip")
    st.write("YouTubeアカウントと連携して始めます。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent", access_type="offline",
        state=json.dumps({"code_verifier": flow.code_verifier}),
    )
    st.session_state.rd_code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True, type="primary")

# ============ ② 診断画面(準備中プレースホルダー) ============
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

# ============ ③ 初期設定画面(PersonalProfileの手動編集UI) ============
elif st.session_state.rd_step == "initial_settings":
    render_header(show_back=(bool(st.session_state.rd_channel_id)))
    st.subheader("初期設定")
    st.caption("攻撃的な言い方をされた時、どう扱ってほしいかをカテゴリごとに選んでください。あとからいつでも変更できます。")

    profile = get_profile()
    action_labels = {
        PersonalAction.NORMAL: "通常表示",
        PersonalAction.HIDE_REGSKIP: "見たくない",
        PersonalAction.HIDE_YOUTUBE: "非表示にしたい",
    }
    action_by_label = {v: k for k, v in action_labels.items()}

    for category in Category:
        if category == Category.NONE:
            continue
        current = profile.get_attack_action(category)
        choice = st.radio(
            category.value,
            options=list(action_labels.values()),
            index=list(action_labels.keys()).index(current),
            key=f"rd_attack_action_{category.value}",
            horizontal=True,
        )
        profile.attack_action[category] = action_by_label[choice]

    st.divider()
    if st.button("この設定で始める", use_container_width=True, type="primary"):
        with st.spinner("チャンネル情報を確認しています…"):
            info = get_channel_info(st.session_state.rd_credentials)
        if info is None:
            st.error("チャンネル情報を取得できませんでした。YouTubeチャンネルがあるアカウントでログインしているか確認してください。")
            st.stop()
        st.session_state.rd_channel_id = info["channel_id"]
        st.session_state.rd_uploads_playlist_id = info["uploads_playlist_id"]
        profile.user_id = info["channel_id"]
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

# ============ ⑤ ローディング画面 / ⑥ コメント画面は次の実装区切りで対応 ============
elif st.session_state.rd_step in ("loading", "comments"):
    render_header(show_back=True)
    st.info("⑤ローディング画面・⑥コメント画面は次の実装区切りで対応予定です。")
    video = st.session_state.rd_selected_video
    if video:
        st.write(f"選択中の動画: {video['title']}")
