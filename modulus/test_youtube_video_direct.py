"""
YouTube連携モジュール 動作確認テスト(動画ID直接指定版)
=========================================================
チャンネル経由ではなく、動画IDを直接指定してコメント取得を試す。
エラーの原因切り分け用。

【実行方法】
py test_youtube_video_direct.py
"""

from youtube_client import YouTubeClient


def main():
    print("YouTube連携モジュール 動作確認(動画ID直接指定)を開始します。\n")

    client = YouTubeClient(credentials_path="client_secret.json")
    client.authenticate()

    print("\n認証に成功しました！")

    video_id = input("動作確認したい動画のIDを入力してください: ").strip()

    print(f"\n動画ID「{video_id}」のコメントを5件だけ取得してみます...")
    comments = client.fetch_comments(video_id, max_results=5)

    if not comments:
        print("コメントが0件でした(コメント欄が無効になっている可能性があります)")
    else:
        for c in comments:
            print(f"  - {c.author}: {c.text[:50]}")

    print("\n動作確認が完了しました。")


if __name__ == "__main__":
    main()
