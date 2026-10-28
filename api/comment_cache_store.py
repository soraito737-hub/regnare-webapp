"""
コメント単位の判定結果の永続化。

【なぜ必要か】process_videoは以前、動画を開くたびに全コメントをLLM分類+
エンベディング類似度チェックにかけ直していた。同じコメント本文なら結果は
変わらないので、何度も判定するのはGemini APIコストの無駄になる。
ここでcomment_id単位の判定結果をJSONに永続化し、一度判定したコメントは
二度と分類・類似度チェックを行わない(新しく届いたコメントだけを判定する)。

【トレードオフ】この永続化により、初期設定やマークを後から変更しても、
すでに判定済みの古いコメントは自動では再判定されない。ユーザー自身が
「視聴者コメントに戻す」で明示的に直したものだけ、その場でキャッシュが
書き換わる(comments.pyのrestore_comment参照)。

【YouTube API Services利用規約への対応】YouTube Data API経由で取得した
コメント本文・投稿者情報は、取得から30日以内にAPIから再取得するか削除する
必要がある。ここでは「30日経過したエントリをキャッシュから削除し、次回
アクセス時にAPIから再取得・再判定させる」方式でこれを満たす。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_TTL_DAYS = 30


def _path(user_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return DATA_DIR / f"comment_cache_{safe_id}.json"


def _is_expired(entry: dict) -> bool:
    cached_at = entry.get("cached_at")
    if not cached_at:
        # タイムスタンプがない旧形式のデータは、次回save時に付与されるまで
        # 一旦保持する(いきなり全削除して不具合に見えるのを避けるため)。
        return False
    try:
        cached_dt = datetime.fromisoformat(cached_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - cached_dt > timedelta(days=CACHE_TTL_DAYS)


def load_comment_cache(user_id: str) -> dict:
    path = _path(user_id)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        cache = json.load(f)
    expired_keys = [k for k, v in cache.items() if _is_expired(v)]
    if expired_keys:
        for k in expired_keys:
            del cache[k]
        save_comment_cache(user_id, cache)
    return cache


def save_comment_cache(user_id: str, cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    for entry in cache.values():
        # 既存のタイムスタンプは上書きしない(=「最初にAPIから取得した時刻」を保持する。
        # マーク変更等、内容に関係ない更新のたびに期限が延長されてしまうことを防ぐ)。
        entry.setdefault("cached_at", now)
    with open(_path(user_id), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
