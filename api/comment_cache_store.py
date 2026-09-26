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
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def _path(user_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return DATA_DIR / f"comment_cache_{safe_id}.json"


def load_comment_cache(user_id: str) -> dict:
    path = _path(user_id)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_comment_cache(user_id: str, cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path(user_id), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
