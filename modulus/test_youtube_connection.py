"""
YouTube連携モジュール 動作確認テスト
======================================
OAuth認証が正しく動くか、実際に自分のチャンネルのコメントを
少しだけ取得してみて確認する。

【実行方法】
py test_youtube_connection.py

初回実行時、ブラウザが自動で開き、Googleアカウントでのログインと
「このアプリにアクセスを許可しますか?」という確認画面が出ます。
「詳細」→「(アプリ名)に移動(安全ではないページ)」のような表示が
出ることがありますが、これは自分で作ったテスト用アプリだからです
(Googleの審査を受けていない、テスト段階のアプリのため)。
"""

from youtube_client import YouTubeClient

def main():
    print("YouTube連携モジュール 動作確認を開始します。")
    print("ブラウザが開いたら、テストユーザーに登録したGoogleアカウントでログインしてください。\n")

    client = YouTubeClient(credentials_path="client_secret.json")
    client.authenticate()

    print("\n認証に成功しました！")

    # 自分のチャンネルの直近の動画を取得してみる(要: チャンネルID)
    channel_id = input("動作確認したいチャンネルのチャンネルIDを入力してください: ").strip()

    video_ids = client.fetch_recent_video_ids(channel_id, max_videos=3)
    print(f"\n直近の動画ID: {video_ids}")

    if video_ids:
        print(f"\n最初の動画のコメントを5件だけ取得してみます...")
        comments = client.fetch_comments(video_ids[0], max_results=5)
        for c in comments:
            print(f"  - {c.author}: {c.text[:50]}")

    print("\n動作確認が完了しました。")


if __name__ == "__main__":
    main()
