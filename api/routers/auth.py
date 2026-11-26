import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import os

from config import ALLOWED_CHANNEL_IDS, FRONTEND_URL
from login_log import record_login
from oauth import decrypt_credentials, encrypt_credentials, get_flow
from youtube_service import get_channel_info

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _admin_channel_ids() -> set[str]:
    return {c.strip() for c in os.environ.get("ADMIN_CHANNEL_IDS", "").split(",") if c.strip()}


def is_allowed_channel(channel_id: str | None) -> bool:
    return not ALLOWED_CHANNEL_IDS or channel_id in ALLOWED_CHANNEL_IDS


@router.get("/login")
def login(request: Request):
    """Google認可URLを発行する。Reactはこのレスポンスのurlにブラウザを遷移させる。"""
    flow = get_flow()
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    request.session["code_verifier"] = flow.code_verifier
    return {"url": auth_url}


def _connect_error(reason: str) -> RedirectResponse:
    # 連携に失敗したときは、利用規約の画面ではなく、理由と次にすることを説明する画面に戻す
    return RedirectResponse(f"{FRONTEND_URL}/connect/error?reason={reason}")


@router.get("/callback")
def callback(request: Request, code: str | None = None, error: str | None = None):
    """Googleからのリダイレクトを受け取り、認証情報をセッションに保存してReact側へ戻す。"""
    # Googleの画面で「キャンセル」を押した・許可しなかった場合は、codeが付かずerrorが付いて戻ってくる
    if error or not code:
        return _connect_error("denied")
    try:
        flow = get_flow(code_verifier=request.session.get("code_verifier"))
        flow.fetch_token(code=code)
        credentials = flow.credentials
        info = get_channel_info(credentials)
    except Exception as e:
        # 原因はRenderのLogsで確かめられるように残す(トークン等の中身は出さない)
        print(f"[auth] 連携に失敗: {type(e).__name__}", flush=True)
        return _connect_error("failed")

    if info is None:
        return _connect_error("no_channel")
    if not is_allowed_channel(info["channel_id"]):
        # 招待していないチャンネルの認証情報はセッションに保存せずに戻す
        return _connect_error("not_invited")

    request.session["credentials"] = encrypt_credentials(credentials)
    request.session["channel_id"] = info["channel_id"]
    request.session["uploads_playlist_id"] = info["uploads_playlist_id"]
    request.session["avatar_url"] = info.get("avatar_url", "")
    try:
        record_login(info["channel_id"])
    except Exception as e:
        # 記録に失敗しても、ログイン自体は止めない
        print(f"[auth] ログインの記録に失敗: {type(e).__name__}", flush=True)
    return RedirectResponse(f"{FRONTEND_URL}/home")


@router.get("/me")
def me(request: Request):
    """ログイン状態の確認。Reactが起動時にこれを呼んで、ログイン画面かホーム画面か決める。"""
    if not isinstance(request.session.get("credentials"), str) or not is_allowed_channel(
        request.session.get("channel_id")
    ):
        request.session.clear()
        return {"logged_in": False}
    if "avatar_url" not in request.session:
        # avatar_url対応前にログインしたセッション向けのフォールバック。1回だけ取得してキャッシュする。
        credentials = get_credentials(request)
        info = get_channel_info(credentials) if credentials else None
        request.session["avatar_url"] = info.get("avatar_url", "") if info else ""
    return {
        "logged_in": True,
        "channel_id": request.session.get("channel_id"),
        "avatar_url": request.session.get("avatar_url", ""),
        "is_admin": request.session.get("channel_id") in _admin_channel_ids(),
    }


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


def get_credentials(request: Request):
    """他のルーターから使う: セッションから認証情報を復元する。"""
    data = request.session.get("credentials")
    # 暗号化前の旧形式(dict)のセッションは平文のトークンを含むため破棄し、再ログインさせる。
    if not isinstance(data, str):
        request.session.clear()
        return None
    # 許可リストから外したチャンネルは、ログイン済みのセッションもその場で無効にする
    if not is_allowed_channel(request.session.get("channel_id")):
        request.session.clear()
        return None
    creds = decrypt_credentials(data)
    if creds is None:
        request.session.clear()
        return None
    # リフレッシュされていたらセッション側も更新する
    request.session["credentials"] = encrypt_credentials(creds)
    return creds
