"""
YouTube API Services利用規約の30日ルール(III.E.4.c)に沿って、古いデータを消す。

YouTubeから取ったコメント本文・投稿者情報は、取得から30日以内に取り直すか削除する。
コメントの判定キャッシュとマーク(似ているコメントの記録)は、動画を開いて元のコメントが
YouTubeにまだあれば refreshed_at を更新する。30日間一度も取り直せなかったものだけを、
ここで丸ごと削除する。利用者がRegskipを開かなくなっても消えるよう、サーバーの
起動時と、その後24時間ごとに全員分を点検する(start_cleanup_thread)。

マークは本文から作った埋め込みを含むため、本文だけ消して残すことはしない
(規約III.E.4.hの「APIデータから新しいデータを作らない」に触れないよう安全側に倒す)。
"""
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from json_store import encrypt_plaintext_rows, list_names, load_json, save_json

RETENTION_DAYS = 30
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_expired(timestamp: str | None) -> bool:
    if not timestamp:
        return False
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - dt > timedelta(days=RETENTION_DAYS)


def cache_entry_expired(entry: dict) -> bool:
    return is_expired(entry.get("refreshed_at") or entry.get("cached_at"))


def cleanup_expired() -> None:
    for name in list_names("comment_cache_"):
        cache = load_json(name, {})
        kept = {k: v for k, v in cache.items() if not cache_entry_expired(v)}
        if len(kept) != len(cache):
            save_json(name, kept)
            print(f"[retention] {name}: {len(cache) - len(kept)}件削除", flush=True)

    for name in list_names("personal_similarity_"):
        marks = load_json(name, [])
        changed = False
        kept = []
        for m in marks:
            # 日時を持たない旧データは、今日を起点に30日数える
            if not m.get("refreshed_at"):
                m["refreshed_at"] = now_iso()
                changed = True
            if is_expired(m["refreshed_at"]):
                changed = True
                continue
            kept.append(m)
        if changed:
            save_json(name, kept)
            if len(kept) != len(marks):
                print(f"[retention] {name}: マーク{len(marks) - len(kept)}件削除", flush=True)


def _loop() -> None:
    try:
        n = encrypt_plaintext_rows()
        if n:
            print(f"[encryption] 平文で保存されていた{n}件を暗号化", flush=True)
    except Exception:
        print("[encryption] 平文データの暗号化に失敗", flush=True)
        traceback.print_exc()
    while True:
        try:
            cleanup_expired()
        except Exception:
            print("[retention] 点検に失敗", flush=True)
            traceback.print_exc()
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def start_cleanup_thread() -> None:
    # 起動(ポートを開く処理)を待たせないよう、別スレッドで動かす
    threading.Thread(target=_loop, name="retention-cleanup", daemon=True).start()
