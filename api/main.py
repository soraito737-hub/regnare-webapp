import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "modulus"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from config import FRONTEND_URL, IS_PRODUCTION, SESSION_SECRET  # noqa: E402
from json_store import encryption_enabled  # noqa: E402
from retention import start_cleanup_thread  # noqa: E402
from routers import admin, auth, profile, support, videos  # noqa: E402

# データベースに保存するなら、必ず暗号化する(運営者がデータベースを開いても本文を読めないように)。
# 鍵がないまま平文で保存してしまわないよう、起動させない。
if os.environ.get("DATABASE_URL") and not encryption_enabled():
    raise RuntimeError("DATABASE_URLを使うときはDATA_ENCRYPTION_KEYも設定してください")

app = FastAPI(title="Regskip API")


@app.on_event("startup")
def _start_retention_cleanup():
    # YouTubeのデータの30日ルール。Regskipを開かない利用者の分も消えるよう、起動時と毎日点検する
    start_cleanup_thread()

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_ALLOWED_ORIGIN = FRONTEND_URL.rstrip("/")


@app.middleware("http")
async def _reject_cross_site_writes(request: Request, call_next):
    """CSRF対策。ほかのサイトがログイン中の利用者のブラウザを使って、削除・返信・ブロックなどを
    勝手に送らせるのを防ぐ。ブラウザはサイトをまたぐPOST等に必ずOriginを付けるので、
    Regskipの画面以外のOriginなら断る(Originのないcurl等は利用者のCookieを持たないので通す)。"""
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != _ALLOWED_ORIGIN:
            return JSONResponse({"detail": "許可されていない送信元です"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="none" if IS_PRODUCTION else "lax",
    https_only=IS_PRODUCTION,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(videos.router)
app.include_router(support.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
