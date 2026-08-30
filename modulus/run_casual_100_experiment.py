"""
雑なアンチコメント100件でのLLM判定 実験スクリプト
=====================================================
data/test_dataset_casual_100.json(タイポ・スラング・皮肉混じりの
よりリアルなコメント)を使い、llm_classifier.py の精度を検証する。
"""

import json
import time
from pathlib import Path

from llm_classifier import LLMCommentClassifier

# 有料枠のレート制限に収めるための間隔(無料枠より大幅に緩和されている)
REQUEST_INTERVAL_SECONDS = 2


def load_dataset(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate(per_comment: list[dict]) -> dict:
    tp = fp = tn = fn = 0
    category_correct = 0
    category_total = 0

    for item in per_comment:
        predicted_positive = item["predicted_judgement"] in ("該当", "グレー")
        true_positive = item["true_judgement"] in ("該当", "グレー")

        if predicted_positive and true_positive:
            tp += 1
        elif predicted_positive and not true_positive:
            fp += 1
        elif not predicted_positive and not true_positive:
            tn += 1
        else:
            fn += 1

        if true_positive:
            category_total += 1
            if item["predicted_category"] == item["true_category"]:
                category_correct += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(per_comment)
    category_accuracy = category_correct / category_total if category_total > 0 else 0.0

    return {
        "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(f1, 3), "accuracy": round(accuracy, 3),
        "category_accuracy": round(category_accuracy, 3),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


def main(limit: int | None = None):
    dataset_path = Path(__file__).parent.parent / "data" / "test_dataset_casual_100.json"
    test_data = load_dataset(str(dataset_path))
    if limit is not None:
        test_data = test_data[:limit]
    print(f"{len(test_data)}件の雑なコメントを読み込みました")

    out_path = Path(__file__).parent.parent / "data" / "experiment_casual_100_results.json"

    # 既存の進捗があれば読み込んで、続きから再開する(クォータ制限で
    # 途中失敗しても、前回分のAPI呼び出しを無駄にしないため)
    per_comment: list[dict] = []
    if out_path.exists():
        with open(out_path, "r", encoding="utf-8") as f:
            per_comment = json.load(f).get("per_comment", [])
    done_comments = {item["comment"] for item in per_comment}

    classifier = LLMCommentClassifier()

    print("LLMで判定中...")
    for i, item in enumerate(test_data, 1):
        if item["comment"] in done_comments:
            continue
        result = classifier.classify(item["comment"])
        per_comment.append({
            "comment": item["comment"],
            "true_category": item["true_category"],
            "true_judgement": item["true_judgement"],
            "predicted_category": result.category,
            "predicted_judgement": result.judgement,
            "reason": result.reason,
        })
        # 1件ごとに保存(クォータ制限で途中終了しても結果が残る)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"n": len(per_comment), "per_comment": per_comment}, f, ensure_ascii=False, indent=2)
        print(f"  {i}/{len(test_data)}件完了: {item['comment'][:20]} -> {result.judgement}/{result.category}")
        if i < len(test_data):
            time.sleep(REQUEST_INTERVAL_SECONDS)

    scores = evaluate(per_comment)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": len(per_comment), "scores": scores, "per_comment": per_comment}, f, ensure_ascii=False, indent=2)

    print(f"\n結果を {out_path} に保存しました({len(per_comment)}件分)")
    print(json.dumps(scores, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=n)
