"""
Regskip パーソナライズ判定レイヤー
====================================
regskip_実装仕様_for_claude_code.md の Phase 1(セクション1・2)に対応する、
4次元判定(category / surface_level / tatemae_pattern / emergency)の実装。

【このブランチ(zip-snapshot)について】ヒアリングを踏まえた「改善バージョン」
(5カテゴリー×6文脈)の実験用ブランチ。experimentalブランチ(8カテゴリー×5文脈)
とは別の比較対象として、あえてシンプルな分類体系で作っている。
tatemae_pattern というクラス名・フィールド名はAPI/保存データとの互換性のため
変更していないが、実質的には「文脈(How)」を表す軸として扱う。

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
    APPEARANCE_VOICE = "容姿・声"
    PERSONALITY = "人格"
    CONTENT = "動画の内容"
    POLITICAL = "政治的・イデオロギーへの批判"
    COMMUNITY = "関係者・コラボ・ファン"
    NONE = "該当なし"  # カテゴリに紐づかない直球の暴言、または純粋な好意的コメント用


class SurfaceLevel(int, Enum):
    LEVEL_1 = 1  # 批判(提案〜断定まで含む。侮蔑語・命令形・活動停止要求を含まない)
    LEVEL_2 = 2  # 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む)
    # 【確定事項】旧レベル1(提案・疑問形)と旧レベル2(断定・侮蔑語なし)は、
    # 「嫌な気持ちの差がない」との判断により統合済み。以後3段階には戻さないこと。


class TatemaePattern(str, Enum):
    """文脈(How)。クラス名は旧仕様(建前パターン)のまま残しているが、実質的には文脈軸。
    【注意】「暴言・粗野な言葉遣い」はsurface_level(強度)と、「脅迫」はemergency
    (安全脅迫)と役割が重複する部分がある。emergencyの緊急判定は、このtatemae_pattern
    の値に関わらず常に最優先・無条件でバイパスされるため、実害を伴う本物の脅迫は
    この文脈タグの有無とは関係なくemergency側で拾われる。"""
    MOCKERY = "揶揄・あざけり"
    ABUSE = "暴言・粗野な言葉遣い"
    THREAT = "脅迫"
    BIASED_BELIEF = "思い込みによる攻撃"
    FAKE_CONSENSUS = "集団意見の偽装"
    COMPARISON = "他者比較による蔑み"
    NONE = "該当なし"


class EmergencyType(str, Enum):
    PRIVACY = "プライバシー"          # 住所・本名等の特定、家族への言及、経歴暴露・デマ(真偽不明/軽度含む)、ストーカー的言及
    SAFETY_THREAT = "安全脅迫"        # 危害示唆
    LEAK_THREAT = "拡散予告"          # まとめサイト・暴露系タレコミ
    BUSINESS_HARM = "実害・スポンサー妨害"
    NONE = "該当なし"


@dataclass
class CommentJudgment:
    comment_id: str
    categories: list[Category]  # 1コメントが複数カテゴリに同時に該当することがあるため配列
    surface_level: SurfaceLevel
    tatemae_pattern: TatemaePattern  # 文脈(How)。フィールド名は旧仕様のまま。
    emergency: EmergencyType
    reasoning: str  # LLMの判定理由(デバッグ・監査用、利用者には非表示)


# ============ 判定プロンプト(1回のAPI呼び出しで4項目まとめて取得) ============

_CATEGORY_VALUES = "/".join(c.value for c in Category)
_EMERGENCY_VALUES = "\n".join(f"- {e.value}" for e in EmergencyType if e != EmergencyType.NONE)

CATEGORY_DESCRIPTIONS = """\
- 容姿・声:顔立ち・体型・声質・髪型・服装など身体的特徴への攻撃
- 人格:性格・知性・態度・振る舞い・活動姿勢への侮辱(お金・案件・人気・成功へのやっかみや、
  「初心を忘れた」「調子に乗っている」等の姿勢への非難も含む)
- 動画の内容:企画・編集・トーク・プレイスキル・才能等、動画そのものの質への評価や、
  「再生数稼ぎ」「台本通り」など動画・企画の意図の決めつけ
- 政治的・イデオロギーへの批判:政治的スタンス・思想信条を理由にした攻撃
- 関係者・コラボ・ファン:視聴者民度・リスナー層・スタッフ・コラボ相手への批判、
  または他の配信者・関係者の発言を持ち出して人間関係の不和をあおる伝聞・告げ口
  (例:「リスナーの層が悪い」「スタッフの質が悪い」「〇〇があなたの悪口言ってた」)
- 該当なし:純粋な好意的・中立的コメント、または配信者への否定的評価を含まないもの"""

CATEGORY_RULES = """\
【カテゴリ判別ルール】
- 配信者本人やその動画への否定的な評価・非難・圧力を含むコメントは、「該当なし」にせず、
  上記5つのうち最も近いカテゴリを必ず選ぶこと。対象が明示されていなくても、
  「あなた」本人への評価なら人格、動画・活動の出来への評価なら動画の内容とする。
- 皮肉(褒めるふり)の場合は、褒めるふりをしている対象でカテゴリを選ぶこと。
  メイク・仕上がり・見た目なら容姿・声、「意識高い」「正義感」「ご意見」など思想・主張なら
  政治的・イデオロギーへの批判、ファン・スタッフ・コラボ相手への対応なら関係者・コラボ・ファン。
- 話題が複数の独立した対象(例:容姿と人格)にまたがる場合は、該当する全カテゴリを配列で返すこと。
  1つの話題の言い換え・比喩に過ぎない場合は1つだけを選ぶこと。
- 実生活の特定・経歴暴露やデマ・ストーカー的言及は、真偽や危険度が本文だけでは判断できないため、
  重さに関わらず常にemergency=プライバシーとして扱い、categoryでは判定しないこと。"""

CONTEXT_DESCRIPTIONS = """\
- 揶揄・あざけり:皮肉っぽく小馬鹿にする、または丁寧な言葉遣いの裏に嫌味を込める
- 暴言・粗野な言葉遣い:侮蔑語・罵倒語(「ブス」「ゴミ」「民度最低」「気持ち悪い」等)や
  命令形を伴う直接的な罵倒。命令形がなくても、侮蔑語で直接けなしていれば該当する
- 脅迫:実害・危害を示唆する言い回し(「痛い目見るよ」「後悔させてやる」「覚えとけ」等の
  ぼかした脅し文句を含む)
- 思い込みによる攻撃:根拠のない決めつけ(相手の意図・動機の勝手な断定等)や、
  真偽不明の伝聞(「〇〇が悪口言ってた」等)で攻撃する
- 集団意見の偽装:個人の意見を「みんな」「視聴者全員」等、複数人・コミュニティ全体の
  総意であるかのように偽装する
- 他者比較による蔑み:他者・他チャンネルと比較して相対的に貶める"""

CONTEXT_RULES = """\
【文脈判別ルール】
- 文法形式(疑問文か断定文か、敬語か)だけで判定しないこと。
- 思い込みによる攻撃(動機・意図の決めつけ)と、集団意見の偽装(数・総意を騙る)は、
  「個人の勝手な決めつけ」か「架空の多数派を演出しているか」で区別すること。
- 特定の手口が見当たらない場合は「該当なし」とする。"""

FEW_SHOT_EXAMPLES = f"""\
【判定例】
- 「ブスすぎ、声も聞き苦しい」
  → category=[容姿・声], surface_level=2, tatemae_pattern=暴言・粗野な言葉遣い
  (身体的特徴への侮蔑語を含む直球の攻撃)
- 「性格終わってるだろ、消えろ」
  → category=[人格], surface_level=2, tatemae_pattern=暴言・粗野な言葉遣い
  (命令形を含む直接的な罵倒)
- 「編集下手すぎ、内容もつまらない」
  → category=[動画の内容], surface_level=2, tatemae_pattern=該当なし
  (企画・編集の「質」への評価)
- 「その思想を発信するのはどうかと思う」
  → category=[政治的・イデオロギーへの批判], surface_level=1, tatemae_pattern=該当なし
  (政治的スタンスを理由にした批判。侮蔑語を含まないためレベル1)
- 「リスナーの層が悪い、民度低すぎ」
  → category=[関係者・コラボ・ファン], surface_level=2, tatemae_pattern=暴言・粗野な言葉遣い
  (視聴者層を侮蔑語で直接けなしている)
- 「才能ないんだから動画作るのやめな」
  → category=[動画の内容], surface_level=2, tatemae_pattern=暴言・粗野な言葉遣い
  (動画を作る才能・実力への罵倒。人柄ではなく活動の質への攻撃なので動画の内容)
- 「どうせバズ狙いの企画でしょ」
  → category=[動画の内容], surface_level=1, tatemae_pattern=思い込みによる攻撃
  (企画・動画の意図の決めつけ。お金目当てという人柄への非難ではないので動画の内容)
- 「社会派気取り、ご立派ですね(笑)」
  → category=[政治的・イデオロギーへの批判], surface_level=1, tatemae_pattern=揶揄・あざけり
  (主張・思想の「意識の高さ」を、褒めるふりをして皮肉っている)
- 「スタッフの質が悪いって聞きましたけど」
  → category=[関係者・コラボ・ファン], surface_level=1, tatemae_pattern=該当なし
  (関係者への批判。断定的だが侮蔑語・命令形を含まないためレベル1)
- 「よくこんな内容で続けられるね(笑)」
  → category=[動画の内容], surface_level=1, tatemae_pattern=揶揄・あざけり
  (「よく〜できるね」という感心を装う構文の中に、否定的評価が埋め込まれている)
- 「こんな企画ばっかりだと、そのうち後悔することになるよ」
  → category=[動画の内容], surface_level=1, tatemae_pattern=脅迫, emergency=該当なし
  (何をするかがぼかされた脅し文句。具体的な危害の予告ではないので緊急判定にはせず、
  脅しの理由になっている対象のカテゴリと文脈=脅迫で判定する)
- 「家まで行って殴ってやる」
  → category=[該当なし], surface_level=2, tatemae_pattern=脅迫, emergency=安全脅迫
  (身体への危害を具体的に予告している。実際の表示判定ではemergency側が常に最優先される)
- 「どうせお金のためにやってるんでしょ」
  → category=[人格], surface_level=1, tatemae_pattern=思い込みによる攻撃
  (根拠を示さず、相手の動機を勝手に決めつけている。お金への非難は活動姿勢への攻撃なので人格)
- 「みんな不快に思ってるよ、視聴者全員呆れてる」
  → category=[人格], surface_level=1, tatemae_pattern=集団意見の偽装
  (個人の意見を「みんな」「視聴者全員」に置き換えて、配信者本人に圧力をかけている)
- 「〇〇(他のYouTuber)があなたの悪口言ってましたよ」
  → category=[関係者・コラボ・ファン], surface_level=1, tatemae_pattern=思い込みによる攻撃
  (直接の批判ではないが、真偽不明の伝聞で他の配信者との関係に波風を立てようとしている)
- 「〇〇さんの方が編集うまいよね」
  → category=[動画の内容], surface_level=1, tatemae_pattern=他者比較による蔑み
  (他クリエイターとの比較を経由した間接的な批判)
- 「今日の動画も面白かったです!」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし
  (純粋な肯定的感想で、攻撃的意図はない)
- 「体格いいね」「最初の女の子可愛い」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし
  (限定句・否定的な前提を伴わない、素直な褒め言葉。否定的な評価が明確に埋め込まれている
  場合のみ揶揄・あざけりに該当し、単語だけで機械的に判定しないこと)
- 「この背景の景色、家の近くだよね特定した」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし, emergency=プライバシー
  (実生活の特定を示唆する緊急案件。特定カテゴリへの批判ではないため該当なし)
- 「ブスだし性格も終わってるよね」
  → category=[容姿・声, 人格], surface_level=2, tatemae_pattern=該当なし
  (容姿と人格、2つの独立したカテゴリへの侮蔑語を含む攻撃が1文に同時に含まれている。
  このように複数のカテゴリに同時に当てはまる場合は、該当するもの全てを配列で返すこと)"""

SYSTEM_PROMPT = f"""以下のYouTubeコメントについて、次の4点を判定し、JSON形式で出力してください。

【判定項目】

① category(カテゴリ、対象=What)
以下から該当するもの全てを配列で挙げること(複数該当してよい):{_CATEGORY_VALUES}

{CATEGORY_DESCRIPTIONS}

{CATEGORY_RULES}

② surface_level(表層レベル、1〜2)
文面の形式のみで判定すること。文脈や意図は考慮しない。
1 = 批判(提案から断定まで、侮蔑語・命令形・活動停止要求を含まないもの。例:「編集をもう少し工夫できるかも?」「編集が下手だと思う」)
2 = 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む。例:「編集下手くそ、才能ないからやめろ」)
   「もう投稿しない方がいい」「引退した方がチャンネルのため」のように、丁寧な言い方や
   助言の形でも、活動の停止・引退を求めていれば活動停止要求として2とする。

③ tatemae_pattern(文脈、How)
このコメントがどんな心理的な手口を使っているか、以下から最も近いものを一つ選ぶこと。
特定の手口が見当たらない場合は「該当なし」とする。

{CONTEXT_DESCRIPTIONS}

{CONTEXT_RULES}

④ emergency(緊急判定)
実生活への危険に関わる場合のみ、以下から選ぶ。それ以外は「該当なし」。
{_EMERGENCY_VALUES}
安全脅迫は、殺害・暴力・押しかけ等、身体や生活への具体的な危害を予告・示唆する場合に限る。
「痛い目見るよ」「後悔させてやる」のように何をするかがぼかされた脅し文句は、
emergency=該当なしとし、tatemae_pattern=脅迫と対象のカテゴリで判定すること。

{FEW_SHOT_EXAMPLES}

必ず以下のJSON形式のみで回答してください(説明文やコードブロックは不要):
{{"category": ["...", "..."], "surface_level": 1または2, "tatemae_pattern": "...", "emergency": "...", "reasoning": "簡潔な判定理由"}}"""


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
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                        # 思考を控えめにする(既定より1件あたり約4.5秒→約3秒、思考トークン分の料金も減る)。
                        # minimalはさらに速いが、会場への押しかけ予告等の安全脅迫を見逃したため使わない
                        thinking_config=types.ThinkingConfig(thinking_level="low"),
                    ),
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
                categories=[Category.NONE],
                surface_level=SurfaceLevel.LEVEL_1,
                tatemae_pattern=TatemaePattern.NONE,
                emergency=EmergencyType.NONE,
                reasoning="LLM応答の解析に失敗したため安全側で該当なしとしました",
            )
        # まれにオブジェクトを配列で包んで返すことがあるので、先頭要素を使う
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if not isinstance(data, dict):
            data = {}

        categories = self._to_enum_list(Category, data.get("category"), Category.NONE)
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
            categories=categories,
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

    @staticmethod
    def _to_enum_list(enum_cls, value, default):
        """category用。配列を期待するが、旧形式(単一文字列)が返ってきても救済する。
        不正な値は無視し、有効な値が1つも無ければ[default]にする。重複は除く。
        """
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return [default]
        result = []
        for v in value:
            try:
                parsed = enum_cls(v)
            except (ValueError, TypeError):
                continue
            if parsed not in result:
                result.append(parsed)
        return result or [default]

    def classify_batch(self, comments: list[str]) -> list[CommentJudgment]:
        return [self.classify(c, comment_id=str(i)) for i, c in enumerate(comments)]


def is_emergency(judgment: CommentJudgment) -> bool:
    return judgment.emergency != EmergencyType.NONE


if __name__ == "__main__":
    classifier = PersonalJudgmentClassifier()

    test_comments = [
        "ブスすぎ、声も聞き苦しい",
        "リスナーの層が悪い、民度低すぎ",
        "〇〇さんの方が編集うまいよね",
        "みんな不快に思ってるよ、視聴者全員呆れてる",
        "今日の動画も面白かったです！",
        "この背景の景色、家の近くだよね特定した",
    ]

    print("=" * 70)
    print("パーソナライズ判定結果")
    print("=" * 70)
    for i, result in enumerate(classifier.classify_batch(test_comments)):
        print(f"\nコメント: 「{test_comments[i]}」")
        cat_str = "・".join(c.value for c in result.categories)
        print(f"  category=[{cat_str}] / surface_level={result.surface_level.value} / "
              f"tatemae_pattern={result.tatemae_pattern.value} / emergency={result.emergency.value}")
        print(f"  理由: {result.reasoning}")
