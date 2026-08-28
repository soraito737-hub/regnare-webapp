"""
100件テストデータセットでの分類実験スクリプト
==============================================
data/test_dataset_100.json を使い、embedding_classifier.py の
現行閾値(THRESHOLD_MATCH=0.60, THRESHOLD_GRAY=0.45)での性能を評価し、
結果をJSONで出力する。
"""

import json
from pathlib import Path

from embedding_classifier import CommentClassifier, THRESHOLD_MATCH, THRESHOLD_GRAY
from evaluate_thresholds import RawResult, evaluate, run_grid_search


def load_dataset(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    dataset_path = Path(__file__).parent.parent / "data" / "test_dataset_100.json"
    test_data = load_dataset(str(dataset_path))
    print(f"{len(test_data)}件のテストコメントを読み込みました")

    print("教師データをベクトル化中...")
    classifier = CommentClassifier()
    classifier.fit()

    print("100件のコメントを分類中...")
    raw_results: list[RawResult] = []
    per_comment = []
    for item in test_data:
        result = classifier.classify(item["comment"])
        raw_results.append(RawResult(
            comment=item["comment"],
            true_category=item["true_category"],
            true_judgement=item["true_judgement"],
            predicted_category=result.category,
            similarity=result.similarity,
        ))
        per_comment.append({
            "comment": item["comment"],
            "true_category": item["true_category"],
            "true_judgement": item["true_judgement"],
            "predicted_category": result.category,
            "predicted_judgement": result.judgement,
            "similarity": result.similarity,
            "matched_example": result.matched_example,
        })

    current_scores = evaluate(raw_results, THRESHOLD_MATCH, THRESHOLD_GRAY)
    grid_scores = run_grid_search(raw_results)

    output = {
        "n": len(test_data),
        "current_thresholds": {"match": THRESHOLD_MATCH, "gray": THRESHOLD_GRAY},
        "current_scores": current_scores,
        "grid_search_top10": grid_scores[:10],
        "per_comment": per_comment,
    }

    out_path = Path(__file__).parent.parent / "data" / "experiment_100_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n結果を {out_path} に保存しました")
    print(json.dumps(current_scores, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
