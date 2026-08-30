"""
YouTube連携モジュール
======================
YouTube Data API v3を使い、コメントを取得するのみを行う(読み取り専用)。
非表示・削除・ブロックなど書き込み操作は一切実装しない(要件定義 3-3, Non-Goals参照)。

【事前準備】
1. Google Cloud Console で YouTube Data API v3 を有効化
2. OAuth 2.0 クライアントID を発行(スコープ: youtube.readonly のみ)
3. pip install google-api-python-client google-auth-oauthlib
"""

from dataclasses import dataclass
from datetime import datetime, timezone

# 読み取り専用スコープのみ要求する(書き込みスコープは要求しない)
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

@dataclass
class Comment:
    comment_id: str
    video_id: str
    author: str
    text: str
    published_at: datetime


class YouTubeClient:
    """
    使い方:
        client = YouTubeClient(credentials_path="client_secret.json")
        client.authenticate()
        comments = client.fetch_comments(video_id="xxxx", max_results=100)
    """

    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path
        self._service = None

    def authenticate(self) -> None:
        """OAuth認証フローを実行する(初回はブラウザでの許可画面が開く)

        prompt='consent' を指定し、毎回必ず同意画面を表示させる。
        これにより、以前の認証が省略されてスコープ不足になる問題を防ぐ。
        """
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
        credentials = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
        )

        # --- 診断: 実際に許可されたスコープを表示する ---
        print("\n[診断情報]")
        print(f"  リクエストしたスコープ: {SCOPES}")
        print(f"  実際に許可されたスコープ: {credentials.scopes}")
        if not credentials.scopes or "youtube" not in str(credentials.scopes):
            print("  ⚠ YouTube関連のスコープが許可されていません！")
        print()

        self._service = build("youtube", "v3", credentials=credentials)

    def fetch_comments(self, video_id: str, max_results: int = 100) -> list[Comment]:
        """指定した動画のコメントを取得する(1回の呼び出しで1ユニット消費)"""
        if self._service is None:
            raise RuntimeError("先に authenticate() を呼び出してください")

        comments: list[Comment] = []
        request = self._service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=min(max_results, 100),
            textFormat="plainText",
        )

        while request is not None and len(comments) < max_results:
            response = request.execute()
            for item in response.get("items", []):
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                comments.append(Comment(
                    comment_id=item["snippet"]["topLevelComment"]["id"],
                    video_id=video_id,
                    author=snippet["authorDisplayName"],
                    text=snippet["textDisplay"],
                    published_at=datetime.fromisoformat(
                        snippet["publishedAt"].replace("Z", "+00:00")
                    ),
                ))
            request = self._service.commentThreads().list_next(request, response)

        return comments[:max_results]

    def fetch_recent_video_ids(self, channel_id: str, max_videos: int = 5) -> list[str]:
        """チャンネルの直近の動画IDを取得する(検証モード用。要件定義 3-4参照)"""
        if self._service is None:
            raise RuntimeError("先に authenticate() を呼び出してください")

        channel_response = self._service.channels().list(
            part="contentDetails", id=channel_id
        ).execute()
        uploads_playlist_id = (
            channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        )

        playlist_response = self._service.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_videos,
        ).execute()

        return [
            item["contentDetails"]["videoId"]
            for item in playlist_response.get("items", [])
        ]


# NOTE: このファイル単体では実行できません(OAuth認証情報が必要なため)。
# pipeline_example.py から呼び出す想定です。
