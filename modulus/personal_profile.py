"""
Regskip パーソナライズ判定レイヤー — 表示判定・個人プロファイル
====================================================================
regskip_実装仕様_for_claude_code.md のセクション4/4.6/4.7/5に対応。
personal_classifier.py の CommentJudgment を受け取り、
個人の設定・過去の判断履歴に基づいて実際に非表示にするかどうかを決める。
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from sklearn.metrics.pairwise import cosine_similarity

from gemini_client import make_client, embed_text
from personal_classifier import Category, CommentJudgment, EmergencyType, SurfaceLevel, TatemaePattern

# ============ 4節: 個人プロファイル ============


class PersonalAction(str, Enum):
    NORMAL = "normal"              # 通常表示
    HIDE_REGSKIP = "hide_regskip"  # 見たくない(Regskip内でのみ非表示、YouTube上は公開のまま)
    HIDE_YOUTUBE = "hide_youtube"  # 非表示にしたい(setModerationStatusでYouTube上からも非表示)


@dataclass
class PatternSetting:
    action: PersonalAction
    source: str = "manual"  # "manual" | "diagnostic_clear" | "diagnostic_ambiguous"
    # source == "diagnostic_ambiguous" の場合のみ、手動設定画面に警告バッジ(「診断では迷っていました」)を表示する。
    # 診断で「非表示にしたい」「見たくない」と明確に答えた場合は "diagnostic_clear"(警告なしでボタンを事前選択)。
    # 「別に見たいとは思わない」の場合は action=HIDE_REGSKIP・source="diagnostic_ambiguous" として反映する。
    # (診断機能自体は本ファイルでは未実装。Phase 2で心理学レビュー完了後に着手する。)


@dataclass
class PersonalProfile:
    user_id: str
    # カテゴリごとの「攻撃的な言い方」の扱い(手動設定画面、3択スイッチ)。デフォルトはHIDE_REGSKIP。
    attack_action: dict[Category, PersonalAction] = field(default_factory=dict)
    # カテゴリ×建前パターンごとの扱い。デフォルトはNORMAL(未設定=そのキー自体が辞書にない)。
    pattern_action: dict[tuple[Category, TatemaePattern], PatternSetting] = field(default_factory=dict)

    def get_attack_action(self, category: Category) -> PersonalAction:
        return self.attack_action.get(category, PersonalAction.HIDE_REGSKIP)

    def get_pattern_setting(self, category: Category, pattern: TatemaePattern) -> PatternSetting:
        return self.pattern_action.get((category, pattern), PatternSetting(action=PersonalAction.NORMAL))


@dataclass
class DisplayAction:
    hide: bool
    escalate_to_youtube: bool  # Trueの場合、setModerationStatusを呼び出す
    reason: str  # "personal_similarity" | "personal_pattern" | "attack_level" | "none"(デバッグ・監査用)


def resolve_display_action(
    judgment: CommentJudgment,
    profile: PersonalProfile,
    similarity_action: Optional[PersonalAction] = None,  # PersonalSimilarityList.check_similarityの結果
) -> DisplayAction:
    # 最優先: 個人用の類似検索リストに該当があれば、それを採用する
    # (実際のコメントに対する、最も具体的で新しい意思表示のため。NORMALの明示も含めて最優先)
    if similarity_action is not None:
        return DisplayAction(
            hide=(similarity_action != PersonalAction.NORMAL),
            escalate_to_youtube=(similarity_action == PersonalAction.HIDE_YOUTUBE),
            reason="personal_similarity",
        )

    # 個人ルール: カテゴリ×建前パターンの組み合わせに、通常表示以外の設定があれば従う
    pattern_setting = profile.get_pattern_setting(judgment.category, judgment.tatemae_pattern)
    if pattern_setting.action != PersonalAction.NORMAL:
        return DisplayAction(
            hide=True,
            escalate_to_youtube=(pattern_setting.action == PersonalAction.HIDE_YOUTUBE),
            reason="personal_pattern",
        )

    # 攻撃レベル(侮蔑語等を含む)は、カテゴリごとの3択設定に従う
    if judgment.surface_level == SurfaceLevel.LEVEL_2:
        attack_action = profile.get_attack_action(judgment.category)
        if attack_action != PersonalAction.NORMAL:
            return DisplayAction(
                hide=True,
                escalate_to_youtube=(attack_action == PersonalAction.HIDE_YOUTUBE),
                reason="attack_level",
            )

    return DisplayAction(hide=False, escalate_to_youtube=False, reason="none")


# ============ 5節: 緊急ルート ============


def is_emergency(judgment: CommentJudgment) -> bool:
    return judgment.emergency != EmergencyType.NONE

# is_emergencyがTrueの場合:
# - 個人プロファイルを一切参照せず、既存の通報・エスカレーション導線に直行させる
# - policy_action.py側に、emergency専用のアクションパスを新設することを推奨(未実装、TODO)


# ============ 4.6節: 個人用の類似検索リスト ============
# 重要: この仕組みはembedding_classifier.pyの共有カテゴリベクトルには一切書き込まない。
# 利用者ごとに完全に独立したベクトル集合として実装する。共有モデルの学習ループに混ぜないこと。

HIGH_SIMILARITY_THRESHOLD = 0.85  # 暫定値。実コメントテストで調整すること
MID_SIMILARITY_THRESHOLD = 0.60   # 暫定値。実コメントテストで調整すること


@dataclass
class SimilarityMark:
    comment: str
    embedding: list[float]
    action: PersonalAction
    category: Category
    tatemae_pattern: TatemaePattern
    surface_level: SurfaceLevel  # 【仕様書からの変更点】ユーザー指示によりレベルも記憶・再現の対象に追加
    author_channel_id: str | None = None  # 要注意ユーザー機能(投稿者単位の集計)のために保持


def _similarity_store_path(user_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return Path(__file__).parent.parent / "data" / f"personal_similarity_{safe_id}.json"


class PersonalSimilarityList:
    """利用者ごとの過去の判断を埋め込みで記憶し、似たコメントに同じ判断を再現する。"""

    def __init__(self, api_key: str | None = None):
        self._client = make_client(api_key)
        self._marks: dict[str, list[SimilarityMark]] = {}

    def _load(self, user_id: str) -> list[SimilarityMark]:
        if user_id in self._marks:
            return self._marks[user_id]
        path = _similarity_store_path(user_id)
        marks: list[SimilarityMark] = []
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for r in raw:
                marks.append(SimilarityMark(
                    comment=r["comment"],
                    embedding=r["embedding"],
                    action=PersonalAction(r["action"]),
                    category=Category(r["category"]),
                    tatemae_pattern=TatemaePattern(r["tatemae_pattern"]),
                    surface_level=SurfaceLevel(r["surface_level"]),
                    author_channel_id=r.get("author_channel_id"),
                ))
        self._marks[user_id] = marks
        return marks

    def _save(self, user_id: str) -> None:
        path = _similarity_store_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = [
            {
                "comment": m.comment,
                "embedding": m.embedding,
                "action": m.action.value,
                "category": m.category.value,
                "tatemae_pattern": m.tatemae_pattern.value,
                "surface_level": m.surface_level.value,
                "author_channel_id": m.author_channel_id,
            }
            for m in self._marks.get(user_id, [])
        ]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    def mark_comment(
        self,
        user_id: str,
        comment: str,
        action: PersonalAction,
        category: Category,
        tatemae_pattern: TatemaePattern,
        surface_level: SurfaceLevel,
        author_channel_id: str | None = None,
    ) -> None:
        marks = self._load(user_id)
        embedding = embed_text(self._client, comment)
        marks.append(SimilarityMark(
            comment=comment,
            embedding=embedding,
            action=action,
            category=category,
            tatemae_pattern=tatemae_pattern,
            surface_level=surface_level,
            author_channel_id=author_channel_id,
        ))
        self._save(user_id)

    def check_similarity(self, user_id: str, new_comment: str) -> tuple[Optional[PersonalAction], float]:
        marks = self._load(user_id)
        if not marks:
            return None, 0.0
        vec = embed_text(self._client, new_comment)
        sims = cosine_similarity([vec], [m.embedding for m in marks])[0]
        idx = int(sims.argmax())
        best_score = float(sims[idx])
        if best_score < MID_SIMILARITY_THRESHOLD:
            return None, 0.0
        return marks[idx].action, best_score


def similarity_tier(score: float) -> str:
    """UI側で「高一致/グレーゾーン/無関係」の3段階表示を出し分けるためのヘルパー。"""
    if score >= HIGH_SIMILARITY_THRESHOLD:
        return "high"
    if score >= MID_SIMILARITY_THRESHOLD:
        return "grey"
    return "none"


# ============ 4.7節: 育った設定のレビュー ============


def find_promotion_candidates(user_id: str, similarity_list: PersonalSimilarityList, min_count: int = 5) -> list[dict]:
    """(category, tatemae_pattern)の組み合わせで、action != NORMALの記録がmin_count件以上あるものを抽出する。"""
    marks = similarity_list._load(user_id)
    groups: dict[tuple[Category, TatemaePattern], list[PersonalAction]] = {}
    for m in marks:
        if m.action == PersonalAction.NORMAL:
            continue
        groups.setdefault((m.category, m.tatemae_pattern), []).append(m.action)

    candidates = []
    for (category, pattern), actions in groups.items():
        if len(actions) < min_count:
            continue
        most_common = max(set(actions), key=actions.count)
        candidates.append({
            "category": category,
            "tatemae_pattern": pattern,
            "count": len(actions),
            "most_common_action": most_common,
        })
    return candidates


def apply_promotion(profile: PersonalProfile, category: Category, tatemae_pattern: TatemaePattern, action: PersonalAction) -> None:
    """「初期設定に反映する」ボタンが押された時の処理。"""
    profile.pattern_action[(category, tatemae_pattern)] = PatternSetting(action=action, source="manual")


def find_flagged_authors(
    user_id: str, similarity_list: PersonalSimilarityList, min_count: int = 3
) -> list[dict]:
    """要注意ユーザー機能: 投稿者(author_channel_id)単位で、action != NORMALの件数を集計する。
    UI仕様の「投稿者単位に置き換えたもの」に対応(4.7節と同様の集計処理)。"""
    marks = similarity_list._load(user_id)
    by_author: dict[str, list[SimilarityMark]] = {}
    for m in marks:
        if m.action == PersonalAction.NORMAL or not m.author_channel_id:
            continue
        by_author.setdefault(m.author_channel_id, []).append(m)

    flagged = []
    for author_id, author_marks in by_author.items():
        if len(author_marks) < min_count:
            continue
        breakdown: dict[tuple[Category, TatemaePattern], int] = {}
        for m in author_marks:
            key = (m.category, m.tatemae_pattern)
            breakdown[key] = breakdown.get(key, 0) + 1
        flagged.append({
            "author_channel_id": author_id,
            "count": len(author_marks),
            "breakdown": breakdown,
        })
    return flagged
