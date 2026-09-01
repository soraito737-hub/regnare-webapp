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
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from google.genai import errors, types

from gemini_client import make_client

CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "プライバシー"]
Judgement = Literal["該当", "グレー", "非該当"]

DEFAULT_MODEL = "gemini-3.6-flash"

CATEGORY_DEFINITIONS = """\
- 外見: 容姿・体型・服装・メイクなど見た目への否定的な言及
- 人間性: 性格・人格・誠実さなど、人間性そのものへの否定的な決めつけ
- 活動クオリティ: 動画・配信の企画/編集/構成/クオリティへの批判
- モラル・マナー説教: マナー違反や倫理観への説教・非難(内容の正否に関わらず、上から目線の指摘)
- プライバシー: 本名・住所・職場・家族など個人が特定できる情報の指摘・詮索。
  配信内容からの推測(背景・声・私物など)に限らず、「〇〇で見かけた」「前は△△に住んでたよね」
  のような、実生活での目撃情報や過去の言及の蒸し返しも含む"""

# 判定基準のキャリブレーション用few-shot例。特に「該当」と「グレー」の境界線
# (皮肉が明確かどうか、深刻度がどの程度か)をLLMに掴ませるためのもの。
FEW_SHOT_EXAMPLES = """\
該当の例:
- 「才能なさすぎ、動画作るのやめたら？」→ 活動クオリティ/該当(直接的な人格否定を伴う攻撃)
- 「さすが人間性豊かですね(笑)」→ 人間性/該当((笑)を伴う皮肉の意図が明確)
- 「この背景の景色、家の近くだよね特定した」→ プライバシー/該当(住所特定を明言している)
- 「名古屋で見かけたことあるよ、〇〇にいたでしょ」→ プライバシー/該当(実際の目撃情報を晒している)

グレーの例:
- 「今日はちょっと元気なさそうな顔してますね」→ 外見/グレー(体調を気遣う言葉にも読めるが外見への言及でもあり、意図が読み切れない)
- 「対応いつも塩対応だよね」→ 人間性/グレー(軽い指摘で、愛のあるいじりか嫌味か文脈次第)
- 「話し方のイントネーションで出身地方だいたい分かるね」→ プライバシー/グレー(個人情報の推測ではあるが、深刻な特定・脅迫的な意図までは読めない)

非該当の例(紛らわしいが安全なもの):
- 「配信環境整ってきましたね、快適に見れます」→ 非該当(設備の話題で悪意なし)
- 「その考え方、すごく参考になりました」→ 非該当(純粋な肯定的感想)
- 「使ってるカメラは何ですか？」→ 非該当(素朴な質問で詮索の意図はない)
- 「顔出しする勇気がすごいと思います、尊敬します」→ 非該当(「顔出し」という外見関連の単語を含むが、文全体は称賛であり否定的な意図はない)
- 「整形の技術ってすごい進歩してるんですね、勉強になります」→ 非該当(「整形」という単語があっても、一般的な技術トピックへの感想で配信者本人への否定的な言及ではない)
- 「老け顔って言われがちだけど魅力だと思います」→ 非該当(「老け」というネガティブな単語を含むが、文全体としては擁護・肯定している)

重要: 「整形」「老け」「顔出し」「見た目」のような、カテゴリに関連しそうな単語が
含まれているというだけで機械的に判定しないでください。その単語を含む文全体が、
実際に批判・皮肉・詮索の意図を持っているかを必ず確認し、
文脈上あきらかに称賛・擁護・中立的な話題であれば「非該当」としてください。"""

SYSTEM_PROMPT = f"""あなたはYouTuber/配信者向けのコメント判定アシスタントです。
以下のカテゴリ定義に基づき、与えられたコメント1件について、
「該当」(明確に有害・攻撃的)「グレー」(あいまい・軽微で判断が難しい)「非該当」(問題ない)
のいずれかで判定してください。

重要: 表面上はポジティブ・中立な言葉でも、皮肉や嫌味として使われている場合は
文脈から意図を読み取り「該当」または「グレー」と判定してください。
単に話題が近いだけで悪意のないコメントは「非該当」としてください。

カテゴリ定義:
{CATEGORY_DEFINITIONS}

{FEW_SHOT_EXAMPLES}

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

    def classify(self, comment: str, max_retries: int = 5) -> LLMClassificationResult:
        prompt = f"{SYSTEM_PROMPT}\n\n判定するコメント:「{comment}」"

        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    ),
                )
                break
            except errors.ClientError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    # レート制限。指定された待機時間 + 余裕を見て再試行する
                    time.sleep(15 * (attempt + 1))
                    continue
                raise
            except errors.ServerError as e:
                if e.code in (503, 500) and attempt < max_retries - 1:
                    # サーバー側の一時的な混雑。少し待って再試行する
                    time.sleep(10 * (attempt + 1))
                    continue
                raise
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                # 通信レベルの一時的な切断・タイムアウト。少し待って再試行する
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise

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
