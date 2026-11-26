"""
利用者ごとのデータ(判定キャッシュ・初期設定・マーク)を名前つきのJSONとして読み書きする。

【なぜ必要か】Renderのサーバーはスリープ・デプロイのたびに作り直され、data/に
書いたファイルが消える。環境変数DATABASE_URL(NeonのPostgres)があるときは
データベースに保存して消えないようにする。ないとき(手元での開発)は従来どおり
data/<name>.json に保存するので、既存のファイルもそのまま読める。

【暗号化】環境変数DATA_ENCRYPTION_KEYがあるとき、中身を暗号化して {"enc": "..."} の形で
保存する。データベースの画面を開いてもコメント本文などは読めない。鍵はRenderの環境変数
にだけ置く。鍵をなくすと保存済みのデータは二度と読めないので、変えないこと。
暗号化前に保存された平文のデータもそのまま読め、次に保存したとき暗号化される
(encrypt_plaintext_rowsで一括で暗号化もできる)。
"""
import base64
import hashlib
import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

_pool = None
_pool_lock = threading.Lock()
_fernet_cache: tuple[str, object] | None = None


def _fernet():
    """鍵があればFernet(AES)を返す。どんな長さの文字列でも鍵にできるようSHA-256で32バイトにする。"""
    global _fernet_cache
    key = os.environ.get("DATA_ENCRYPTION_KEY")
    if not key:
        return None
    if _fernet_cache is None or _fernet_cache[0] != key:
        from cryptography.fernet import Fernet

        _fernet_cache = (key, Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())))
    return _fernet_cache[1]


def encryption_enabled() -> bool:
    return _fernet() is not None


def _is_encrypted(stored) -> bool:
    return isinstance(stored, dict) and set(stored) == {"enc"}


def _encode(data):
    f = _fernet()
    if f is None:
        return data
    return {"enc": f.encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()}


def _decode(stored):
    if not _is_encrypted(stored):
        return stored  # 暗号化前の平文データ
    f = _fernet()
    if f is None:
        raise RuntimeError("保存データは暗号化されていますが、DATA_ENCRYPTION_KEYが設定されていません")
    return json.loads(f.decrypt(stored["enc"].encode()))


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
        return _decode(row[0]) if row else default
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return _decode(json.load(f))


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
                (name, Jsonb(_encode(data))),
            )
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(_encode(data), f, ensure_ascii=False, indent=2)


def delete_json(name: str) -> None:
    if _database_url():
        with _get_pool().connection() as conn:
            conn.execute("DELETE FROM json_store WHERE name = %s", (name,))
        return
    (DATA_DIR / f"{name}.json").unlink(missing_ok=True)


def encrypt_plaintext_rows() -> int:
    """暗号化前に保存された平文の行を暗号化し直す。鍵がなければ何もしない。直した件数を返す。"""
    if not encryption_enabled() or not _database_url():
        return 0
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT name FROM json_store WHERE NOT (jsonb_typeof(data) = 'object' AND data ? 'enc')"
        ).fetchall()
    for (name,) in rows:
        save_json(name, load_json(name))
    return len(rows)
