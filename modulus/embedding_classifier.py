"""
判定モジュール（柱I） - Gemini Embeddingによる5カテゴリ分類
=============================================================
【実行方法(宙さんのPCで)】
1. export GEMINI_API_KEY="発行したAPIキー" (Windowsは $env:GEMINI_API_KEY="...")
2. pip install google-genai (または py -m pip install google-genai)
3. python embedding_classifier.py (Windowsは py embedding_classifier.py)

このモジュールは他のモジュール(pipeline_example.py等)から
import して呼び出すことを想定している。
"""

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

CATEGORIES = ["外見", "人間性", "活動クオリティ", "モラル・マナー説教", "プライバシー"]
Judgement = Literal["該当", "グレー", "非該当"]

# 類似度の閾値(初期値。検証データで要調整。要件定義 3-2参照)
THRESHOLD_MATCH = 0.60
THRESHOLD_GRAY = 0.45


@dataclass
class ClassificationResult:
    comment: str
    category: str            # 最も類似度が高かったカテゴリ
    similarity: float
    judgement: Judgement
    matched_example: str      # 最も近い教師データ(根拠として表示用)


class CommentClassifier:
    def __init__(self, training_data_path: str | None = None, api_key: str | None = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "環境変数 GEMINI_API_KEY が設定されていません。\n"
                "設定してから再度実行してください。"
            )
        self.client = genai.Client(api_key=api_key)

        # 教師データの読み込み
        if training_data_path is None:
            training_data_path = Path(__file__).parent.parent / "data" / "training_examples.json"
        with open(training_data_path, "r", encoding="utf-8") as f:
            self.training_examples: dict[str, list[str]] = json.load(f)

        self._category_vectors: dict[str, list[list[float]]] = {}
        self._is_fitted = False

    def _embed(self, text: str) -> list[float]:
        result = self.client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
        )
        return result.embeddings[0].values

    def fit(self) -> None:
        """教師データ50件を全てベクトル化しておく(初回のみ実行すればよい)"""
        print("教師データをベクトル化中...(初回のみ、少し時間がかかります)")
        for category, examples in self.training_examples.items():
            self._category_vectors[category] = [self._embed(ex) for ex in examples]
        self._is_fitted = True
        print("完了。")

    def classify(self, comment: str) -> ClassificationResult:
        if not self._is_fitted:
            raise RuntimeError("先に fit() を呼び出して教師データをベクトル化してください")

        comment_vector = self._embed(comment)

        best_category = None
        best_similarity = -1.0
        best_example = None

        for category, vectors in self._category_vectors.items():
            examples = self.training_examples[category]
            sims = cosine_similarity([comment_vector], vectors)[0]
            max_idx = sims.argmax()
            if sims[max_idx] > best_similarity:
                best_similarity = sims[max_idx]
                best_category = category
                best_example = examples[max_idx]

        
        if best_category == "非該当":
            judgement: Judgement = "非該当"
        elif best_similarity >= THRESHOLD_MATCH:
            judgement = "該当"
        elif best_similarity >= THRESHOLD_GRAY:
            judgement = "グレー"
        else:
            judgement = "非該当"

        return ClassificationResult(
            comment=comment,
            category=best_category,
            similarity=round(float(best_similarity), 4),
            judgement=judgement,
            matched_example=best_example,
        )

    def classify_batch(self, comments: list[str]) -> list[ClassificationResult]:
        return [self.classify(c) for c in comments]


if __name__ == "__main__":
    classifier = CommentClassifier()
    classifier.fit()

    test_comments = [
        "才能なさすぎ、動画作るのやめたら？",
        "顔が生理的に無理、二度と出てくるな",
        "今日の動画も面白かったです！",
        "編集もう少し工夫できそうですね",
        "声質が独特で好きです",
        "案件表記をもっと分かりやすくしてほしいです",
        "調子乗ってるけど落ち目になったら一瞬だよ",
    ]

    print("\n" + "=" * 70)
    print("判定結果")
    print("=" * 70)
    for result in classifier.classify_batch(test_comments):
        print(f"\nコメント: 「{result.comment}」")
        print(f"  判定: {result.judgement}（カテゴリ: {result.category}, 類似度: {result.similarity}）")
        print(f"  根拠(最も近い教師データ): 「{result.matched_example}」")
