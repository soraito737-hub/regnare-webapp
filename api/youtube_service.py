"""
YouTube Data API呼び出し。webapp/streamlit_app_redesign.py の同名関数と同じロジック。
"""
from google.oauth2.credentials import Credentials

from oauth import build_youtube_service, execute_with_retry

VIDEOS_PAGE_SIZE = 50
MAX_COMMENTS_PER_VIDEO = 200


def get_channel_info(credentials: Credentials) -> dict | None:
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.channels().list(part="contentDetails", mine=True))
    items = resp.get("items", [])
    if not items:
        return None
    return {
        "channel_id": items[0]["id"],
        "uploads_playlist_id": items[0]["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def list_channel_videos(credentials: Credentials, playlist_id: str, page_token: str | None = None,
                         max_results: int = VIDEOS_PAGE_SIZE) -> tuple[list[dict], str | None]:
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.playlistItems().list(
        part="snippet", playlistId=playlist_id, maxResults=max_results, pageToken=page_token,
    ))
    videos = []
    for item in resp.get("items", []):
        sn = item["snippet"]
        resource_id = sn.get("resourceId", {})
        if resource_id.get("kind") != "youtube#video":
            continue
        videos.append({
            "video_id": resource_id["videoId"],
            "title": sn.get("title", "(タイトル取得不可)"),
            "thumbnail": sn.get("thumbnails", {}).get("default", {}).get("url", ""),
            "published_at": sn.get("publishedAt", ""),
        })
    return videos, resp.get("nextPageToken")


def fetch_comments(credentials: Credentials, video_id: str, max_results: int = MAX_COMMENTS_PER_VIDEO) -> list[dict]:
    service = build_youtube_service(credentials)
    comments = []
    page_token = None
    for _ in range(50):
        if len(comments) >= max_results:
            break
        request = service.commentThreads().list(
            part="snippet", videoId=video_id,
            maxResults=min(max_results - len(comments), 100),
            textFormat="plainText", pageToken=page_token,
        )
        response = execute_with_retry(request)
        for item in response.get("items", []):
            top_comment = item["snippet"]["topLevelComment"]
            snippet = top_comment["snippet"]
            comments.append({
                "author": snippet["authorDisplayName"],
                "author_channel_id": snippet.get("authorChannelId", {}).get("value"),
                "text": snippet["textDisplay"],
                "comment_id": top_comment["id"],
                "published_at": snippet.get("publishedAt", ""),
            })
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return comments[:max_results]


def hide_comment_on_youtube(credentials: Credentials, comment_id: str) -> None:
    service = build_youtube_service(credentials)
    execute_with_retry(service.comments().setModerationStatus(id=comment_id, moderationStatus="heldForReview"))


def ban_author_on_youtube(credentials: Credentials, comment_id: str) -> None:
    service = build_youtube_service(credentials)
    execute_with_retry(
        service.comments().setModerationStatus(id=comment_id, moderationStatus="rejected", banAuthor=True)
    )
