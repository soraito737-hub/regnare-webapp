"""
実コメント × 判定モジュール 統合テスト
========================================
YouTubeから実際に取得したコメントを、Gemini Embeddingの5カテゴリ判定にかける。

【実行方法】
1. export GEMINI_API_KEY="発行したAPIキー" (Windowsは $env:GEMINI_API_KEY="...")
2. py test_full_pipeline.py
"""

from youtube_client import YouTubeClient
from embedding_classifier import CommentClassifier


def main():
    print("=" * 70)
    print("STEP 1: YouTubeから実際のコメントを取得")
    print("=" * 70)

    client = YouTubeClient(credentials_path="client_secret.json")
    client.authenticate()

    video_id = input("\n判定したい動画のIDを入力してください: ").strip()
    comments = client.fetch_comments(video_id, max_results=20)

    if not comments:
        print("コメントが取得できませんでした。")
        return

    print(f"\n{len(comments)}件のコメントを取得しました。")

    print("\n" + "=" * 70)
    print("STEP 2: Gemini Embeddingで5カテゴリ判定")
    print("=" * 70)

    classifier = CommentClassifier()
    classifier.fit()

    print("\n判定中...(コメント数が多いと少し時間がかかります)\n")

    results = []
    for c in comments:
        result = classifier.classify(c.text)
        results.append((c, result))

    print("=" * 70)
    print("判定結果")
    print("=" * 70)

    for comment, result in results:
        print(f"\n投稿者: {comment.author}")
        print(f"  コメント: 「{comment.text[:60]}」")
        print(f"  判定: {result.judgement}（カテゴリ: {result.category}, 類似度: {result.similarity}）")

    # カテゴリ別集計
    print("\n" + "=" * 70)
    print("カテゴリ別集計")
    print("=" * 70)
    from collections import Counter
    category_counts = Counter(
        r.category for _, r in results if r.judgement == "該当"
    )
    gray_count = sum(1 for _, r in results if r.judgement == "グレー")
    safe_count = sum(1 for _, r in results if r.judgement == "非該当")

    for category, count in category_counts.most_common():
        print(f"  {category}: {count}件")
    print(f"  確認待ち(グレー): {gray_count}件")
    print(f"  通常(非該当): {safe_count}件")


if __name__ == "__main__":
    main()
