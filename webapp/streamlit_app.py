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

import html
import json
import math
import re
import sys
from pathlib import Path
from collections import Counter

import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google import genai
from google.genai import types
import pandas as pd
import altair as alt

# modulus/ 配下の判定モジュール(LLMベース)をimportできるようにする
sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))
from hybrid_classifier import HybridClassifier

# ============ 設定 ============
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

_current_step = st.session_state.get("step", "intro")
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
    ("diagnosis", "① 診断"),
    ("result", "② 診断結果"),
    ("category", "③ カテゴリ選択"),
    ("connect", "④ 連携"),
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


_RISK_BADGE_COLORS = {"低": "#3F8F63", "注意": "#C98A1B", "やや高め": "#C98A1B", "高": "#C2483D"}


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
    "プライバシー": "#c1443c",
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
        "name": "プライベート発信型",
        "description": "生活環境やプライベートな情報を開示し、ファンと距離の近いコミュニケーションを取るスタイルです。",
        "centroid": {"exposure": 0.0, "private": 1.0, "assertion": -0.5},
        "risk_level": "やや高め",
        "cluster_id": 1,
        # 調査の解釈: 執着・ガチ恋・実害ハラスメント(文脈の歪曲/反応を面白がられる/
        # 暴力的コメント/まとめサイト晒し/妨害行為)のリスクが最も懸念されるタイプ
        "harm_top3_keys": ["curated_site", "distortion", "sabotage"],
        "tendency_text": (
            "「距離の近さ」があなたの発信の魅力である一方、私生活の領域まで踏み込まれやすい傾向があります。"
        ),
        "phase_note": (
            "単発の批判コメントよりも、つきまとい的な言動や実害化といった"
            "「二次被害」へ発展しやすいタイプです。早めの対策をおすすめします。"
        ),
        "categories": ["人間性", "プライバシー"],
    },
    "light": {
        "name": "ナチュラル発信型",
        "description": "露出を最小限に抑え、過激な主張も控える安全第一の運用スタイルです。",
        "centroid": {"exposure": -1.0, "private": -0.6, "assertion": -0.3},
        "risk_level": "低",
        "cluster_id": 2,
        # 調査の解釈: 最もアンチ攻撃に遭いにくいタイプ
        "reassurance_text": "アンケート調査の中でも、被害の水準がもっとも低いグループです。",
        "simulation_text": (
            "もし今後、顔出しや容姿の露出を増やしていくと「ビジュアル発信型」の傾向に近づく可能性があります。"
        ),
        "caution_text": "「被害が少ない」だけで「被害がゼロ」というわけではない点にはご注意ください。",
        "categories": [],
    },
    "visual": {
        "name": "ビジュアル発信型",
        "description": "見た目(ルックスや衣装)をメインコンテンツにしつつ、プライベート開示や過激な発言は控えるスタイルです。",
        "centroid": {"exposure": 0.85, "private": -0.1, "assertion": -0.5},
        "risk_level": "注意",
        "cluster_id": 3,
        # 調査の解釈: 外見批判に集中的に狙われやすいタイプ
        "harm_top3_keys": ["appearance"],
        "tendency_text": "見た目をコンテンツの核にしているからこそ、そこが攻撃対象になりやすい構造です。",
        "distinction_text": (
            "このタイプで多いのは「見た目」への言及であり、「人格」そのものを否定されているわけではありません。"
            "混同せず切り分けて受け止めることも大切です。"
        ),
        "categories": ["外見"],
    },
    "assertive": {
        "name": "ストレート発信型",
        "description": "自分の主張や、特定の話題・人物への切り込みを武器にするスタイルです。",
        "centroid": {"exposure": 0.2, "private": -0.1, "assertion": 1.2},
        "risk_level": "高",
        "cluster_id": 4,
        # 調査の解釈: 人格否定コメントや炎上・集団叩きのリスクが最も高いタイプ
        "harm_top3_keys": ["humanity", "violent", "enjoying"],
        "tendency_text": (
            "意見表明を武器にしている分、意見そのものより「あなた自身」への攻撃に転化しやすい傾向があります。"
        ),
        "severity_text": "4タイプの中で、被害の深刻度がもっとも高い傾向にあります。事前に心の準備をしておくと安心です。",
        "categories": ["活動クオリティ", "モラル・マナー説教"],
    },
}

PERSONA_ORDER = ["private_fan", "light", "visual", "assertive"]

# 被害タイプ別のクラスター平均値(アンケート「グループ統計量」より、n=213)
HARM_ITEM_STATS = {
    "appearance": {"label": "外見に関する批判コメント", "means": {1: 2.00, 2: 1.22, 3: 2.03, 4: 2.43}},
    "humanity": {"label": "人格を否定するようなコメント", "means": {1: 1.78, 2: 1.69, 3: 1.83, 4: 2.71}},
    "distortion": {"label": "発言の文脈を歪められる", "means": {1: 1.31, 2: 1.24, 3: 1.31, 4: 1.88}},
    "enjoying": {"label": "炎上・反応を面白がられる", "means": {1: 1.42, 2: 1.28, 3: 1.41, 4: 2.12}},
    "violent": {"label": "暴力的・攻撃的なコメント", "means": {1: 1.69, 2: 1.57, 3: 1.56, 4: 2.16}},
    "curated_site": {"label": "まとめサイト等への晒し", "means": {1: 1.42, 2: 1.22, 3: 1.42, 4: 1.94}},
    "sabotage": {"label": "活動の妨害行為", "means": {1: 1.19, 2: 1.06, 3: 1.25, 4: 1.63}},
}


def render_harm_comparison_chart(item_keys: list[str]) -> None:
    """指定した被害項目について、4タイプの平均値を並べた棒グラフを表示する(表示専用)。"""
    rows = {}
    for key in item_keys:
        item = HARM_ITEM_STATS[key]
        rows[item["label"]] = {
            PERSONA_PROFILES[p]["name"]: item["means"][PERSONA_PROFILES[p]["cluster_id"]]
            for p in PERSONA_ORDER
        }
    st.bar_chart(pd.DataFrame(rows).T, stack=False)
    st.caption("数値はアンケート回答の平均スコアで、高いほど該当する被害が多い傾向を示します。")


def render_persona_harm_detail(persona_key: str) -> None:
    """診断結果のタイプごとに、想定される被害の傾向を詳しく表示する(表示専用)。"""
    persona = PERSONA_PROFILES[persona_key]

    if persona_key == "light":
        st.write(persona["reassurance_text"])
        st.info(persona["simulation_text"])
        st.caption(persona["caution_text"])
        return

    if persona_key == "visual":
        item = HARM_ITEM_STATS["appearance"]
        score = item["means"][persona["cluster_id"]]
        baseline = item["means"][PERSONA_PROFILES["light"]["cluster_id"]]
        ratio = score / baseline
        st.metric(item["label"], f"{score:.1f} / 4点中", f"控えめ層の約{ratio:.1f}倍", delta_color="off")
        st.caption("その他の項目は、他タイプと比べて平均的〜低めの水準です。")
        st.write(persona["tendency_text"])
        st.info(persona["distinction_text"])
        return

    st.write("来やすい被害の傾向トップ3")
    for key in persona["harm_top3_keys"]:
        st.write(f"- {HARM_ITEM_STATS[key]['label']}")
    render_harm_comparison_chart(persona["harm_top3_keys"])
    st.write(persona["tendency_text"])

    if persona_key == "private_fan":
        st.warning(persona["phase_note"])
    elif persona_key == "assertive":
        st.warning(persona["severity_text"])


def render_diagnosis_summary(result: dict) -> None:
    """診断結果(タイプ名・リスクバッジ・被害確率・被害傾向)をまとめて表示する(表示専用)。"""
    st.markdown(f"### あなたのタイプ：{result['persona_name']}")
    st.markdown(f"リスクレベル: {render_risk_badge(result['risk_level'])}", unsafe_allow_html=True)
    st.write(result["persona_description"])

    st.metric("悪質被害リスク(統計モデルによる推定確率)", f"{result['severe_harm_probability'] * 100:.1f}%")
    st.caption(
        "※ アンケート調査(n=213)の2項ロジスティック回帰分析モデルによる推定値です。"
        "「活動スタイルの変化」は現在の質問に含まれないため、変化なし(最も安全な状態)と仮定して計算しています。"
        "大きな路線変更を予定している場合、実際のリスクはこれより高い可能性があります。"
    )

    with st.container(border=True):
        render_persona_harm_detail(result["persona_key"])

    st.caption("※ タイプ分類は独自アンケート調査(n=213)のクラスター分析に基づく傾向の目安であり、将来を確定的に予測するものではありません。")


def _normalize_answer(value: int, max_value: int) -> float:
    """1〜max_valueの回答を-1〜+1のスケールに変換する。"""
    return (value - 1) / (max_value - 1) * 2 - 1


def _rescale_to_4(value: int, max_value: int) -> float:
    """1〜max_valueの回答を、回帰モデルが前提とする1〜4のスケールに線形換算する。"""
    if max_value == 4:
        return float(value)
    return 1 + (value - 1) / (max_value - 1) * 3


# ============ 悪質被害スコア(2項ロジスティック回帰モデル) ============
# アンケート分析(n=213)の2項ロジスティック回帰分析(目的変数: Severe_Damage_Dummy)による実測モデル。
SEVERE_HARM_INTERCEPT = -4.329
SEVERE_HARM_BETA_EXPOSURE = 0.471
SEVERE_HARM_BETA_ASSERTION = 1.216
SEVERE_HARM_BETA_CHANGE = 0.436
# 「活動スタイルの変化」は現在の質問セットに含まれないため、最も安全な値(変化なし)で固定する
SEVERE_HARM_CHANGE_BASELINE = 1


def compute_severe_harm_probability(answers: dict) -> float:
    """2項ロジスティック回帰モデルにより、深刻な被害を受ける確率を推定する。
    Exposure_Score/Assertion_Scoreは4段階評価を前提とした係数のため、
    3択の質問(opinion/criticism/harsh)は1〜4相当に線形換算して用いる。"""
    exposure_score = (answers["exposure"] + answers["private"]) / 2
    assertion_score = (
        _rescale_to_4(answers["opinion"], 3)
        + _rescale_to_4(answers["criticism"], 3)
        + _rescale_to_4(answers["harsh"], 3)
    ) / 3
    logit = (
        SEVERE_HARM_INTERCEPT
        + SEVERE_HARM_BETA_EXPOSURE * exposure_score
        + SEVERE_HARM_BETA_ASSERTION * assertion_score
        + SEVERE_HARM_BETA_CHANGE * SEVERE_HARM_CHANGE_BASELINE
    )
    return 1 / (1 + math.exp(-logit))


def build_diagnosis_result(persona_key: str, severe_harm_probability: float) -> dict:
    """persona_keyと被害確率から診断結果の辞書を組み立てる(OAuthリダイレクト後の復元にも使う)。"""
    persona = PERSONA_PROFILES[persona_key]
    return {
        "persona_key": persona_key,
        "persona_name": persona["name"],
        "persona_description": persona["description"],
        "risk_level": persona["risk_level"],
        "suggested_categories": persona["categories"],
        "severe_harm_probability": severe_harm_probability,
    }


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
    severe_harm_probability = compute_severe_harm_probability(answers)
    return build_diagnosis_result(persona_key, severe_harm_probability)

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


def classify_comment(text: str) -> dict:
    result = get_hybrid_classifier().classify(text)
    return {
        "category": result.category,
        "judgement": result.judgement,
        "reason": result.reason,
        "source": result.source,
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

def render_comment_card(c: dict, key_name: str) -> None:
    """コメント1件をカードとして表示する(見たくないタブの伏せ字表示、確認待ちの判断ボタン、
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
    st.session_state.step = "intro"
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

st.title("🛡️ レグナレ")
st.caption("コメント欄 — Regnare")

# ============ OAuthコールバック処理 ============
query_params = st.query_params
if "code" in query_params and st.session_state.credentials is None:
    # OAuthリダイレクトでsession_stateがリセットされるケースに備え、
    # stateパラメータに乗せておいたcode_verifier/selected_categories/診断結果を先に復元する
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
    persona_key = restored.get("persona_key")
    probability = restored.get("severe_harm_probability")
    if persona_key in PERSONA_PROFILES and isinstance(probability, (int, float)):
        st.session_state.diagnosis_result = build_diagnosis_result(persona_key, probability)

    st.query_params.clear()
    st.session_state.step = "inbox"
    st.rerun()

# ============ STEP 0: はじめに ============
if st.session_state.step == "intro":
    st.subheader("はじめに")

    st.markdown("##### 🛡️ これは何のためのアプリか")
    st.write("YouTubeのアンチコメントによる「傷つき」を、事前に回避するための安全な受信トレイです。")

    st.markdown("##### 📋 これから何が起きるか")
    st.write("この後、以下の4ステップで進みます(合計2〜3分ほどです)。")
    st.write("① いくつかの質問に答える(1分)")
    st.write("② 見たくないコメントの種類を選ぶ")
    st.write("③ Googleでログインする(YouTube連携)")
    st.write("④ 実際のコメントが自動で振り分けられる")

    st.markdown("##### 🔒 データの扱いについて")
    with st.container(border=True):
        st.write("・コメントを勝手に削除することはありません")
        st.write("・見たくないコメントは、あなたが確認するまで本文を表示しません")
        st.write("・データは保存されず、ブラウザを閉じると消えます")

    st.markdown("##### 🔑 何に同意することになるか")
    st.write(
        "Googleでログインすると、あなたのチャンネルのコメント欄にアクセスする許可を求められます。"
        "これは実際にコメントを取得・判定するために必要な連携です。"
    )
    st.info(
        "その際「このアプリはGoogleで確認されていません」という警告画面が表示されますが、"
        "審査前のテスト段階であるための一般的な表示です。"
        "驚かれるかもしれませんが、そういうものだと事前に知っておいていただければと思います。"
    )

    if st.button("始める →", use_container_width=True, type="primary"):
        st.session_state.step = "diagnosis"
        st.rerun()

# ============ STEP 1: 診断 ============
if st.session_state.step == "diagnosis":
    render_step_indicator("diagnosis")
    st.subheader("STEP 1 / 4  発信スタイル診断")
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
        st.session_state.step = "result"
        st.rerun()

# ============ STEP 2: 診断結果 ============
elif st.session_state.step == "result":
    render_step_indicator("result")
    st.subheader("STEP 2 / 4  診断結果")
    result = st.session_state.diagnosis_result
    render_diagnosis_summary(result)

    if st.button("次へ：見たくないカテゴリを選ぶ →", use_container_width=True, type="primary"):
        st.session_state.step = "category"
        st.rerun()

# ============ STEP 3: カテゴリ提案 ============
elif st.session_state.step == "category":
    render_step_indicator("category")
    st.subheader("STEP 3 / 4  見たくないカテゴリを選ぶ")
    result = st.session_state.diagnosis_result

    st.write("診断結果をもとに、見たくないカテゴリの候補にチェックを入れています。内容を確認し、必要に応じて調整してください。")

    none_selected = st.checkbox(
        "見たくないカテゴリは設定しない(すべて通常タブに表示する)",
        value=False,
        key="cat_none",
    )

    selected = []
    if not none_selected:
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

# ============ STEP 4: YouTube連携 ============
elif st.session_state.step == "connect":
    render_step_indicator("connect")
    st.subheader("STEP 4 / 4  YouTubeと連携")
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
            "persona_key": st.session_state.diagnosis_result["persona_key"],
            "severe_harm_probability": st.session_state.diagnosis_result["severe_harm_probability"],
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

    main_tab3, main_tab2, main_tab1 = st.tabs(
        ["🧭 診断結果", "📊 動画分析", "📥 コメント欄(振り分け)"]
    )

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
                            render_grouped_comments(tabs[key_name], key_name, key_prefix=vid)

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

    with main_tab3:
        result = st.session_state.get("diagnosis_result")
        if result:
            render_diagnosis_summary(result)
        else:
            st.info("診断結果が見つかりませんでした。お手数ですが、最初から診断をやり直してください。")
