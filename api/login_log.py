"""
ログインの記録。何人が使っているか(利用者数・その日にログインした人数)を運営チームが
知るためだけに使う。

2種類の記録を持つ。
・login_log_<チャンネルID>: 1チャンネルにつき1行。初めて/最後にログインした日時と、
  ログインした日(日本時間)だけ。チャンネルIDはYouTubeから受け取ったデータなので、
  YouTube API Services Developer Policies III.E.4.c(30日ルール)に沿って、
  最後のログイン(=YouTubeから取り直した時)から30日たったら retention.py が消す。
・login_summary: 人数だけの集計(これまでの人数、日ごとの人数)。チャンネルIDを含まない。

コメントやアカウントの情報は入れない。ほかのデータと同じく暗号化して保存する(json_store.py)。
30日以上ログインしなかった人がまた来たときは、記録が消えているので「初めての人」として数える。
"""
from datetime import datetime, timedelta, timezone

from json_store import list_names, load_json, safe_name, save_json

PREFIX = "login_log"  # 名前は login_log_<チャンネルID>
SUMMARY_NAME = "login_summary"  # login_log_ で始めない(30日で消す対象に入れない)
DAILY_DAYS_KEPT = 400
JST = timezone(timedelta(hours=9))


def _name(channel_id: str) -> str:
    return safe_name(PREFIX, channel_id)


def record_login(channel_id: str) -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(JST).date().isoformat()
    log = load_json(_name(channel_id), None)
    is_new = not log
    log = log or {}
    first_today = today not in log.get("days", [])
    # 30日より前の日付は消える行にしかないので、30日分あれば足りる
    days = sorted({*log.get("days", []), today})[-31:]
    save_json(
        _name(channel_id),
        {
            "first_login": log.get("first_login") or now.isoformat(),
            "last_login": now.isoformat(),
            "days": days,
        },
    )

    summary = load_json(SUMMARY_NAME, None) or {"total_users": 0, "total_logins": 0, "daily": {}}
    summary["total_logins"] += 1
    day = summary["daily"].setdefault(today, {"users": 0, "new_users": 0})
    if is_new:
        summary["total_users"] += 1
        day["new_users"] += 1
    if first_today:
        day["users"] += 1
    summary["daily"] = dict(sorted(summary["daily"].items())[-DAILY_DAYS_KEPT:])
    save_json(SUMMARY_NAME, summary)


def login_stats(days_shown: int = 14) -> dict:
    """人数だけを返す(チャンネルIDは返さない)。"""
    today = datetime.now(JST).date()
    summary = load_json(SUMMARY_NAME, None) or {"total_users": 0, "total_logins": 0, "daily": {}}
    logs = [load_json(n, None) for n in list_names(PREFIX + "_")]
    logs = [log for log in logs if log]

    def since(days: int) -> str:
        return (today - timedelta(days=days - 1)).isoformat()

    def active(days: int) -> int:
        return sum(1 for log in logs if any(d >= since(days) for d in log.get("days", [])))

    daily = []
    for i in range(days_shown - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        counts = summary["daily"].get(d, {"users": 0, "new_users": 0})
        daily.append({"date": d, **counts})

    return {
        "total_users": summary["total_users"],
        "total_logins": summary["total_logins"],
        "active_today": active(1),
        "active_7days": active(7),
        "active_30days": active(30),
        "new_7days": sum(v["new_users"] for d, v in summary["daily"].items() if d >= since(7)),
        "daily": daily,
    }
