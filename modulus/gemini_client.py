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


# 埋め込みは件数が多いと1回の呼び出しが遅くなる(200件を100件ずつ:約5秒、25件ずつ同時:約4秒)ため、
# 25件ずつに分けて同時に送る
EMBED_BATCH_SIZE = 25


def embed_texts(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """複数の文をまとめて埋め込む。1件ずつ呼ぶより速く、順番は入力と同じ。"""
    from concurrent.futures import ThreadPoolExecutor

    chunks = [texts[i : i + EMBED_BATCH_SIZE] for i in range(0, len(texts), EMBED_BATCH_SIZE)]
    if not chunks:
        return []

    def _embed(chunk: list[str]) -> list[list[float]]:
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=chunk,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
        )
        return [e.values for e in result.embeddings]

    with ThreadPoolExecutor(max_workers=min(len(chunks), 10)) as executor:
        return [v for vecs in executor.map(_embed, chunks) for v in vecs]
