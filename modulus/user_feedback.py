"""
グレー判定へのユーザーフィードバック管理
==========================================
「確認待ち」タブで本人が下した「見たくない/問題ない」の判断を保存し、
次回以降、似たコメントが来たときにエンベディング類似度で
過去の判断をそのまま再現できるようにする(LLM/人手への問い合わせを省略)。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sklearn.metrics.pairwise import cosine_similarity

from gemini_client import make_client, embed_text

Decision = Literal["見たくない", "問題ない"]

# 過去の確認済みコメントとの類似度がこの値以上のときだけ、
# 判断を再利用する(通常のカテゴリ判定の閾値より厳しめに設定)
FEEDBACK_MATCH_THRESHOLD = 0.90


def _feedback_path() -> Path:
    return Path(__file__).parent.parent / "data" / "user_feedback.json"


def load_feedback() -> list[dict]:
    path = _feedback_path()
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_feedback_entry(comment: str, decision: Decision, category: str | None) -> None:
    """ユーザーが「見たくない/問題ない」で確定させた判断を永続化する"""
    entries = load_feedback()
    entries.append({"comment": comment, "decision": decision, "category": category})
    path = _feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


@dataclass
class KnownDecision:
    decision: Decision
    category: str | None
    similarity: float
    matched_comment: str


class FeedbackMatcher:
    """過去にユーザーが確認済みのコメントと、新しいコメントの類似度を見る"""

    def __init__(self, api_key: str | None = None):
        self._client = make_client(api_key)
        self._entries: list[dict] = []
        self._vectors: list[list[float]] = []

    def fit(self) -> None:
        """保存済みフィードバックを読み込みベクトル化する(フィードバックが増えるたびに呼び直す想定)"""
        self._entries = load_feedback()
        self._vectors = [embed_text(self._client, e["comment"]) for e in self._entries]

    def find_known_decision(self, comment: str) -> KnownDecision | None:
        """十分似た過去の確認済みコメントがあれば、その判断を返す。無ければNone"""
        if not self._entries:
            return None
        vec = embed_text(self._client, comment)
        sims = cosine_similarity([vec], self._vectors)[0]
        idx = int(sims.argmax())
        if sims[idx] < FEEDBACK_MATCH_THRESHOLD:
            return None
        entry = self._entries[idx]
        return KnownDecision(
            decision=entry["decision"],
            category=entry["category"],
            similarity=float(sims[idx]),
            matched_comment=entry["comment"],
        )
