"""
LLMベース判定モジュール
=========================
埋め込みの最近傍探索(embedding_classifier.py)と違い、
Geminiに文脈・皮肉・深刻度を考慮した判定を直接行わせる。

【実行方法】
1. export GEMINI_API_KEY="発行したAPIキー" (Windowsは $env:GEMINI_API_KEY="...")
2. python llm_classifier.py
"""

import json
from dataclasses import dataclass
from typing import Literal

from google.genai import types

from gemini_client import make_client

CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "プライバシー"]
Judgement = Literal["該当", "グレー", "非該当"]

DEFAULT_MODEL = "gemini-3.6-flash"

CATEGORY_DEFINITIONS = """\
- 外見: 容姿・体型・服装・メイクなど見た目への否定的な言及
- 人間性: 性格・人格・誠実さなど、人間性そのものへの否定的な決めつけ
- 活動クオリティ: 動画・配信の企画/編集/構成/クオリティへの批判
- モラル・マナー説教: マナー違反や倫理観への説教・非難(内容の正否に関わらず、上から目線の指摘)
- プライバシー: 本名・住所・職場・家族など、個人が特定できる情報の指摘・詮索"""

SYSTEM_PROMPT = f"""あなたはYouTuber/配信者向けのコメント判定アシスタントです。
以下のカテゴリ定義に基づき、与えられたコメント1件について、
「該当」(明確に有害・攻撃的)「グレー」(あいまい・軽微で判断が難しい)「非該当」(問題ない)
のいずれかで判定してください。

重要: 表面上はポジティブ・中立な言葉でも、皮肉や嫌味として使われている場合は
文脈から意図を読み取り「該当」または「グレー」と判定してください。
単に話題が近いだけで悪意のないコメントは「非該当」としてください。

カテゴリ定義:
{CATEGORY_DEFINITIONS}

必ず以下のJSON形式のみで回答してください(説明文やコードブロックは不要):
{{"category": "上記5カテゴリのいずれか、非該当ならnull", "judgement": "該当/グレー/非該当", "reason": "判定理由を1文で"}}"""


@dataclass
class LLMClassificationResult:
    comment: str
    category: str | None
    judgement: Judgement
    reason: str


class LLMCommentClassifier:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.client = make_client(api_key)
        self.model = model

    def classify(self, comment: str) -> LLMClassificationResult:
        prompt = f"{SYSTEM_PROMPT}\n\n判定するコメント:「{comment}」"
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )

        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            # 応答がJSONとして壊れていた場合は安全側(非該当)に倒す
            return LLMClassificationResult(
                comment=comment, category=None, judgement="非該当",
                reason="LLM応答の解析に失敗したため安全側で非該当としました",
            )

        category = data.get("category")
        if category not in CATEGORIES:
            category = None

        judgement = data.get("judgement")
        if judgement not in ("該当", "グレー", "非該当"):
            judgement = "非該当"

        return LLMClassificationResult(
            comment=comment,
            category=category,
            judgement=judgement,
            reason=data.get("reason", ""),
        )

    def classify_batch(self, comments: list[str]) -> list[LLMClassificationResult]:
        return [self.classify(c) for c in comments]


if __name__ == "__main__":
    classifier = LLMCommentClassifier()

    test_comments = [
        "才能なさすぎ、動画作るのやめたら？",
        "さすが人間性豊かですね(笑)",  # 皮肉の例(埋め込みだと安全寄りに誤判定しやすい)
        "今日の動画も面白かったです！",
        "この背景の景色、家の近くだよね特定した",
        "声質が独特で好きです",
    ]

    print("=" * 70)
    print("LLM判定結果")
    print("=" * 70)
    for result in classifier.classify_batch(test_comments):
        print(f"\nコメント: 「{result.comment}」")
        print(f"  判定: {result.judgement}（カテゴリ: {result.category}）")
        print(f"  理由: {result.reason}")
