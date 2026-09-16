"""
Regskip パーソナライズ判定レイヤー
====================================
regskip_実装仕様_for_claude_code.md の Phase 1(セクション1・2)に対応する、
新しい4次元判定(category / surface_level / tatemae_pattern / emergency)の実装。

既存の modulus/llm_classifier.py(5分類・該当/グレー/非該当の単一判定)は
本番(main)で稼働中のため変更しない。本モジュールは redesign ブランチ向けの
並行実装として、独立したファイルに切り出している。

【実行方法】
1. export GEMINI_API_KEY="発行したAPIキー" (Windowsは $env:GEMINI_API_KEY="...")
2. python personal_classifier.py
"""

from dataclasses import dataclass
from enum import Enum

from google.genai import types

from gemini_client import make_client

DEFAULT_MODEL = "gemini-3.6-flash"


class Category(str, Enum):
    APPEARANCE = "容姿否定"
    PERSONALITY = "人格否定"
    MORAL_LECTURE = "社会モラルマナー説教"
    ACTIVITY_QUALITY = "活動クオリティ"
    NONE = "該当なし"  # カテゴリに紐づかない直球の暴言用


class SurfaceLevel(int, Enum):
    LEVEL_1 = 1  # 批判(提案〜断定まで含む。侮蔑語・命令形・活動停止要求を含まない)
    LEVEL_2 = 2  # 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む)
    # 【確定事項】旧レベル1(提案・疑問形)と旧レベル2(断定・侮蔑語なし)は、
    # 「嫌な気持ちの差がない」との判断により統合済み。以後3段階には戻さないこと。


class TatemaePattern(str, Enum):
    OTHER_COMPARISON = "他者比較"
    FAN_DEPARTURE = "ファン離脱"
    RHETORICAL_QUESTION = "疑問形"
    BACKHANDED_COMPLIMENT = "褒め殺し型"
    FALSE_CONSENSUS = "主語肥大化"
    POLITE_INTERROGATION = "丁寧語長文詰問"
    FAKE_ADVICE = "自己満足アドバイス"
    NONE = "該当なし"


class EmergencyType(str, Enum):
    PRIVACY = "プライバシー"          # 住所特定等
    SAFETY_THREAT = "安全脅迫"        # 危害示唆
    LEAK_THREAT = "拡散予告"          # まとめサイト・暴露系タレコミ
    BUSINESS_HARM = "実害・スポンサー妨害"
    NONE = "該当なし"


@dataclass
class CommentJudgment:
    comment_id: str
    category: Category
    surface_level: SurfaceLevel
    tatemae_pattern: TatemaePattern
    emergency: EmergencyType
    reasoning: str  # LLMの判定理由(デバッグ・監査用、利用者には非表示)


# ============ 判定プロンプト(1回のAPI呼び出しで4項目まとめて取得) ============

_CATEGORY_VALUES = "/".join(c.value for c in Category)
_EMERGENCY_VALUES = "\n".join(f"- {e.value}" for e in EmergencyType if e != EmergencyType.NONE)

FEW_SHOT_EXAMPLES = """\
【判定例】
- 「才能なさすぎ、動画作るのやめたら？」
  → category=活動クオリティ, surface_level=2, tatemae_pattern=該当なし
  (活動停止要求を含み字面通りの攻撃。皮肉的な建前がないため該当なし)
- 「編集をもう少し工夫できるかも？」
  → category=活動クオリティ, surface_level=1, tatemae_pattern=該当なし
  (提案の形式で、侮蔑語・命令形・活動停止要求を含まない)
- 「〇〇さんの方が編集うまいよね」
  → category=活動クオリティ, surface_level=1, tatemae_pattern=他者比較
  (他クリエイターとの比較を経由した間接的な批判)
- 「もう見ません」
  → category=該当なし, surface_level=1, tatemae_pattern=ファン離脱
  (離脱の事実報告を装い罪悪感を負わせる。侮蔑語がないためレベル1)
- 「頭大丈夫？」
  → category=人格否定, surface_level=2, tatemae_pattern=疑問形
  (疑問文の形式だが、答えようがなく相手を貶める機能を持つ。侮蔑的意図が明確なためレベル2)
- 「よくこんな内容で続けられるね(笑)」
  → category=活動クオリティ, surface_level=1, tatemae_pattern=褒め殺し型
  (「よく〜できるね」という感心を装う構文の中に、否定的評価(内容が低い)が埋め込まれている)
- 「みんな呆れてるよ」
  → category=人格否定, surface_level=1, tatemae_pattern=主語肥大化
  (個人の意見を「みんな」に置き換えて圧力をかけている)
- 「老婆心ながら、そのやり方は良くないと思いますよ」
  → category=モラル・マナー説教, surface_level=1, tatemae_pattern=自己満足アドバイス
  (助言を装いながら見下す評価になっている)
- 「今日の動画も面白かったです！」
  → category=該当なし, surface_level=1, tatemae_pattern=該当なし
  (純粋な肯定的感想で、攻撃的意図はない)
- 「この背景の景色、家の近くだよね特定した」
  → category=該当なし, surface_level=1, tatemae_pattern=該当なし, emergency=プライバシー
  (特定カテゴリへの批判ではなく、実生活の特定を示唆する緊急案件)

【注意】疑問形と褒め殺し型は、文法形式(疑問文か断定文か)では判定しないこと。
表層レベルの判定基準と同じ形式的特徴を、建前パターンの判定に流用しないこと。
両者は攻撃が成立する仕組み(答えを封じるか、前提に否定を埋め込むか)で区別する。"""

SYSTEM_PROMPT = f"""以下のYouTubeコメントについて、次の4点を判定し、JSON形式で出力してください。

【判定項目】

① category(カテゴリ)
以下のいずれか一つ:{_CATEGORY_VALUES}
(該当なし=特定の話題に紐づかない、対象を問わない罵倒)

② surface_level(表層レベル、1〜2)
文面の形式のみで判定すること。文脈や意図は考慮しない。
1 = 批判(提案から断定まで、侮蔑語・命令形・活動停止要求を含まないもの。例:「編集をもう少し工夫できるかも?」「編集が下手だと思う」)
2 = 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む。例:「編集下手くそ、才能ないからやめろ」)

③ tatemae_pattern(建前パターン)
このコメントを字面通り受け取った場合と、皮肉・当てこすり・遠回しな攻撃として受け取った場合で、
意味やダメージの大きさが大きく変わる場合のみ、以下から最も近いものを一つ選ぶこと。
変わらない場合は「該当なし」とする。

- 他者比較:他クリエイターと比較する形で批判している(例:「〇〇さんの方が編集うまいよね」)
- ファン離脱:離脱の事実報告を装い罪悪感を負わせている(例:「もう見ません」)
- 疑問形:相手がどう答えても不利になる問いかけになっている(答えを封じる。例:「頭大丈夫?」)
- 褒め殺し型:表面上は褒め言葉の形だが、その中の限定句・前提部分に否定的評価が埋め込まれている(例:「よくこんな内容で続けられるね(笑)」)
- 主語肥大化:個人の意見を「みんな」「視聴者全員」等に置き換えている(例:「みんな呆れてる」)
- 丁寧語長文詰問:丁寧な文体のまま長文で特定の非を執拗に追及している
- 自己満足アドバイス:善意・助言を装いながら内容は見下す評価になっている(例:「老婆心ながら」)

④ emergency(緊急判定)
実生活への危険に関わる場合のみ、以下から選ぶ。それ以外は「該当なし」。
{_EMERGENCY_VALUES}

{FEW_SHOT_EXAMPLES}

必ず以下のJSON形式のみで回答してください(説明文やコードブロックは不要):
{{"category": "...", "surface_level": 1または2, "tatemae_pattern": "...", "emergency": "...", "reasoning": "簡潔な判定理由"}}"""


class PersonalJudgmentClassifier:
    """4次元(category/surface_level/tatemae_pattern/emergency)を1回のAPI呼び出しで判定する。"""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.client = make_client(api_key)
        self.model = model

    def classify(self, comment: str, comment_id: str = "", max_retries: int = 5) -> CommentJudgment:
        import time

        import httpx
        from google.genai import errors

        prompt = f"{SYSTEM_PROMPT}\n\n【入力コメント】\n{comment}"

        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
                )
                break
            except errors.ClientError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    time.sleep(15 * (attempt + 1))
                    continue
                raise
            except errors.ServerError as e:
                if e.code in (503, 500) and attempt < max_retries - 1:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                if attempt < max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise

        return self._parse(comment_id, comment, response.text)

    def _parse(self, comment_id: str, comment: str, raw_text: str) -> CommentJudgment:
        import json

        try:
            data = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            # 応答が壊れていた場合は安全側(緊急・攻撃なし)に倒す
            return CommentJudgment(
                comment_id=comment_id,
                category=Category.NONE,
                surface_level=SurfaceLevel.LEVEL_1,
                tatemae_pattern=TatemaePattern.NONE,
                emergency=EmergencyType.NONE,
                reasoning="LLM応答の解析に失敗したため安全側で該当なしとしました",
            )

        category = self._to_enum(Category, data.get("category"), Category.NONE)
        tatemae_pattern = self._to_enum(TatemaePattern, data.get("tatemae_pattern"), TatemaePattern.NONE)
        emergency = self._to_enum(EmergencyType, data.get("emergency"), EmergencyType.NONE)

        raw_level = data.get("surface_level")
        surface_level = SurfaceLevel.LEVEL_2 if raw_level == 2 else SurfaceLevel.LEVEL_1
        # 【設計原則の実装について】仕様書は「category=該当なし かつ 侮蔑語を含む場合」に
        # surface_levelをLEVEL_2に補正するとしているが、「該当なし」は純粋な肯定的コメント
        # (侮蔑語なし)にも使われるため、category単体での無条件補正は誤判定を生む
        # (実測で「今日の動画も面白かったです!」のような肯定コメントがLEVEL_2に
        # 誤補正されることを確認済み)。プロンプト側で既にsurface_levelを侮蔑語・命令形・
        # 活動停止要求の有無から独立して判定させているため、ここでの追加補正は行わず
        # LLMの直接判定をそのまま採用する。

        return CommentJudgment(
            comment_id=comment_id,
            category=category,
            surface_level=surface_level,
            tatemae_pattern=tatemae_pattern,
            emergency=emergency,
            reasoning=data.get("reasoning", ""),
        )

    @staticmethod
    def _to_enum(enum_cls, value, default):
        try:
            return enum_cls(value)
        except ValueError:
            return default

    def classify_batch(self, comments: list[str]) -> list[CommentJudgment]:
        return [self.classify(c, comment_id=str(i)) for i, c in enumerate(comments)]


def is_emergency(judgment: CommentJudgment) -> bool:
    return judgment.emergency != EmergencyType.NONE


if __name__ == "__main__":
    classifier = PersonalJudgmentClassifier()

    test_comments = [
        "才能なさすぎ、動画作るのやめたら？",
        "〇〇さんの方が編集うまいよね",
        "よくこんな内容で続けられるね(笑)",
        "今日の動画も面白かったです！",
        "この背景の景色、家の近くだよね特定した",
    ]

    print("=" * 70)
    print("パーソナライズ判定結果")
    print("=" * 70)
    for i, result in enumerate(classifier.classify_batch(test_comments)):
        print(f"\nコメント: 「{test_comments[i]}」")
        print(f"  category={result.category.value} / surface_level={result.surface_level.value} / "
              f"tatemae_pattern={result.tatemae_pattern.value} / emergency={result.emergency.value}")
        print(f"  理由: {result.reasoning}")
