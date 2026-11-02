"""
Google OAuthまわりのヘルパー。
webapp/streamlit_app_redesign.py の get_flow() / build_youtube_service() 等と
同じロジックだが、st.secrets の代わりに config.py の環境変数を使う。
"""
import base64
import hashlib
import json
import time

import google_auth_httplib2
from cryptography.fernet import Fernet, InvalidToken
import httplib2
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, REDIRECT_URI, SCOPES, SESSION_SECRET


def get_flow(code_verifier: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI, autogenerate_code_verifier=False
    )
    if code_verifier:
        flow.code_verifier = code_verifier
    return flow


# セッションCookieは署名されるだけで中身は誰でもデコードできるため、
# トークンはFernet(AES)で暗号化してから入れる。client_secretはCookieに入れない。
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SESSION_SECRET.encode()).digest()))


def encrypt_credentials(credentials: Credentials) -> str:
    payload = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "scopes": credentials.scopes,
    }
    return _fernet.encrypt(json.dumps(payload).encode()).decode()


def decrypt_credentials(encrypted: str) -> Credentials | None:
    try:
        data = json.loads(_fernet.decrypt(encrypted.encode()))
    except InvalidToken:
        return None
    creds = Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=data["scopes"],
    )
    # アクセストークンが切れていればリフレッシュする
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    return creds


def build_youtube_service(credentials: Credentials, timeout: int = 30):
    authed_http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))
    return build("youtube", "v3", http=authed_http, cache_discovery=False)


def execute_with_retry(request, max_retries: int = 4):
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
