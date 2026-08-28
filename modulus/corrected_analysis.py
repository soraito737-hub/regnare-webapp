"""
experiment_100_results.json のキャッシュ済み類似度データを使い、
本番のclassify()と同じロジック(非該当カテゴリへの一致は無条件で非該当とする)
で正しく再評価する。APIは呼び出さない(追加コストゼロ)。
"""

import json
from pathlib import Path


def real_judgement(predicted_category: str, similarity: float, match: float, gray: float) -> str:
    if predicted_category == "非該当":
        return "非該当"
    if similarity >= match:
        return "該当"
    if similarity >= gray:
        return "グレー"
    return "非該当"


def evaluate(per_comment, match, gray):
    tp = fp = tn = fn = 0
    gray_count = 0
    for item in per_comment:
        predicted = real_judgement(item["predicted_category"], item["similarity"], match, gray)
        predicted_positive = predicted in ("該当", "グレー")
        true_positive = item["true_judgement"] in ("該当", "グレー")
        if predicted == "グレー":
            gray_count += 1
        if predicted_positive and true_positive:
            tp += 1
        elif predicted_positive and not true_positive:
            fp += 1
        elif not predicted_positive and not true_positive:
            tn += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(per_comment)
    return {
        "match": match, "gray": gray,
        "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(f1, 3), "accuracy": round(accuracy, 3),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn, "gray_predicted": gray_count,
    }


def main():
    path = Path(__file__).parent.parent / "data" / "experiment_100_results.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    per_comment = data["per_comment"]

    # 本番と同じ現行閾値での「正しい」評価
    current = evaluate(per_comment, 0.60, 0.45)
    print("=== 現行閾値(0.60/0.45)での正しい評価 ===")
    print(json.dumps(current, ensure_ascii=False, indent=2))

    # グリッドサーチ(キャッシュ済み類似度を使い回すのでAPI呼び出し不要)
    match_candidates = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    gray_candidates = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
    results = []
    for m in match_candidates:
        for g in gray_candidates:
            if g >= m:
                continue
            results.append(evaluate(per_comment, m, g))
    results.sort(key=lambda x: (x["f1"], x["accuracy"]), reverse=True)

    print("\n=== グリッドサーチ上位10件(正しいロジック) ===")
    for r in results[:10]:
        print(json.dumps(r, ensure_ascii=False))

    # カテゴリ誤分類の内訳(該当/グレーの50件のみ対象)
    print("\n=== カテゴリ誤分類の内訳 ===")
    confusions = {}
    for item in per_comment:
        if item["true_judgement"] in ("該当", "グレー") and item["predicted_category"] != item["true_category"]:
            key = f'{item["true_category"]} -> {item["predicted_category"]}'
            confusions.setdefault(key, []).append(item["comment"])
    for key, comments in confusions.items():
        print(f"\n{key} ({len(comments)}件)")
        for c in comments:
            print(f"  - {c}")

    out_path = Path(__file__).parent.parent / "data" / "corrected_analysis.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "current_corrected": current,
            "grid_search_top10": results[:10],
            "category_confusions": confusions,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n保存先: {out_path}")


if __name__ == "__main__":
    main()
