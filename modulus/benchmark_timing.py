"""
本番と同じ条件(ペース調整なし・逐次呼び出し)で100件処理し、実際の所要時間を計測する。
"""
import time
from pathlib import Path
import json

from llm_classifier import LLMCommentClassifier

dataset_path = Path(__file__).parent.parent / "data" / "test_dataset_random_100.json"
with open(dataset_path, "r", encoding="utf-8") as f:
    test_data = json.load(f)

classifier = LLMCommentClassifier()

start = time.time()
for i, item in enumerate(test_data, 1):
    classifier.classify(item["comment"])
    if i % 10 == 0:
        elapsed = time.time() - start
        print(f"{i}件処理済み / 経過時間: {elapsed:.1f}秒 / 平均: {elapsed/i:.2f}秒・件", flush=True)

total = time.time() - start
print(f"\n合計: {len(test_data)}件 / {total:.1f}秒 ({total/60:.1f}分) / 平均{total/len(test_data):.2f}秒・件")
