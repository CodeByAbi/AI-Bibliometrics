"""Ollama client and health inspection service.

Docs Reference: docs/06 Api Design.md §6, docs/09 Tech Stack.md §3.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import httpx

from backend.app.core.config import get_settings
from backend.app.core.http import get_http_client
from backend.app.core.logging import logger
from backend.app.models.health import EmbeddingServiceHealth, LLMServiceHealth


async def check_ollama_health() -> tuple[LLMServiceHealth, EmbeddingServiceHealth]:
    """Inspect Ollama server availability, model tags, and embedding service readiness."""
    settings = get_settings()
    host = settings.ollama_host.rstrip("/")
    timeout = settings.ollama_timeout_s

    available_models: List[str] = []
    connected = False
    error_msg: Optional[str] = None

    try:
        # P3 server-*: shared client (TCP keep-alive); timeout stays per-request.
        resp = await get_http_client().get(f"{host}/api/tags", timeout=timeout)
        if resp.status_code == 200:
            connected = True
            data = resp.json()
            models_data = data.get("models", [])
            available_models = [m.get("name", "") for m in models_data if isinstance(m, dict)]
        else:
            error_msg = f"Ollama HTTP {resp.status_code}"
    except httpx.ConnectError:
        error_msg = "Ollama daemon unreachable at host"
    except httpx.TimeoutException:
        error_msg = "Ollama connection timeout"
    except Exception as exc:
        error_msg = f"Ollama check error: {str(exc)}"

    llm_health = LLMServiceHealth(
        status="connected" if connected else "disconnected",
        model=settings.llm_model,
        provider="Ollama",
        available_models=available_models,
        error=error_msg if not connected else None,
    )

    # Embedding service status: ready if Ollama reachable (bge-m3), else unreachable.
    # Stored pgvector rows are probed separately by check_db_health().pgvector_ready.
    embed_health = EmbeddingServiceHealth(
        status="ready" if connected else "unreachable",
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        source="pgvector (stored) + Ollama/HF",
    )

    return llm_health, embed_health
