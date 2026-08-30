"""
ハイブリッド判定パイプライン
==============================
1. まず過去のユーザー確認済みフィードバック(user_feedback.py)と
   十分似ていないかチェックする(埋め込み、高速・低コスト)
2. 一致しなければLLM(llm_classifier.py)で新規判定する
   (意図・皮肉・深刻度を考慮した判断)
3. 「グレー」になったものはUI側で本人に確認してもらい、
   save_feedback_entry() で結果を記録する
   → 次回以降、似たコメントは1.の時点で自動的に同じ判定になる

【実行方法】
1. export GEMINI_API_KEY="発行したAPIキー"
2. python hybrid_classifier.py
"""

from dataclasses import dataclass
from typing import Literal

from llm_classifier import LLMCommentClassifier
from user_feedback import FeedbackMatcher

Judgement = Literal["該当", "グレー", "非該当"]


@dataclass
class HybridResult:
    comment: str
    category: str | None
    judgement: Judgement
    source: Literal["feedback", "llm"]
    reason: str


class HybridClassifier:
    def __init__(self, api_key: str | None = None):
        self._llm_classifier = LLMCommentClassifier(api_key=api_key)
        self._feedback_matcher = FeedbackMatcher(api_key=api_key)

    def fit(self) -> None:
        self._feedback_matcher.fit()

    def classify(self, comment: str) -> HybridResult:
        known = self._feedback_matcher.find_known_decision(comment)
        if known is not None:
            # 「問題ない」と確認済み -> 非該当扱い、「見たくない」と確認済み -> 該当扱い
            judgement: Judgement = "非該当" if known.decision == "問題ない" else "該当"
            return HybridResult(
                comment=comment,
                category=known.category,
                judgement=judgement,
                source="feedback",
                reason=(
                    f"過去に確認済みの「{known.matched_comment}」と"
                    f"類似度{known.similarity:.2f}で一致したため、前回と同じ判断を適用しました"
                ),
            )

        llm_result = self._llm_classifier.classify(comment)
        return HybridResult(
            comment=comment,
            category=llm_result.category,
            judgement=llm_result.judgement,
            source="llm",
            reason=llm_result.reason,
        )

    def classify_batch(self, comments: list[str]) -> list[HybridResult]:
        return [self.classify(c) for c in comments]


if __name__ == "__main__":
    classifier = HybridClassifier()
    classifier.fit()

    test_comments = [
        "才能なさすぎ、動画作るのやめたら？",
        "さすが人間性豊かですね(笑)",
        "今日の動画も面白かったです！",
    ]

    print("=" * 70)
    print("ハイブリッド判定結果")
    print("=" * 70)
    for result in classifier.classify_batch(test_comments):
        print(f"\nコメント: 「{result.comment}」")
        print(f"  判定: {result.judgement}（カテゴリ: {result.category}, 情報源: {result.source}）")
        print(f"  理由: {result.reason}")
