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
from collections import Counter

import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
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


# ============ 判定モジュール(Gemini Embedding) ============
@st.cache_resource
def load_training_data():
    with open("training_examples.json", "r", encoding="utf-8") as f:
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


@st.cache_resource
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

    if best_similarity >= THRESHOLD_MATCH:
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
        client_config, scopes=SCOPES, redirect_uri=st.secrets["REDIRECT_URI"]
    )


def fetch_comments(credentials, video_id: str, max_results: int = 20) -> list[dict]:
    service = build("youtube", "v3", credentials=credentials)
    comments = []
    request = service.commentThreads().list(
        part="snippet", videoId=video_id, maxResults=min(max_results, 100), textFormat="plainText"
    )
    response = request.execute()
    for item in response.get("items", []):
        snippet = item["snippet"]["topLevelComment"]["snippet"]
        comments.append({
            "author": snippet["authorDisplayName"],
            "text": snippet["textDisplay"],
        })
    return comments[:max_results]


# ============ セッション状態の初期化 ============
if "step" not in st.session_state:
    st.session_state.step = "diagnosis"
if "selected_categories" not in st.session_state:
    st.session_state.selected_categories = []
if "credentials" not in st.session_state:
    st.session_state.credentials = None

st.title("🛡️ レグナレ")
st.caption("安全な受信トレイ — Regnare")

# ============ OAuthコールバック処理 ============
query_params = st.query_params
if "code" in query_params and st.session_state.credentials is None:
    flow = get_flow()
    flow.code_verifier = st.session_state.get("code_verifier")
    flow.fetch_token(code=query_params["code"])
    st.session_state.credentials = flow.credentials
    st.query_params.clear()
    st.session_state.step = "inbox"
    st.rerun()

# ============ STEP 1: 診断 ============
if st.session_state.step == "diagnosis":
    st.subheader("STEP 1 / 3　発信スタイル診断")
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
    st.subheader("STEP 2 / 3　診断結果")
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
            f"{cat}" + ("　🟢 おすすめ" if default else ""),
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
    st.subheader("STEP 3 / 3　YouTubeと連携")
    st.write("下のボタンから、ご自身のYouTubeアカウントでログインしてください。")
    st.info("「このアプリはGoogleで確認されていません」という警告が出ますが、テスト段階のアプリのため正常な表示です。「続行」を押して進めてください。")

    flow = get_flow()
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    st.session_state.code_verifier = flow.code_verifier
    st.link_button("Googleでログインして連携する", auth_url, use_container_width=True)

# ============ STEP 4: 安全受信トレイ ============
elif st.session_state.step == "inbox":
    st.subheader("安全受信トレイ")

    video_id = st.text_input("確認したい動画のID(またはURL末尾)を入力してください")

    if video_id and st.button("コメントを取得・判定する", use_container_width=True):
        with st.spinner("コメントを取得し、判定しています..."):
            comments = fetch_comments(st.session_state.credentials, video_id, max_results=20)
            classified = []
            for c in comments:
                result = classify_comment(c["text"])
                classified.append({**c, **result})
            st.session_state.classified_comments = classified

    if "classified_comments" in st.session_state:
        classified = st.session_state.classified_comments
        selected_categories = st.session_state.selected_categories

        tabs = {"通常": [], "気になる": [], "確認待ち": []}
        for c in classified:
            if c["judgement"] == "グレー":
                tabs["確認待ち"].append(c)
            elif c["judgement"] == "該当" and c["category"] in selected_categories:
                tabs["気になる"].append(c)
            else:
                tabs["通常"].append(c)

        tab1, tab2, tab3 = st.tabs([
            f"通常 ({len(tabs['通常'])})",
            f"気になる ({len(tabs['気になる'])})",
            f"確認待ち ({len(tabs['確認待ち'])})",
        ])

        for tab, key in zip([tab1, tab2, tab3], ["通常", "気になる", "確認待ち"]):
            with tab:
                if not tabs[key]:
                    st.write("このタブにコメントはありません")
                for c in tabs[key]:
                    with st.container(border=True):
                        st.write(f"**{c['author']}**")
                        st.write(c["text"])
                        if c["category"]:
                            st.caption(f"カテゴリ: {c['category']} / 類似度: {c['similarity']:.2f}")
                        if key == "確認待ち":
                            col1, col2 = st.columns(2)
                            col1.button("見たくない", key=f"want_{c['text'][:20]}")
                            col2.button("問題ない", key=f"ok_{c['text'][:20]}")
