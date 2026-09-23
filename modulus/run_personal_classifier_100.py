"""
personal_classifier.py(4次元判定)を、既存のアドバーサリアル100件データセットで
一括テストする。正解ラベルは旧タクソノミー(5分類/該当・グレー・非該当)のため
自動採点はできないが、分布と「明らかにおかしい」結果を目視確認するための
実験スクリプト。
"""
import concurrent.futures
import json
import time
from pathlib import Path

from personal_classifier import PersonalJudgmentClassifier, EmergencyType


def classify_safe(classifier, comment_id, text):
    try:
        j = classifier.classify(text, comment_id=comment_id)
        return {
            "comment": text,
            "categories": [c.value for c in j.categories],
            "surface_level": j.surface_level.value,
            "tatemae_pattern": j.tatemae_pattern.value,
            "emergency": j.emergency.value,
            "reasoning": j.reasoning,
        }
    except Exception as e:
        return {"comment": text, "error": str(e)}


def main():
    dataset_path = Path(__file__).parent.parent / "data" / "test_dataset_adversarial_100.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        test_data = json.load(f)

    classifier = PersonalJudgmentClassifier()

    start = time.time()
    results = [None] * len(test_data)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_idx = {
            executor.submit(classify_safe, classifier, str(i), item["comment"]): i
            for i, item in enumerate(test_data)
        }
        done = 0
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(test_data)}件完了")
    elapsed = time.time() - start

    out_path = Path(__file__).parent.parent / "data" / "experiment_personal_classifier_adversarial_100.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"n": len(results), "elapsed_sec": elapsed, "results": results}, f, ensure_ascii=False, indent=2)

    # --- 分布の集計 ---
    cat_counts = {}
    level_counts = {1: 0, 2: 0}
    pattern_counts = {}
    emergency_hits = []
    errors = []
    for r in results:
        if "error" in r:
            errors.append(r)
            continue
        for cat in r["categories"]:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        level_counts[r["surface_level"]] += 1
        pattern_counts[r["tatemae_pattern"]] = pattern_counts.get(r["tatemae_pattern"], 0) + 1
        if r["emergency"] != EmergencyType.NONE.value:
            emergency_hits.append(r)

    print(f"\n{len(test_data)}件 / {elapsed:.1f}秒 / 平均{elapsed/len(test_data):.2f}秒・件")
    print(f"エラー: {len(errors)}件")
    print(f"\nカテゴリ分布: {cat_counts}")
    print(f"レベル分布: {level_counts}")
    print(f"建前パターン分布: {pattern_counts}")
    print(f"\n緊急判定されたもの({len(emergency_hits)}件):")
    for r in emergency_hits:
        print(f"  [{r['emergency']}] 「{r['comment']}」")

    print(f"\n結果を {out_path} に保存しました")


if __name__ == "__main__":
    main()
