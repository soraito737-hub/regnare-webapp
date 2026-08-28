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
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity

# ============ 設定 ============
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

_current_step = st.session_state.get("step", "diagnosis")
st.set_page_config(
    page_title="レグナレ", page_icon="🛡️",
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
</style>
""", unsafe_allow_html=True)

_STEP_ORDER = [("diagnosis", "① 診断"), ("category", "② カテゴリ選択"), ("connect", "③ 連携")]


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


_RISK_BADGE_COLORS = {"低": "#3F8F63", "注意": "#C98A1B", "やや高め": "#C98A1B", "高": "#C2483D"}


def render_risk_badge(level: str) -> str:
    """診断結果のリスクレベルを色付きバッジのHTMLとして返す(表示専用)。"""
    color = _RISK_BADGE_COLORS.get(level, "#5B6B6A")
    return (
        f'<span style="background:{color}22;color:{color};padding:2px 10px;'
        f'border-radius:12px;font-weight:600;">{level}</span>'
    )


CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "嫉妬型"]
THRESHOLD_MATCH = 0.60
THRESHOLD_GRAY = 0.45

# ============ 動画選択・APIクォータ設定 ============
VIDEOS_PAGE_SIZE = 10          # 動画一覧の1ページあたり取得件数
DEFAULT_SELECTED_VIDEOS = 5    # デフォルトでチェックを入れる最新動画数
MAX_VIDEOS_PER_RUN = 5         # 一度に処理できる動画数の上限
MAX_COMMENTS_PER_VIDEO = 200   # 1動画あたりのコメント取得上限

# ============ 発信スタイル診断(アンケートのクラスター分析に基づく) ============
# 独自アンケート調査(n=213)のクラスター分析結果をもとにした発信スタイル診断。
# 4クラスターへの分類は、調査で報告された各クラスターの特徴(高い/低い項目)から
# 近似した重心(-1〜+1に正規化)を用いた最近傍判定。正確なクラスター重心の数値表が
# 得られ次第、PERSONA_PROFILESの"centroid"を差し替えることでより精緻化できる。
DIAGNOSIS_QUESTIONS = [
    {
        "key": "exposure",
        "question": "動画や配信での「顔や容姿」の露出度はどのくらいですか？",
        "options": [
            ("顔や姿は出していない(声のみ、テロップのみ、立ち絵など)", 1),
            ("首から下や手元など、身体の一部のみ見せている", 2),
            ("顔や全体的な姿をしっかり出している", 3),
            ("顔出しに加え、メイクや衣装・ビジュアルを魅せる工夫を意識している", 4),
        ],
    },
    {
        "key": "private",
        "question": "自分の「プライベート(私生活・居住地・購入品など)」の情報をどの程度出していますか？",
        "options": [
            ("プライベートな情報は一切出していない", 1),
            ("部屋の一部や買ったものなど、差し障りのない範囲で時々出している", 2),
            ("自宅の様子、ファッション、私生活の日常を積極的に出している", 3),
            ("最寄り駅周辺、地元の風景、よく行く店など、生活環境がわかる情報まで出している", 4),
        ],
    },
    {
        "key": "opinion",
        "question": "トレンドや特定の話題に対して、「好き・嫌い」「賛成・反対」などの主張をしますか？",
        "options": [
            ("自分の強い意見や好き嫌いは動画内でほとんど言わない", 1),
            ("テーマによっては、自分の意見や賛否を控えめに言うことがある", 2),
            ("自分の「好き・嫌い」「賛成・反対」をはっきりと主張することが多い", 3),
        ],
    },
    {
        "key": "criticism",
        "question": "特定の個人・動画・出来事を取り上げて、批判・ツッコミ・物申すことはありますか？",
        "options": [
            ("他人を批判したり、ツッコミを入れたりする企画は一切しない", 1),
            ("たまにネタや冗談交じりで軽く触れる程度", 2),
            ("あえて特定の話題や人物を取り上げ、批評・批判・ツッコミをすることがよくある", 3),
        ],
    },
    {
        "key": "harsh",
        "question": "動画内で「毒舌」「過激な表現」「エッジの効いた演出」を取り入れていますか？",
        "options": [
            ("トゲのある表現は避け、安全でマイルドな表現を心がけている", 1),
            ("視聴者を飽きさせないよう、たまに少しトゲのある発言や演出を入れる", 2),
            ("毒舌やエッジの効いた演出、煽り要素を自分の強み(武器)として取り入れている", 3),
        ],
    },
]

PERSONA_PROFILES = {
    "private_fan": {
        "name": "プライベート・ファン親密型",
        "description": "生活環境やプライベートな情報を開示し、ファンと距離の近いコミュニケーションを取るスタイルです。",
        "centroid": {"exposure": 0.0, "private": 1.0, "assertion": -0.5},
        "risk_level": "やや高め",
        # 調査の解釈: 執着・ガチ恋・実害ハラスメント(文脈の歪曲/反応を面白がられる/
        # 暴力的コメント/まとめサイト晒し/妨害行為)のリスクが最も懸念されるタイプ
        "harm_highlights": [
            "距離の近さにつけこんだ、しつこい絡み・つきまとい的な言動",
            "「ガチ恋」的な感情のもつれから実害(妨害行為など)につながるリスク",
        ],
        "categories": ["人間性", "嫉妬型"],
    },
    "light": {
        "name": "ライト・控えめ型",
        "description": "露出を最小限に抑え、過激な主張も控える安全第一の運用スタイルです。",
        "centroid": {"exposure": -1.0, "private": -0.6, "assertion": -0.3},
        "risk_level": "低",
        # 調査の解釈: 最もアンチ攻撃に遭いにくいタイプ
        "harm_highlights": [],
        "categories": [],
    },
    "visual": {
        "name": "ビジュアル・顔出し特化型",
        "description": "見た目(ルックスや衣装)をメインコンテンツにしつつ、プライベート開示や過激な発言は控えるスタイルです。",
        "centroid": {"exposure": 0.85, "private": -0.1, "assertion": -0.5},
        "risk_level": "注意",
        # 調査の解釈: 外見批判に集中的に狙われやすいタイプ
        "harm_highlights": ["外見(容姿・服装など)に関する批判コメント"],
        "categories": ["外見"],
    },
    "assertive": {
        "name": "物申す・毒舌・物議醸し型",
        "description": "自分の主張や、特定の話題・人物への切り込みを武器にするスタイルです。",
        "centroid": {"exposure": 0.2, "private": -0.1, "assertion": 1.2},
        "risk_level": "高",
        # 調査の解釈: 人格否定コメントや炎上・集団叩きのリスクが最も高いタイプ
        "harm_highlights": ["人格を否定するようなコメント", "炎上・集団での叩き"],
        "categories": ["活動クオリティ", "モラル・マナー説教"],
    },
}


def _normalize_answer(value: int, max_value: int) -> float:
    """1〜max_valueの回答を-1〜+1のスケールに変換する。"""
    return (value - 1) / (max_value - 1) * 2 - 1


def diagnose(answers: dict) -> dict:
    scores = {
        "exposure": _normalize_answer(answers["exposure"], 4),
        "private": _normalize_answer(answers["private"], 4),
        "assertion": (
            _normalize_answer(answers["opinion"], 3)
            + _normalize_answer(answers["criticism"], 3)
            + _normalize_answer(answers["harsh"], 3)
        ) / 3,
    }

    def distance(centroid: dict) -> float:
        return sum((scores[dim] - val) ** 2 for dim, val in centroid.items()) ** 0.5

    persona_key = min(PERSONA_PROFILES, key=lambda k: distance(PERSONA_PROFILES[k]["centroid"]))
    persona = PERSONA_PROFILES[persona_key]

    return {
        "persona_key": persona_key,
        "persona_name": persona["name"],
        "persona_description": persona["description"],
        "risk_level": persona["risk_level"],
        "harm_highlights": persona["harm_highlights"],
        "suggested_categories": persona["categories"],
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


GENERATION_MODEL = "gemini-3.6-flash"


def summarize_category(category: str, comments_texts: list[str]) -> str:
    """指定カテゴリのコメント群から、動画制作者向けにAIが共通する論点を要約する。
    個々のコメント本文はここでは表示しない(要約結果のみを返す)。"""
    if not comments_texts:
        return "該当するコメントがありませんでした。"
    client = get_gemini_client()
    joined = "\n".join(f"- {t}" for t in comments_texts[:100])
    prompt = (
        f"以下はYouTube動画に寄せられた「{category}」に分類されたコメントです。"
        "動画制作者が状況を把握し、今後の活動に活かせるよう、"
        "共通する論点や傾向を日本語で2〜4個の箇条書きで簡潔に要約してください。"
        "個々のコメントをそのまま引用せず、あくまで傾向として要約してください。\n\n"
        f"{joined}"
    )
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
    )
    return response.text

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
if "revealed_comment_keys" not in st.session_state:
    st.session_state.revealed_comment_keys = set()
if "analysis_selected_video_ids" not in st.session_state:
    st.session_state.analysis_selected_video_ids = set()
if "analysis_results_by_video" not in st.session_state:
    st.session_state.analysis_results_by_video = {}
if "category_summaries" not in st.session_state:
    st.session_state.category_summaries = {}

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
    render_step_indicator("diagnosis")
    st.subheader("STEP 1 / 3  発信スタイル診断")
    st.write("5つの質問にお答えください。所要時間は1分ほどです。")

    with st.form("diagnosis_form"):
        answers_raw = {}

        st.markdown("**見た目・プライベートについて**")
        for q in DIAGNOSIS_QUESTIONS[:2]:
            labels = [opt[0] for opt in q["options"]]
            answers_raw[q["key"]] = st.radio(q["question"], labels, index=0, key=f"q_{q['key']}")

        st.markdown("**発信スタイルについて**")
        for q in DIAGNOSIS_QUESTIONS[2:]:
            labels = [opt[0] for opt in q["options"]]
            answers_raw[q["key"]] = st.radio(q["question"], labels, index=0, key=f"q_{q['key']}")

        submitted = st.form_submit_button("診断結果を見る →", use_container_width=True, type="primary")

    if submitted:
        answers = {}
        for q in DIAGNOSIS_QUESTIONS:
            label_to_score = dict(q["options"])
            answers[q["key"]] = label_to_score[answers_raw[q["key"]]]
        st.session_state.diagnosis_result = diagnose(answers)
        st.session_state.selected_categories = list(
            st.session_state.diagnosis_result["suggested_categories"]
        )
        st.session_state.step = "category"
        st.rerun()

# ============ STEP 2: カテゴリ提案 ============
elif st.session_state.step == "category":
    render_step_indicator("category")
    st.subheader("STEP 2 / 3  診断結果")
    result = st.session_state.diagnosis_result

    st.markdown(f"### あなたのタイプ：{result['persona_name']}")
    st.markdown(f"リスクレベル: {render_risk_badge(result['risk_level'])}", unsafe_allow_html=True)
    st.write(result["persona_description"])
    if result["harm_highlights"]:
        st.write("同じ傾向を持つ配信者では、特に次のような被害が比較的多く報告されています。")
        for h in result["harm_highlights"]:
            st.write(f"- {h}")
    else:
        st.write("アンケート調査上、同じ傾向の配信者は比較的被害の少ない層です。")
    st.caption("※ 本結果は独自アンケート調査(n=213)のクラスター分析に基づく傾向の目安であり、将来を確定的に予測するものではありません。")

    st.divider()
    st.subheader("見たくないカテゴリを選ぶ")
    st.write("診断結果をもとに、見たくないカテゴリの候補にチェックを入れています。内容を確認し、必要に応じて調整してください。")

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

    if st.button("この設定で連携する →", use_container_width=True, type="primary"):
        st.session_state.step = "connect"
        st.rerun()

# ============ STEP 2.5: YouTube連携 ============
elif st.session_state.step == "connect":
    render_step_indicator("connect")
    st.subheader("STEP 3 / 3  YouTubeと連携")
    st.write("下のボタンから、ご自身のYouTubeアカウントでログインしてください。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        state=json.dumps(st.session_state.selected_categories),
    )
    st.session_state.code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True, type="primary")

    with st.expander("この確認画面について"):
        st.write(
            "「このアプリはGoogleで確認されていません」という画面が表示されることがありますが、"
            "審査前の段階であるための一般的な表示です。ご自身のチャンネル以外の情報にはアクセスしませんので、"
            "ご安心のうえ「詳細」→「(アプリ名)に移動」と進んでください。"
        )

# ============ STEP 4: 安全受信トレイ ============
elif st.session_state.step == "inbox":
    st.subheader("安全受信トレイ")
    st.caption(f"見たくない設定中のカテゴリ: {', '.join(st.session_state.selected_categories) or '(未選択)'}")

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

    main_tab1, main_tab2 = st.tabs(["📥 安全受信トレイ(振り分け)", "📊 動画分析(カテゴリ別AI要約)"])

    with main_tab1:

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

            st.divider()
            st.markdown("### 全体サマリー")
            s1, s2, s3 = st.columns(3)
            s1.metric("✅ 通常", total_counts["通常"])
            s2.metric("⏳ 確認待ち", total_counts["確認待ち"])
            s3.metric("🙈 見たくない", total_counts["見たくない"])

            st.markdown("#### 動画ごとの件数内訳")
            for vid, data in st.session_state.results_by_video.items():
                t = per_video_tabs[vid]
                st.write(
                    f"**{data['title']}** — "
                    f"✅ 通常:{len(t['通常'])} / ⏳ 確認待ち:{len(t['確認待ち'])} / 🙈 見たくない:{len(t['見たくない'])}"
                )

            st.divider()
            st.markdown("### 動画ごとの詳細")
            for vid, data in st.session_state.results_by_video.items():
                tabs = per_video_tabs[vid]
                with st.expander(data["title"]):
                    tab1, tab2, tab3 = st.tabs([
                        f"✅ 通常 ({len(tabs['通常'])})",
                        f"⏳ 確認待ち ({len(tabs['確認待ち'])})",
                        f"🙈 見たくない ({len(tabs['見たくない'])})",
                    ])
                    for tab, key_name in zip([tab1, tab2, tab3], ["通常", "確認待ち", "見たくない"]):
                        with tab:
                            if not tabs[key_name]:
                                st.write("このタブにコメントはありません")
                            for c in tabs[key_name]:
                                with st.container(border=True):
                                    st.write(f"**{c['author']}**")
                                    if key_name == "見たくない":
                                        revealed = c["comment_key"] in st.session_state.revealed_comment_keys
                                        if not revealed:
                                            st.warning(
                                                "内容を確認しますか？見ずに非表示にすることもできます。"
                                            )
                                            if st.button(
                                                "👁 はい、本文を表示する", key=f"reveal_{c['comment_key']}",
                                                type="primary",
                                            ):
                                                st.session_state.revealed_comment_keys.add(c["comment_key"])
                                                st.rerun()
                                        else:
                                            st.write(c["text"])
                                            if st.button(
                                                "🙈 本文を隠す", key=f"hide_reveal_{c['comment_key']}"
                                            ):
                                                st.session_state.revealed_comment_keys.discard(c["comment_key"])
                                                st.rerun()
                                    else:
                                        st.write(c["text"])
                                    if c["category"]:
                                        st.caption(f"カテゴリ: {c['category']} / 類似度: {c['similarity']:.2f}")
                                    if key_name == "確認待ち":
                                        col1, col2 = st.columns(2)
                                        if col1.button("🙅 見たくない", key=f"want_{c['comment_key']}"):
                                            st.session_state.hidden_comment_ids.add(c["comment_key"])
                                            st.rerun()
                                        if col2.button("✅ 問題ない", key=f"ok_{c['comment_key']}", type="primary"):
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
                                                "🚫 YouTube上で非表示にする", key=f"ythide_{c['comment_key']}"
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

                                        with st.expander("💬 返信する" + ("(返信済み)" if yt_replied else "")):
                                            reply_text = st.text_area(
                                                "返信内容", key=f"replytext_{c['comment_key']}"
                                            )
                                            if st.button("返信を投稿する", key=f"replybtn_{c['comment_key']}", type="primary"):
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

    with main_tab2:
        st.info(
            "こちらは判定・振り分けとは完全に独立した分析専用のコメント取得です。"
            "選んだ動画のコメントを、5分類ごとにAIが要約します。個別のコメント本文は表示されません。"
        )

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
                except HttpError:
                    analysis_skipped.append(v["title"])
                    progress_bar.progress(idx / total)
                    continue
                classified = []
                for i, c in enumerate(comments):
                    result = classify_comment(c["text"])
                    classified.append({**c, **result})
                analysis_results[v["video_id"]] = {"title": v["title"], "classified": classified}
                progress_bar.progress(idx / total)
            status_text.text(f"処理が完了しました({total}本中{len(analysis_results)}本を確認しました)")
            if analysis_skipped:
                st.warning(
                    "以下の動画はコメント欄が無効になっているか取得できなかったためスキップしました: "
                    + "、".join(analysis_skipped)
                )
            st.session_state.analysis_results_by_video = analysis_results
            st.session_state.category_summaries = {}

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

            total_classified = sum(total_by_category.values()) or 1

            # --- 傾向レポート ---
            st.divider()
            st.markdown("### 📊 傾向レポート")
            st.caption("コメント本文は表示しません。数字の傾向だけを確認できます。")

            st.markdown("#### ① 今回処理した動画のカテゴリ内訳(応援コメント含む)")
            st.caption("選択した動画すべてを合算した内訳です")
            for b in ALL_BUCKETS:
                pct = total_by_category[b] / total_classified * 100
                label = "💬 応援コメント(非該当)" if b == "非該当" else b
                st.write(f"{label}: {pct:.1f}%  ({total_by_category[b]}件)")
                st.progress(min(pct / 100, 1.0))

            if len(per_video_category_counts) >= 2:
                st.markdown("##### 動画ごとに分けて見る")
                for video_title, counts in per_video_category_counts.items():
                    video_total = sum(counts.values()) or 1
                    with st.expander(video_title):
                        for b in ALL_BUCKETS:
                            pct_v = counts[b] / video_total * 100
                            label = "💬 応援コメント(非該当)" if b == "非該当" else b
                            st.write(f"{label}: {pct_v:.1f}%  ({counts[b]}件)")
                            st.progress(min(pct_v / 100, 1.0))

            st.divider()
            st.markdown("#### ② 同規模クリエイターとの比較")
            st.caption(
                "アンケート調査(n=213)における、5分類間の相対的な傾向との比較です。"
                "調査は自己申告の被害頻度(5件法)を基にした相対シェアであり、"
                "実際のコメント件数比率とは単位が異なる点にご留意ください。"
            )
            BENCHMARK_SHARE = {
                "外見": 18.8,
                "人間性": 20.0,
                "活動クオリティ": 21.4,
                "モラル・マナー説教": 19.3,
                "嫉妬型": 20.4,
            }
            anti_total = sum(total_by_category[c] for c in CATEGORIES) or 1
            for cat, bench in BENCHMARK_SHARE.items():
                you_pct = total_by_category[cat] / anti_total * 100
                st.write(f"**{cat}** — あなた: {you_pct:.1f}% / アンケート平均: {bench}%")
                if you_pct > bench + 3:
                    st.caption("⚠️ 平均より高めの傾向です")
                elif you_pct < bench - 3:
                    st.caption("✓ 平均より低めです")
                else:
                    st.caption("✓ 平均並みです")

            st.divider()
            st.markdown("#### ③ 動画ごとの推移")
            if len(per_video_category_counts) >= 2:
                trend_df = pd.DataFrame(per_video_category_counts).T[ALL_BUCKETS]
                st.bar_chart(trend_df)
            else:
                st.caption("推移を見るには2本以上の動画を分析してください。")

            # --- AI要約(見たくない設定のカテゴリは対象外) ---
            st.divider()
            st.markdown("### 🌱 AI要約")
            selected_categories = st.session_state.selected_categories
            visible_buckets = [
                b for b in ALL_BUCKETS if b == "非該当" or b not in selected_categories
            ]
            hidden_buckets = [b for b in ALL_BUCKETS if b not in visible_buckets]
            st.caption(
                "「見たくない」に設定したカテゴリの要約は表示されません。"
                + (f"(非表示中: {'、'.join(hidden_buckets)})" if hidden_buckets else "")
            )

            if st.button("AI要約を生成する", key="gen_category_summaries", type="primary"):
                with st.spinner("AIがコメントの傾向を要約しています…"):
                    summaries = {}
                    for b in visible_buckets:
                        label = "応援コメント" if b == "非該当" else b
                        try:
                            summaries[b] = summarize_category(label, category_texts[b])
                        except Exception as e:
                            summaries[b] = f"要約に失敗しました: {e}"
                    st.session_state.category_summaries = summaries

            if st.session_state.category_summaries:
                for b in visible_buckets:
                    label = "💬 応援コメント" if b == "非該当" else b
                    with st.expander(f"{label}({total_by_category.get(b, 0)}件)"):
                        st.info(st.session_state.category_summaries.get(b, "(未生成)"))
                        if category_texts[b]:
                            with st.expander("実際のコメントを見る"):
                                for t in category_texts[b]:
                                    st.write(f"- {t}")
