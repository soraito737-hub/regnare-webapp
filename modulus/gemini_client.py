"""
Gemini APIの共通ヘルパー
==========================
クライアント生成と埋め込み関数を、判定系モジュール間で使い回す。
"""

import os

from google import genai
from google.genai import types

EMBEDDING_MODEL = "gemini-embedding-001"


def make_client(api_key: str | None = None) -> genai.Client:
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "環境変数 GEMINI_API_KEY が設定されていません。\n"
            "設定してから再度実行してください。"
        )
    return genai.Client(api_key=api_key)


def embed_text(client: genai.Client, text: str) -> list[float]:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
    )
    return result.embeddings[0].values
