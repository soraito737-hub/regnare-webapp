"""
並列版の速度を実測する(webapp/streamlit_app.pyのclassify_comments_parallelと同じロジック)。
"""
import concurrent.futures
import json
import time
from pathlib import Path

from llm_classifier import LLMCommentClassifier


def classify_comment_safe(classifier, text):
    try:
        return classifier.classify(text)
    except Exception as e:
        return type("R", (), {"category": None, "judgement": "グレー", "reason": f"error: {e}"})()


def classify_comments_parallel(classifier, texts, max_workers=6):
    results = [None] * len(texts)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(classify_comment_safe, classifier, t): i for i, t in enumerate(texts)}
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
    return results


dataset_path = Path(__file__).parent.parent / "data" / "test_dataset_random_100.json"
with open(dataset_path, "r", encoding="utf-8") as f:
    test_data = json.load(f)[:30]

classifier = LLMCommentClassifier()

start = time.time()
results = classify_comments_parallel(classifier, [item["comment"] for item in test_data], max_workers=6)
elapsed = time.time() - start

ok = sum(1 for r in results if r.judgement in ("該当", "グレー", "非該当"))
print(f"{len(test_data)}件 / {elapsed:.1f}秒 / 平均{elapsed/len(test_data):.2f}秒・件 / 正常応答{ok}件")
