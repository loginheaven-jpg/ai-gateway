"""Shared outbound clients, reused across requests so connections (and TLS
sessions) are kept alive instead of re-established per call.

All are created lazily inside the running event loop and closed on shutdown.
Assumes the single uvicorn worker / single event loop the gateway runs on.
"""
import os
from typing import Dict, Optional, Tuple

import httpx
from google import genai
from openai import AsyncOpenAI

_http: Optional[httpx.AsyncClient] = None
_openai: Dict[Tuple, AsyncOpenAI] = {}
_genai: Dict[Tuple, genai.Client] = {}


def get_http_client() -> httpx.AsyncClient:
    """Shared httpx client. Pass the per-call timeout on each request."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=60.0),
            # No cap on concurrent connections (Claude streams can hold one for
            # minutes); keep a modest idle pool for reuse.
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=50),
        )
    return _http


def get_async_openai(api_key: str, base_url: Optional[str], timeout: httpx.Timeout) -> AsyncOpenAI:
    key = (api_key, base_url, timeout.read, timeout.connect)
    client = _openai.get(key)
    if client is None:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        _openai[key] = client
    return client


def get_genai_client(api_key: str, timeout_ms: Optional[int] = None) -> genai.Client:
    key = (api_key, timeout_ms)
    client = _genai.get(key)
    if client is None:
        http_options = {}
        if timeout_ms:
            http_options["timeout"] = timeout_ms
        if os.getenv("GEMINI_BASE_URL"):  # tests / proxies only; unset in production
            http_options["base_url"] = os.environ["GEMINI_BASE_URL"]
        client = genai.Client(api_key=api_key, http_options=http_options or None)
        _genai[key] = client
    return client


async def close_clients() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None
    for client in _openai.values():
        await client.close()
    _openai.clear()
    _genai.clear()
