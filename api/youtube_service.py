"""
YouTube Data API呼び出し。webapp/streamlit_app_redesign.py の同名関数と同じロジック。
"""
import re

from google.oauth2.credentials import Credentials

from oauth import build_youtube_service, execute_with_retry

VIDEOS_PAGE_SIZE = 50
MAX_COMMENTS_PER_VIDEO = 200

_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _parse_duration_seconds(iso_duration: str) -> int | None:
    m = _DURATION_RE.match(iso_duration or "")
    if not m:
        return None
    hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def _format_duration(iso_duration: str) -> str:
    """YouTubeのISO8601形式(例: "PT12M36S")を "12:36" / "1:02:10" 形式に変換する。"""
    m = _DURATION_RE.match(iso_duration or "")
    if not m:
        return ""
    hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def get_channel_info(credentials: Credentials) -> dict | None:
    service = build_youtube_service(credentials)
    resp = execute_with_retry(service.channels().list(part="snippet,contentDetails", mine=True))
    items = resp.get("items", [])
    if not items:
        return None
    thumbs = items[0].get("snippet", {}).get("thumbnails", {})
    avatar = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
    return {
        "channel_id": items[0]["id"],
        "uploads_playlist_id": items[0]["contentDetails"]["relatedPlaylists"]["uploads"],
        "avatar_url": avatar.get("url", ""),
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


def get_video_details(credentials: Credentials, video_ids: list[str]) -> dict[str, dict]:
    """動画ごとの視聴回数・コメント総数・動画時間・高解像度サムネイルを取得する。
    Gemini判定は呼ばない、YouTube APIのみの軽量な呼び出し。
    videos().list(part="snippet,statistics,contentDetails") はID数に関わらず1回1クオータなので、
    ホーム画面表示のたびに呼んでもコスト・レート的な負担はほぼない。
    playlistItems().list のサムネイルは解像度が低いことがあるため、
    ここで maxres/standard/high の高解像度版があれば差し替える。
    """
    if not video_ids:
        return {}
    service = build_youtube_service(credentials)
    details: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = execute_with_retry(
            service.videos().list(part="snippet,statistics,contentDetails", id=",".join(batch))
        )
        for item in resp.get("items", []):
            stats = item.get("statistics", {})
            thumbs = item.get("snippet", {}).get("thumbnails", {})
            best_thumb = (
                thumbs.get("maxres") or thumbs.get("standard")
                or thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
            )
            iso_duration = item.get("contentDetails", {}).get("duration", "")
            duration_seconds = _parse_duration_seconds(iso_duration)
            width, height = best_thumb.get("width"), best_thumb.get("height")
            # ショート動画かどうかは公開APIに明確なフラグが無いため、
            # サムネイルが縦長(height > width)であることを主な判定基準にする。
            # サムネイルの縦横情報が取れない場合は、60秒以下という簡易な条件で代用する。
            if width and height:
                is_short = height > width
            else:
                is_short = duration_seconds is not None and duration_seconds <= 60
            details[item["id"]] = {
                "view_count": int(stats.get("viewCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "thumbnail": best_thumb.get("url", ""),
                "duration": _format_duration(iso_duration),
                "is_short": is_short,
            }
    return details


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


def unhide_comment_on_youtube(credentials: Credentials, comment_id: str) -> None:
    """YouTube側で保留(非表示)にしていたコメントを、通常公開の状態に戻す。
    「YouTubeにも報告して非表示」から「Regskipだけの非表示」へ降格する際に使う。
    """
    service = build_youtube_service(credentials)
    execute_with_retry(service.comments().setModerationStatus(id=comment_id, moderationStatus="published"))


def reply_to_comment(credentials: Credentials, comment_id: str, text: str) -> dict:
    """トップレベルコメントに返信する。parentIdには返信先のトップレベルコメントIDを渡す
    (YouTube Data APIの仕様上、返信は常にスレッドの最上位コメントにぶら下がる)。
    """
    service = build_youtube_service(credentials)
    response = execute_with_retry(
        service.comments().insert(
            part="snippet",
            body={"snippet": {"parentId": comment_id, "textOriginal": text}},
        )
    )
    snippet = response["snippet"]
    return {
        "comment_id": response["id"],
        "author": snippet.get("authorDisplayName", ""),
        "text": snippet.get("textDisplay", text),
        "published_at": snippet.get("publishedAt", ""),
    }


def ban_author_on_youtube(credentials: Credentials, comment_id: str) -> None:
    service = build_youtube_service(credentials)
    execute_with_retry(
        service.comments().setModerationStatus(id=comment_id, moderationStatus="rejected", banAuthor=True)
    )
