"""右下の吹き出しから送られたお問い合わせを、運営チームのメールに届ける。

データベースには保存しない(届けるだけ)。送れなかったときは画面にエラーを返し、
書いた内容が消えずに残るようにする。

Renderの環境変数に以下を設定して使う(値はRenderの画面にだけ入れ、コードやチャットに書かない):
SUPPORT_SMTP_USER=送信に使うGmailアドレス
SUPPORT_SMTP_APP_PASSWORD=そのGmailのアプリパスワード(16文字)
SUPPORT_TO_EMAIL=受け取るメールアドレス(省略するとSUPPORT_SMTP_USERに届く)
"""
import os
import smtplib
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from routers.auth import get_credentials
from youtube_service import get_channel_info

router = APIRouter(prefix="/api", tags=["support"])

MAX_MESSAGE_LENGTH = 2000
# いたずらで大量に送られないよう、1チャンネルにつき1時間に5件まで
RATE_LIMIT_COUNT = 5
RATE_LIMIT_WINDOW_SECONDS = 60 * 60

_sent_times: dict[str, list[float]] = {}
_lock = threading.Lock()


class SupportIn(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE_LENGTH)
    reply_email: str = Field(default="", max_length=254)
    page: str = Field(default="", max_length=200)


def _smtp_settings() -> tuple[str, str, str] | None:
    user = os.environ.get("SUPPORT_SMTP_USER", "").strip()
    password = os.environ.get("SUPPORT_SMTP_APP_PASSWORD", "").replace(" ", "")
    to = os.environ.get("SUPPORT_TO_EMAIL", "").strip() or user
    if not user or not password:
        return None
    return user, password, to


def _check_rate_limit(channel_id: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _sent_times.get(channel_id, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent) >= RATE_LIMIT_COUNT:
            raise HTTPException(status_code=429, detail="送信の回数が多すぎます。1時間ほどあけてから送ってください")
        recent.append(now)
        _sent_times[channel_id] = recent


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1] and "\n" not in value and "\r" not in value


@router.post("/support")
def send_support_message(body: SupportIn, request: Request):
    credentials = get_credentials(request)
    channel_id = request.session.get("channel_id")
    if credentials is None or channel_id is None:
        raise HTTPException(status_code=401, detail="ログインしてから送ってください")

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="お問い合わせの内容を入力してください")
    reply_email = body.reply_email.strip()
    if reply_email and not _looks_like_email(reply_email):
        raise HTTPException(status_code=400, detail="メールアドレスの形が正しくありません")

    settings = _smtp_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="お問い合わせの受付を準備中です。時間をおいてもう一度お試しください")
    smtp_user, smtp_password, to_email = settings

    _check_rate_limit(channel_id)

    # 誰から来たか分かるよう、チャンネル名をYouTubeから取る(取れなくてもIDだけで送る)
    try:
        info = get_channel_info(credentials)
        channel_title = (info or {}).get("title", "") or "(取得できませんでした)"
    except Exception:
        channel_title = "(取得できませんでした)"
    channel_title = " ".join(channel_title.split())[:100]  # 件名に改行が入らないようにする

    msg = EmailMessage()
    msg["Subject"] = f"[Regskip お問い合わせ] {channel_title}"
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    if reply_email:
        msg["Reply-To"] = reply_email
    msg.set_content(
        "Regskipの吹き出しからお問い合わせが届きました。\n\n"
        f"チャンネル名: {channel_title}\n"
        f"チャンネルID: {channel_id}\n"
        f"チャンネルURL: https://www.youtube.com/channel/{channel_id}\n"
        f"返信先メール: {reply_email or '(入力なし)'}\n"
        f"送信した画面: {body.page or '(不明)'}\n"
        "\n---- 内容 ----\n"
        f"{message}\n"
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
    except Exception as e:
        # 原因はRenderのLogsで確かめられるように残す(本文は出さない)
        print(f"[support] メール送信に失敗: {type(e).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="送信できませんでした。時間をおいてもう一度お試しください")
    return {"ok": True}
