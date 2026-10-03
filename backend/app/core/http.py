"""Shared httpx AsyncClient singleton for outbound Ollama HTTP calls.

P3 server-* backend perf (Fase B6): one AsyncClient reuses TCP connections
(keep-alive) across Text-to-SQL, embedding, synthesis, and health probes
instead of paying a fresh connect handshake per request. Timeouts stay
per-request so each caller keeps its own budget (OLLAMA_TIMEOUT_S).
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return the shared client, creating it lazily on first use.

    Lazy creation keeps unit-test contexts (no lifespan) working; lifespan
    startup pre-warms it and shutdown closes it via ``close_http_client``.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http_client() -> None:
    """Close and drop the shared client (lifespan shutdown; idempotent)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
