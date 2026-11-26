"""
ログインの記録。何人が使っているか(利用者数・その日にログインした人数)を運営チームが
知るためだけに使う。

1チャンネルにつき1行(login_log_<チャンネルID>)だけを持ち、中身は
・初めてログインした日時
・最後にログインした日時
・ログインした回数
・ログインした日(日本時間の日付、直近LOGIN_DAYS_KEPT日分)
だけ。コメントやアカウントの情報は入れない。ほかのデータと同じく暗号化して保存する
(json_store.py)。
"""
from datetime import datetime, timedelta, timezone

from json_store import list_names, load_json, safe_name, save_json

PREFIX = "login_log"
LOGIN_DAYS_KEPT = 90
JST = timezone(timedelta(hours=9))


def _name(channel_id: str) -> str:
    return safe_name(PREFIX, channel_id)


def record_login(channel_id: str) -> None:
    now = datetime.now(timezone.utc)
    today = now.astimezone(JST).date().isoformat()
    log = load_json(_name(channel_id), None) or {}
    days = [d for d in log.get("days", []) if d != today] + [today]
    save_json(
        _name(channel_id),
        {
            "first_login": log.get("first_login") or now.isoformat(),
            "last_login": now.isoformat(),
            "login_count": int(log.get("login_count", 0)) + 1,
            "days": sorted(days)[-LOGIN_DAYS_KEPT:],
        },
    )


def login_stats(days_shown: int = 14) -> dict:
    """人数だけを返す(チャンネルIDは返さない)。"""
    today = datetime.now(JST).date()
    logs = [load_json(n, None) for n in list_names(PREFIX + "_")]
    logs = [log for log in logs if log]

    def first_day(log) -> str:
        return datetime.fromisoformat(log["first_login"]).astimezone(JST).date().isoformat()

    def within(days: int):
        return (today - timedelta(days=days - 1)).isoformat()

    daily = []
    for i in range(days_shown - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily.append(
            {
                "date": d,
                "users": sum(1 for log in logs if d in log.get("days", [])),
                "new_users": sum(1 for log in logs if first_day(log) == d),
            }
        )

    return {
        "total_users": len(logs),
        "total_logins": sum(int(log.get("login_count", 0)) for log in logs),
        "active_today": sum(1 for log in logs if today.isoformat() in log.get("days", [])),
        "active_7days": sum(1 for log in logs if any(d >= within(7) for d in log.get("days", []))),
        "active_30days": sum(1 for log in logs if any(d >= within(30) for d in log.get("days", []))),
        "new_7days": sum(1 for log in logs if first_day(log) >= within(7)),
        "daily": daily,
    }
