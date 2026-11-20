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
必要がある。動画を開くたびにAPIから取り直した本文で上書きしてrefreshed_atを
更新し(videos.pyのprocess_video参照)、30日間一度も取り直せなかったエントリ
(YouTube上で消えたコメント、長く開いていない動画のコメント)だけを削除する
(Regskipを開かない利用者の分はretention.pyが毎日まとめて消す)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))

from json_store import load_json, safe_name, save_json  # noqa: E402
from retention import cache_entry_expired, now_iso  # noqa: E402


def _name(user_id: str) -> str:
    return safe_name("comment_cache", user_id)


def mark_refreshed(entry: dict) -> None:
    entry["refreshed_at"] = now_iso()


def load_comment_cache(user_id: str) -> dict:
    cache = load_json(_name(user_id), {})
    expired_keys = [k for k, v in cache.items() if cache_entry_expired(v)]
    if expired_keys:
        for k in expired_keys:
            del cache[k]
        save_comment_cache(user_id, cache)
    return cache


def save_comment_cache(user_id: str, cache: dict) -> None:
    now = now_iso()
    for entry in cache.values():
        # 既存のタイムスタンプは上書きしない(=「最初にAPIから取得した時刻」を保持する。
        # マーク変更等、内容に関係ない更新のたびに期限が延長されてしまうことを防ぐ)。
        entry.setdefault("cached_at", now)
        entry.setdefault("refreshed_at", now)
    save_json(_name(user_id), cache)
