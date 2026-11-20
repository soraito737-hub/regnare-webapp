"""
利用者ごとのデータ(判定キャッシュ・初期設定・マーク)を名前つきのJSONとして読み書きする。

【なぜ必要か】Renderのサーバーはスリープ・デプロイのたびに作り直され、data/に
書いたファイルが消える。環境変数DATABASE_URL(NeonのPostgres)があるときは
データベースに保存して消えないようにする。ないとき(手元での開発)は従来どおり
data/<name>.json に保存するので、既存のファイルもそのまま読める。
"""
import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

_pool = None
_pool_lock = threading.Lock()


def safe_name(prefix: str, user_id: str) -> str:
    safe_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return f"{prefix}_{safe_id}"


def _database_url() -> str | None:
    # .envの読み込み(load_dotenv)より先にimportされても拾えるよう、毎回環境変数を見る
    return os.environ.get("DATABASE_URL")


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool

            # Neonは数分使われないと休止して接続が切れるため、貸し出す前に生きているか確かめる
            _pool = ConnectionPool(
                _database_url(),
                min_size=1,
                max_size=5,
                check=ConnectionPool.check_connection,
                open=True,
            )
            with _pool.connection() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS json_store ("
                    " name TEXT PRIMARY KEY,"
                    " data JSONB NOT NULL,"
                    " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
        return _pool


def load_json(name: str, default=None):
    if _database_url():
        with _get_pool().connection() as conn:
            row = conn.execute("SELECT data FROM json_store WHERE name = %s", (name,)).fetchone()
        return row[0] if row else default
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_names(prefix: str) -> list[str]:
    if _database_url():
        with _get_pool().connection() as conn:
            rows = conn.execute("SELECT name FROM json_store WHERE starts_with(name, %s)", (prefix,)).fetchall()
        return [r[0] for r in rows]
    return [p.stem for p in DATA_DIR.glob(f"{prefix}*.json")]


def save_json(name: str, data) -> None:
    if _database_url():
        from psycopg.types.json import Jsonb

        with _get_pool().connection() as conn:
            conn.execute(
                "INSERT INTO json_store (name, data, updated_at) VALUES (%s, %s, now())"
                " ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
                (name, Jsonb(data)),
            )
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
