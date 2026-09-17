"""
環境変数の読み込み。
api/.env (gitignore対象)に以下を設定して使う:

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
REDIRECT_URI=http://localhost:8000/api/auth/callback
GEMINI_API_KEY=...
SESSION_SECRET=適当なランダム文字列
FRONTEND_URL=http://localhost:5173
"""
import os

from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
