"""
Regskip パーソナライズ判定レイヤー
====================================
regskip_実装仕様_for_claude_code.md の Phase 1(セクション1・2)に対応する、
4次元判定(category / surface_level / tatemae_pattern / emergency)の実装。

【2026-10改訂】category/tatemae_pattern(文脈)を、より網羅的な新しい分類体系に
置き換えた。tatemae_pattern というクラス名・フィールド名自体はAPI/保存データとの
互換性のため変更していないが、実質的には「文脈(How)」を表す軸として扱う。

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
    APPEARANCE = "外見・身体的特徴"
    SEXUAL = "セクハラ・性的消費"
    PERSONALITY = "人間性・属性・思想"
    CONTENT_QUALITY = "コンテンツ・スキル・実績"
    MORAL_MONEY = "モラル・金銭・炎上扇動"
    COMMUNITY = "ファン・コミュニティ・関係者"
    OPERATION_STYLE = "活動スタイル・運営方針"
    BASELESS_DENIAL = "理由なき暴言・存在否定"
    NONE = "該当なし"  # カテゴリに紐づかない直球の暴言、または純粋な好意的コメント用


class SurfaceLevel(int, Enum):
    LEVEL_1 = 1  # 批判(提案〜断定まで含む。侮蔑語・命令形・活動停止要求を含まない)
    LEVEL_2 = 2  # 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む)
    # 【確定事項】旧レベル1(提案・疑問形)と旧レベル2(断定・侮蔑語なし)は、
    # 「嫌な気持ちの差がない」との判断により統合済み。以後3段階には戻さないこと。
    # 【2026-10改訂での再確認】新設した強度軸(intensity)の案を検討したが、
    # 「手口のない純粋な暴言ほど強度が上がる」という性質そのものがこのsurface_level
    # と機能的に重複することが判明したため、新設は見送り、既存のこの2段階を流用する。


class TatemaePattern(str, Enum):
    """文脈(How)。相手がどんな"手口"で攻撃・要求してきているかを表す。
    クラス名は旧仕様(建前パターン)のまま残しているが、実質的には文脈軸。
    【確定事項】威圧・攻撃(手口のない純粋な暴言)は、surface_levelと役割が重複するため
    選択肢に含めない。手口の見当たらない暴言は tatemae_pattern=該当なし のまま、
    surface_level側で強度を表現する。"""
    GUIDANCE_DEMAND = "指導・要求"
    DISAPPOINTMENT_WITHDRAWAL = "失望・離脱"
    GUILT_INDUCTION = "罪責感誘発"
    COMPARISON_DEMOTION = "比較・降格"
    MOCKERY = "冷笑・煽り"
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
- 外見・身体的特徴:顔立ち・体型・声質・髪型・衣装・清潔感など身体的特徴への攻撃
- セクハラ・性的消費:性的言動・身体の過度な評価・卑わいな要求・性的対象化
- 人間性・属性・思想:性格・知性・態度への侮辱、属性への差別、政治・社会的スタンスへの攻撃
- コンテンツ・スキル・実績:企画・編集・プレイスキル・知識量・再生数等の「質」への評価
  (上手いか下手か、面白いかつまらないか、という評価軸。「モラル・金銭・炎上扇動」との違いに注意)
- モラル・金銭・炎上扇動:マナー違反、著作権疑惑、お金稼ぎ批判、規約違反主張、通報呼びかけ
  (正しいか不正か、誠実かズルいか、という倫理・規範軸。「コンテンツ・スキル・実績」との違いに注意)
- ファン・コミュニティ・関係者:視聴者民度批判、ファン対立誘発、コラボ相手・スタッフへのバッシング
- 活動スタイル・運営方針:投稿頻度・配信スケジュール・コメント返信方針等、活動の進め方への言及
  (内容の質や倫理ではなく「どう運用しているか」というプロセスの話題であることに注意)
- 理由なき暴言・存在否定:特定のテーマや根拠を伴わない、純粋な悪意のみの存在否定・呪詛
- 該当なし:上記のいずれにも当てはまらない"""

CATEGORY_RULES = """\
【カテゴリ判別ルール】
- 「コンテンツ・スキル・実績」と「モラル・金銭・炎上扇動」は、「上手い/下手」の評価か
  「正しい/不正」の評価かで区別すること。
- 「活動スタイル・運営方針」(カテゴリ=What)と「指導・要求」(文脈=How)は別軸である。
  例:「毎日投稿しろ」→ category=[活動スタイル・運営方針], tatemae_pattern=指導・要求 と
  両方成立しうる。
- 話題が「活動の進め方(頻度・形式等)」のみで、評価を示す言葉(ズルい・稼ぎすぎ・不誠実・
  申請規約違反 等)が本文に明示されていない場合は、「モラル・金銭・炎上扇動」を足さず
  「活動スタイル・運営方針」のみとすること。評価語が明示されている場合のみ両方を足す。
  本文に書かれていない意図を深読みして追加カテゴリを推測しないこと。
  例:「メンバー限定配信が多すぎる」→ category=[活動スタイル・運営方針] のみ
     「メンバー限定配信多すぎ、結局金儲けか」→ category=[活動スタイル・運営方針, モラル・金銭・炎上扇動]
- カテゴリは「プライベート・経歴・身バレ」に相当する項目を持たない。実生活の特定・
  経歴暴露やデマ・ストーカー的言及は、真偽や危険度が本文だけでは判断できないため、
  重さに関わらず常にemergency=プライバシーとして扱い、categoryでは判定しないこと。"""

CONTEXT_DESCRIPTIONS = """\
- 指導・要求:アドバイスや正義を装い、上から目線でコントロール・強要しようとする
- 失望・離脱:期待の裏返しとして「冷め」「見切り」を突きつけ孤立させようとする
- 罪責感誘発:「配慮不足」「悪者」のレッテルを貼り、過剰な反省・謝罪を強制しようとする
- 比較・降格:他者や指標と比較し、相対的に価値を貶める
- 冷笑・煽り:活動を小馬鹿にする、または丁寧な言葉遣いの裏に嫌味を含ませる"""

CONTEXT_RULES = """\
【文脈判別ルール】
- 文脈は「どんな心理的な手口を使っているか」で選ぶこと。手口が見当たらない
  (ただ強いだけの)暴言は tatemae_pattern=該当なし とし、強度はsurface_level側に委ねる。
  実害予告・安全脅迫はemergencyが拾うため、文脈側に「威圧・攻撃」のような選択肢はない。
- 失望・離脱(距離を取って孤立させる)と罪責感誘発(レッテルを貼って謝罪を迫る)は、
  「見捨てる」か「有罪認定する」かで区別する。
- 文法形式(疑問文か断定文か、敬語か)だけで判定しないこと。冷笑・煽りは「丁寧語の裏に
  嫌味がある」場合も含むため、敬語だから冷笑でないとは判断しない。"""

FEW_SHOT_EXAMPLES = f"""\
【判定例】
- 「ブスすぎて無理」
  → category=[外見・身体的特徴], surface_level=2, tatemae_pattern=該当なし
  (身体的特徴への侮蔑語を含む直球の攻撃)
- 「スタイルいいね、エロい体してる」
  → category=[外見・身体的特徴, セクハラ・性的消費], surface_level=1, tatemae_pattern=該当なし
  (外見評価と性的対象化が1文の中で同時に成立している)
- 「どうせ左翼思想でしょ、気持ち悪い」
  → category=[人間性・属性・思想], surface_level=2, tatemae_pattern=該当なし
  (政治的スタンスを理由にした侮蔑語を含む攻撃)
- 「編集下手すぎ、才能ない」
  → category=[コンテンツ・スキル・実績], surface_level=2, tatemae_pattern=該当なし
  (企画・編集の「質」への評価。上手い/下手の軸)
- 「メンバー限定配信が多すぎる」
  → category=[活動スタイル・運営方針], surface_level=1, tatemae_pattern=該当なし
  (活動の進め方のみへの言及。金銭・倫理を示す言葉が本文にないため、モラル・金銭は足さない)
- 「メンバー限定配信多すぎ、結局金儲けか」
  → category=[活動スタイル・運営方針, モラル・金銭・炎上扇動], surface_level=1, tatemae_pattern=該当なし
  (「金儲け」という評価語が明示されているため、運営方針への言及に加えて倫理軸のカテゴリも足す)
- 「もっとファンのこと考えて毎日投稿すべきじゃないですか?」
  → category=[活動スタイル・運営方針], surface_level=1, tatemae_pattern=指導・要求
  (活動の進め方への言及(カテゴリ)と、正義を装った要求(文脈)が同時に成立している)
- 「コラボ相手にあの態度、不誠実すぎるだろ」
  → category=[ファン・コミュニティ・関係者, モラル・金銭・炎上扇動], surface_level=1, tatemae_pattern=該当なし
  (関係者についての話題と、「不誠実」という倫理評価が同時に成立している)
- 「もう見る気失せました、残念です」
  → category=[該当なし], surface_level=1, tatemae_pattern=失望・離脱
  (離脱を突きつけて孤立させようとする文脈。侮蔑語はないためレベル1)
- 「そんな発言するなんて、ファンのこと何も考えてないんですね」
  → category=[ファン・コミュニティ・関係者], surface_level=1, tatemae_pattern=罪責感誘発
  (「配慮不足」のレッテルを貼り、謝罪・反省を迫る文脈)
- 「〇〇さんの方が全然面白いわ」
  → category=[コンテンツ・スキル・実績], surface_level=1, tatemae_pattern=比較・降格
  (他クリエイターとの比較を経由して、相対的に価値を貶めている)
- 「よくこんな内容で続けられますね(笑)」
  → category=[コンテンツ・スキル・実績], surface_level=1, tatemae_pattern=冷笑・煽り
  (「よく〜できるね」という感心を装う構文の中に、否定的評価(内容が低い)が埋め込まれている)
- 「お前の存在が無理、消えろ」
  → category=[理由なき暴言・存在否定], surface_level=2, tatemae_pattern=該当なし
  (特定の話題に紐づかない、純粋な存在否定。このカテゴリでは文脈判定は省略してよい)
- 「今日の動画も面白かったです!」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし
  (純粋な肯定的感想で、攻撃的意図はない)
- 「体格いいね」「最初の女の子可愛い」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし
  (限定句・否定的な前提を伴わない、素直な褒め言葉。否定的な評価が明確に埋め込まれている
  場合のみ冷笑・煽りに該当し、単語だけで機械的に判定しないこと)
- 「ブスだし性格も終わってるよね」
  → category=[外見・身体的特徴, 人間性・属性・思想], surface_level=2, tatemae_pattern=該当なし
  (外見と人格、2つの独立したカテゴリへの侮蔑語を含む攻撃が1文に同時に含まれている。
  このように複数のカテゴリに同時に当てはまる場合は、該当するもの全てを配列で返すこと)
- 「この背景の景色、家の近くだよね特定した」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし, emergency=プライバシー
  (実生活の特定を示唆する緊急案件。特定カテゴリへの批判ではないため該当なし)
- 「元カレに暴力振るってたらしいよ、知り合いから聞いた」
  → category=[該当なし], surface_level=1, tatemae_pattern=該当なし, emergency=プライバシー
  (真偽不明の経歴暴露でも、プライバシー関連は真偽・重さをAIが判断せず常に緊急案件として
  最優先で表面化させ、本人の判断に委ねる)"""

SYSTEM_PROMPT = f"""以下のYouTubeコメントについて、次の4点を判定し、JSON形式で出力してください。

【判定項目】

① category(カテゴリ、対象=What)
以下から該当するもの全てを配列で挙げること(複数該当してよい):{_CATEGORY_VALUES}

{CATEGORY_DESCRIPTIONS}

1つのコメントの中で、独立した複数の対象への批判・攻撃が同時に成立している場合は、
該当するカテゴリを全て配列に含めること。逆に、1つの話題についての言い換え・比喩に
過ぎない場合は1つだけを選ぶこと。どれにも当てはまらない場合は ["該当なし"] のみを
配列に入れること。

{CATEGORY_RULES}

② surface_level(表層レベル、1〜2)
文面の形式のみで判定すること。文脈や意図は考慮しない。
1 = 批判(提案から断定まで、侮蔑語・命令形・活動停止要求を含まないもの。例:「編集をもう少し工夫できるかも?」「編集が下手だと思う」)
2 = 攻撃(侮蔑語・命令形・活動停止要求のいずれかを含む。例:「編集下手くそ、才能ないからやめろ」)

③ tatemae_pattern(文脈、How)
このコメントがどんな心理的な手口を使っているか、以下から最も近いものを一つ選ぶこと。
特定の手口が見当たらない場合は「該当なし」とする。

{CONTEXT_DESCRIPTIONS}

{CONTEXT_RULES}

④ emergency(緊急判定)
実生活への危険に関わる場合のみ、以下から選ぶ。それ以外は「該当なし」。
{_EMERGENCY_VALUES}

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
                categories=[Category.NONE],
                surface_level=SurfaceLevel.LEVEL_1,
                tatemae_pattern=TatemaePattern.NONE,
                emergency=EmergencyType.NONE,
                reasoning="LLM応答の解析に失敗したため安全側で該当なしとしました",
            )

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
        "才能なさすぎ、動画作るのやめたら？",
        "メンバー限定配信多すぎ、結局金儲けか",
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
        cat_str = "・".join(c.value for c in result.categories)
        print(f"  category=[{cat_str}] / surface_level={result.surface_level.value} / "
              f"tatemae_pattern={result.tatemae_pattern.value} / emergency={result.emergency.value}")
        print(f"  理由: {result.reasoning}")
