import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from config import FRONTEND_URL
from oauth import credentials_from_dict, credentials_to_dict, get_flow
from youtube_service import get_channel_info

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
def login(request: Request):
    """Google認可URLを発行する。Reactはこのレスポンスのurlにブラウザを遷移させる。"""
    flow = get_flow()
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    request.session["code_verifier"] = flow.code_verifier
    return {"url": auth_url}


@router.get("/callback")
def callback(request: Request, code: str):
    """Googleからのリダイレクトを受け取り、認証情報をセッションに保存してReact側へ戻す。"""
    flow = get_flow(code_verifier=request.session.get("code_verifier"))
    flow.fetch_token(code=code)
    credentials = flow.credentials

    info = get_channel_info(credentials)
    if info is None:
        return RedirectResponse(f"{FRONTEND_URL}/?error=no_channel")

    request.session["credentials"] = credentials_to_dict(credentials)
    request.session["channel_id"] = info["channel_id"]
    request.session["uploads_playlist_id"] = info["uploads_playlist_id"]
    return RedirectResponse(f"{FRONTEND_URL}/home")


@router.get("/me")
def me(request: Request):
    """ログイン状態の確認。Reactが起動時にこれを呼んで、ログイン画面かホーム画面か決める。"""
    if "credentials" not in request.session:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "channel_id": request.session.get("channel_id"),
    }


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


def get_credentials(request: Request):
    """他のルーターから使う: セッションから認証情報を復元する。"""
    data = request.session.get("credentials")
    if data is None:
        return None
    creds = credentials_from_dict(data)
    # リフレッシュされていたらセッション側も更新する
    request.session["credentials"] = credentials_to_dict(creds)
    return creds
