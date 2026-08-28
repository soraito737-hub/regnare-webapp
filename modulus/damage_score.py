"""
damage_score.py

悪質被害リスクの診断ロジック。
7つの質問（1〜5点）から、ロジスティック回帰モデルでリスク確率を算出し、
初期提案カテゴリを返す。

確認済みモデル:
  intercept = -4.329
  exposure_beta = 0.471
  assertion_beta = 1.216
  style_change_beta = 0.436
"""

from dataclasses import dataclass
import math


INTERCEPT = -4.329
EXPOSURE_BETA = 0.471
ASSERTION_BETA = 1.216
STYLE_CHANGE_BETA = 0.436


@dataclass
class SurveyAnswers:
    face_exposure: int          # 顔出し・容姿服装が分かる投稿をしているか (1-5)
    private_disclosure: int     # 本名・年齢・日常などプライベート情報の開示 (1-5)
    living_area: int            # 居住地域や活動エリアが推測できるか (1-5)
    criticism: int              # 他者への批判的・否定的な意見発信 (1-5)
    harsh_language: int         # 強い言葉（毒舌・皮肉・煽り）を使うか (1-5)
    strong_opinion: int         # 賛否分かれるテーマで主張を強く出すか (1-5)
    style_change: int           # 活動の方向性・キャラクターを大きく変える予定 (1-5)


@dataclass
class DiagnosisResult:
    risk_level: str                    # "高" / "中" / "低"
    probability: float                 # 0.0-1.0
    dominant_factor_label: str         # 一番影響している要因の説明
    disclaimer: str                    # 免責文言
    suggested_categories: list[str]    # 初期提案カテゴリ


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def diagnose(answers: SurveyAnswers) -> DiagnosisResult:
    exposure = (answers.face_exposure + answers.private_disclosure + answers.living_area) / 3
    assertion = (answers.criticism + answers.harsh_language + answers.strong_opinion) / 3
    style_change = answers.style_change

    z = (
        INTERCEPT
        + EXPOSURE_BETA * exposure
        + ASSERTION_BETA * assertion
        + STYLE_CHANGE_BETA * style_change
    )
    probability = _sigmoid(z)

    if probability >= 0.6:
        risk_level = "高"
    elif probability >= 0.3:
        risk_level = "中"
    else:
        risk_level = "低"

    factors = {
        "exposure": EXPOSURE_BETA * exposure,
        "assertion": ASSERTION_BETA * assertion,
        "style_change": STYLE_CHANGE_BETA * style_change,
    }
    dominant = max(factors, key=factors.get)

    dominant_labels = {
        "exposure": "個人情報の露出度が、リスクを高める最も大きな要因です。",
        "assertion": "発信の強さ（批判・強い言葉・強い主張）が、リスクを高める最も大きな要因です。",
        "style_change": "活動方針の変化予定が、リスクを高める最も大きな要因です。",
    }
    dominant_factor_label = dominant_labels[dominant]

    disclaimer = (
        "この診断は簡易的な統計モデルによる目安であり、実際の被害を保証・予測するものではありません。"
    )

    suggested_categories: list[str] = []
    if answers.face_exposure >= 4 or answers.private_disclosure >= 4:
        suggested_categories.append("外見")
        suggested_categories.append("人間性")
    if answers.harsh_language >= 4 or answers.strong_opinion >= 4:
        suggested_categories.append("モラル・マナー説教")
        suggested_categories.append("嫉妬型")
    if answers.style_change >= 4:
        suggested_categories.append("活動クオリティ")

    seen = set()
    deduped = []
    for c in suggested_categories:
        if c not in seen:
            deduped.append(c)
            seen.add(c)
    suggested_categories = deduped or ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "嫉妬型"]

    return DiagnosisResult(
        risk_level=risk_level,
        probability=probability,
        dominant_factor_label=dominant_factor_label,
        disclaimer=disclaimer,
        suggested_categories=suggested_categories,
    )