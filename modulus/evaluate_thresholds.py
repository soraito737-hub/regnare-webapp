"""
閾値自動評価スクリプト
======================
test_dataset.json(正解ラベル付き)を使って、複数の閾値パターンを試し、
どの閾値が一番「適合率(Precision)」「再現率(Recall)」のバランスが良いかを
自動計算する。

【実行方法(宙さんのPCで)】
1. export GEMINI_API_KEY="発行したAPIキー"
2. python evaluate_thresholds.py

出力される表を見て、embedding_classifier.py の
THRESHOLD_MATCH / THRESHOLD_GRAY をどの値にするか判断する。
"""

import json
from pathlib import Path
from dataclasses import dataclass

from embedding_classifier import CommentClassifier


@dataclass
class RawResult:
    """閾値を適用する前の、生の類似度データ"""
    comment: str
    true_category: str | None
    true_judgement: str
    predicted_category: str
    similarity: float


def load_test_dataset(path: str | None = None) -> list[dict]:
    if path is None:
        path = Path(__file__).parent.parent / "data" / "test_dataset.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_raw_results(classifier: CommentClassifier, test_data: list[dict]) -> list[RawResult]:
    """全テストコメントについて、生の類似度スコアだけを先に計算しておく
    (閾値を変えるたびにAPIを呼び直すのは無駄なので、一度だけ計算して使い回す)
    """
    raw_results = []
    for item in test_data:
        result = classifier.classify(item["comment"])
        raw_results.append(RawResult(
            comment=item["comment"],
            true_category=item["true_category"],
            true_judgement=item["true_judgement"],
            predicted_category=result.category,
            similarity=result.similarity,
        ))
    return raw_results


def apply_threshold(raw: RawResult, threshold_match: float, threshold_gray: float) -> str:
    """生の類似度に、指定した閾値を当てはめて judgement を決める"""
    if raw.similarity >= threshold_match:
        return "該当"
    elif raw.similarity >= threshold_gray:
        return "グレー"
    else:
        return "非該当"


def evaluate(raw_results: list[RawResult], threshold_match: float, threshold_gray: float) -> dict:
    """指定した閾値での適合率・再現率・正解率を計算する

    ここでは「該当」と「グレー」を合わせて"アンチとして検知した"とみなし、
    "非該当(安全なコメントを安全と正しく判定できたか)"との2値評価をする。
    """
    tp = fp = tn = fn = 0
    category_correct = 0
    category_total = 0

    for raw in raw_results:
        predicted = apply_threshold(raw, threshold_match, threshold_gray)
        predicted_positive = predicted in ("該当", "グレー")
        true_positive = raw.true_judgement in ("該当", "グレー")

        if predicted_positive and true_positive:
            tp += 1
        elif predicted_positive and not true_positive:
            fp += 1
        elif not predicted_positive and not true_positive:
            tn += 1
        else:
            fn += 1

        # カテゴリ自体が合っているかも別途集計(該当・グレーのケースのみ)
        if true_positive:
            category_total += 1
            if raw.predicted_category == raw.true_category:
                category_correct += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(raw_results)
    category_accuracy = category_correct / category_total if category_total > 0 else 0.0

    return {
        "threshold_match": threshold_match,
        "threshold_gray": threshold_gray,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(accuracy, 3),
        "category_accuracy": round(category_accuracy, 3),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


def run_grid_search(raw_results: list[RawResult]) -> list[dict]:
    """複数の閾値パターンを総当たりで試す"""
    match_candidates = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
    gray_candidates = [0.45, 0.50, 0.55, 0.60]

    all_scores = []
    for match in match_candidates:
        for gray in gray_candidates:
            if gray >= match:
                continue  # グレーの下限は該当ラインより低くなければ意味がない
            scores = evaluate(raw_results, match, gray)
            all_scores.append(scores)

    return sorted(all_scores, key=lambda x: x["f1"], reverse=True)


if __name__ == "__main__":
    print("テストデータを読み込み中...")
    test_data = load_test_dataset()
    print(f"  {len(test_data)}件のテストコメントを読み込みました")

    print("\nGemini Embeddingで類似度を計算中...(少し時間がかかります)")
    classifier = CommentClassifier()
    classifier.fit()
    raw_results = get_raw_results(classifier, test_data)

    print("\n各閾値パターンでの適合率・再現率を評価中...")
    all_scores = run_grid_search(raw_results)

    print("\n" + "=" * 90)
    print("閾値評価結果(F1スコアが高い順、上位10件)")
    print("=" * 90)
    print(f"{'該当閾値':>8} {'グレー閾値':>10} {'適合率':>8} {'再現率':>8} {'F1':>8} {'正解率':>8} {'カテゴリ一致率':>12}")
    for s in all_scores[:10]:
        print(
            f"{s['threshold_match']:>8.2f} {s['threshold_gray']:>10.2f} "
            f"{s['precision']:>8.3f} {s['recall']:>8.3f} {s['f1']:>8.3f} "
            f"{s['accuracy']:>8.3f} {s['category_accuracy']:>12.3f}"
        )

    best = all_scores[0]
    print("\n" + "=" * 90)
    print(f"推奨閾値: THRESHOLD_MATCH = {best['threshold_match']}, THRESHOLD_GRAY = {best['threshold_gray']}")
    print(f"  (適合率: {best['precision']*100:.1f}%, 再現率: {best['recall']*100:.1f}%, "
          f"正解率: {best['accuracy']*100:.1f}%)")
    print("=" * 90)
    print("\n※ 適合率70%が目標値でした。この結果を踏まえ、")
    print("  embedding_classifier.py の THRESHOLD_MATCH / THRESHOLD_GRAY を")
    print("  上記の推奨値に書き換えてください。")
