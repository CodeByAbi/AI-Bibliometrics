"""Online Query Embedding Service.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/09 Tech Stack.md §2, docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from typing import Any

from backend.app.core.config import get_settings
from backend.app.core.errors import AppException
from backend.app.core.http import get_http_client
from backend.app.core.logging import logger

_st_model: Any | None = None
_st_lock = asyncio.Lock()

# Observable state of the preferred local path. The health endpoint used to
# report the embedding service as "ready" based purely on the Ollama probe,
# which stays green while this model is still being materialised — so a
# container that had just started looked healthy while the next VectorRoute
# request was still going to block on the load. Measured on this deployment:
# 167,679 ms with an empty HF cache, 12,012 ms with a warm cache (the volume
# in docker-compose.yml), ~150-450 ms once resident.
_LOCAL_MODEL_UNSET = "not_started"
_LOCAL_MODEL_LOADING = "loading"
_LOCAL_MODEL_LOADED = "loaded"
_LOCAL_MODEL_UNAVAILABLE = "unavailable"
_st_state: str = _LOCAL_MODEL_UNSET

# P3 server-* (Fase B7): bounded TTL cache for repeat query embeddings —
# seed/replay queries skip the CPU/HTTP encode entirely. Key includes model
# + dim so a config change can never serve stale-dimension vectors.
_QUERY_CACHE_MAX_ENTRIES = 256
_QUERY_CACHE_TTL_S = 3600.0
_query_cache: OrderedDict = OrderedDict()


def _query_cache_get(key: tuple) -> tuple | None:
    """Return ``(vector_copy, backend)`` on fresh hit, else None."""
    try:
        expires_at, vector, backend = _query_cache.pop(key)
    except KeyError:
        return None
    if time.monotonic() >= expires_at:
        return None
    _query_cache[key] = (expires_at, vector, backend)  # reinsert = most-recent
    return list(vector), backend


def _query_cache_put(key: tuple, vector: list[float], backend: str) -> None:
    """Store a copy; evict least-recently-used entries beyond the bound."""
    while len(_query_cache) >= _QUERY_CACHE_MAX_ENTRIES:
        _query_cache.popitem(last=False)
    _query_cache[key] = (time.monotonic() + _QUERY_CACHE_TTL_S, list(vector), backend)


def validate_embedding_vector(vector: list[float], expected_dim: int) -> None:
    """Validate embedding dims are finite floats (guards pgvector literal build)."""
    if len(vector) != expected_dim:
        raise EmbeddingError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {len(vector)}",
            details={"expected": expected_dim, "actual": len(vector)},
        )
    for v in vector:
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise EmbeddingError(
                "Embedding contains non-finite value (NaN/Inf); refusing to build SQL literal.",
                details={"expected": expected_dim},
            )


class EmbeddingError(AppException):
    """Exception raised when query embedding generation fails (503, not 500)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=message,
            error_type="embedding_service_unavailable",
            status_code=503,
            details=details,
        )


def _load_sentence_transformer(model_name: str) -> Any:
    """Load SentenceTransformer model instance on CPU with safetensors."""
    from sentence_transformers import SentenceTransformer

    logger.info("Loading local SentenceTransformer model '%s'...", model_name)
    return SentenceTransformer(model_name, model_kwargs={"use_safetensors": True})


def _encode_local_sync(model: Any, text: str) -> list[float]:
    """Synchronous CPU encoding of single query text."""
    embedding = model.encode(text, normalize_embeddings=False)
    if hasattr(embedding, "tolist"):
        return embedding.tolist()
    return list(embedding)


async def _embed_via_ollama(text: str, host: str, model_name: str, timeout_s: int) -> list[float]:
    """Generate embedding vector via Ollama HTTP API."""
    from backend.app.core.retry import is_retriable_http_status, with_retry

    base_url = host.rstrip("/")
    ollama_model = "bge-m3" if "bge-m3" in model_name.lower() else model_name

    # P3 server-*: shared client (TCP keep-alive); timeout stays per-request.
    client = get_http_client()
    req_timeout = float(timeout_s)
    # Try newer /api/embed first (single attempt + fallback; retry applies
    # to the legacy endpoint below so total latency stays bounded).
    try:
        resp = await client.post(
            f"{base_url}/api/embed",
            json={"model": ollama_model, "input": text},
            timeout=req_timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
        elif is_retriable_http_status(resp.status_code):
            logger.warning("Ollama /api/embed transient HTTP %s; trying /api/embeddings", resp.status_code)
        else:
            logger.warning("Ollama /api/embed HTTP %s (non-retriable); trying /api/embeddings", resp.status_code)
    except Exception as exc:
        logger.debug("Ollama /api/embed attempt failed, falling back to /api/embeddings: %s", exc)

    # Fallback to /api/embeddings with one transient CONNECT retry.
    #
    # A TIMEOUT is not retried (P0-B). Query embedding already has two worst
    # cases that dwarf a retry: a cold bge-m3 load measures 29.9-185.8 s and a
    # warm call 154-185 ms. Retrying a timeout therefore either doubles an
    # already-failed cold load or adds a second full timeout to a warm miss,
    # and the identical retry cannot beat the same model on the same box. The
    # caller degrades to a structured not_found / embedding error instead.
    async def _post_legacy():
        return await client.post(
            f"{base_url}/api/embeddings",
            json={"model": ollama_model, "prompt": text},
            timeout=req_timeout,
        )

    settings = get_settings()
    resp = await with_retry(
        _post_legacy,
        max_attempts=1 + max(0, settings.ollama_max_retries),
        operation="ollama-embeddings",
        retry_on_timeout=False,
    )
    if resp.status_code != 200:
        raise EmbeddingError(
            f"Ollama embedding request failed with HTTP {resp.status_code}",
            details={"host": base_url, "model": ollama_model, "status": resp.status_code},
        )
    data = resp.json()
    embedding = data.get("embedding")
    if not embedding or not isinstance(embedding, list):
        raise EmbeddingError(
            "Ollama returned invalid embedding format",
            details={"data_keys": list(data.keys())},
        )
    return embedding


async def generate_query_embedding_with_backend(query: str) -> tuple[list[float], str]:
    """Generate query embedding, also reporting which backend served it.

    Returns ``(vector, backend)`` where ``backend`` is ``"local"``
    (SentenceTransformer CPU) or ``"ollama"`` (HTTP fallback). Phase 4
    audit D1: the dual-path contract is kept for availability, but the
    serving backend is now observable so threshold-gate calibration drift
    during fallback can be attributed (``embedding_backend`` debug field).
    """
    clean_query = query.strip()
    if not clean_query:
        raise EmbeddingError("Cannot embed empty query text.")

    settings = get_settings()
    expected_dim = settings.embedding_dimension
    model_name = settings.embedding_model

    cache_key = (clean_query, model_name, expected_dim)
    cached = _query_cache_get(cache_key)
    if cached is not None:
        return cached

    global _st_model, _st_state
    vector: list[float] | None = None
    backend = "local"
    local_err: Exception | None = None

    # 1. Try local SentenceTransformer in threadpool
    try:
        if _st_model is None:
            async with _st_lock:
                if _st_model is None:
                    _st_state = _LOCAL_MODEL_LOADING
                    _st_model = await asyncio.to_thread(
                        _load_sentence_transformer, model_name
                    )
                    _st_state = _LOCAL_MODEL_LOADED
        vector = await asyncio.to_thread(_encode_local_sync, _st_model, clean_query)
    except Exception as exc:
        if _st_state != _LOCAL_MODEL_LOADED:
            _st_state = _LOCAL_MODEL_UNAVAILABLE
        local_err = exc
        logger.warning(
            "Local SentenceTransformer embedding failed (%s), attempting Ollama fallback...",
            exc,
        )

    # 2. Fallback to Ollama if local failed
    if vector is None:
        backend = "ollama"
        try:
            vector = await _embed_via_ollama(
                text=clean_query,
                host=settings.ollama_host,
                model_name=model_name,
                timeout_s=settings.ollama_timeout_s,
            )
        except Exception as ollama_exc:
            logger.error(
                "Both local SentenceTransformer and Ollama embedding failed. Local: %s, Ollama: %s",
                local_err,
                ollama_exc,
            )
            raise EmbeddingError(
                f"Failed to generate query embedding: local error '{local_err}', Ollama error '{ollama_exc}'",
                details={"local_error": str(local_err), "ollama_error": str(ollama_exc)},
            ) from ollama_exc

    # 3. Validate dimension + finiteness (guards pgvector literal build)
    validate_embedding_vector(vector, expected_dim)

    _query_cache_put(cache_key, vector, backend)
    return vector, backend


async def generate_query_embedding(query: str) -> list[float]:
    """Generate 1024-dimensional dense float vector for search query.

    Attempts local SentenceTransformer first, falling back to Ollama endpoint.

    Encoding contract (locked with offline batch): raw query text with
    ``normalize_embeddings=False``, matching ``scripts/embed_chunks.py``
    (which encodes ``Title: {title}\\nAbstract: {chunk}`` the same way).
    Query-side prefixing is intentionally NOT added here so the live
    ``>= 0.65`` cosine gate stays calibrated to the stored vectors.
    """
    vector, _ = await generate_query_embedding_with_backend(query)
    return vector


def clear_embedding_model_cache() -> None:
    """Clear cached SentenceTransformer instance (for unit testing)."""
    global _st_model, _st_state
    _st_model = None
    _st_state = _LOCAL_MODEL_UNSET
    _query_cache.clear()


def local_embedding_model_state() -> str:
    """Report the preferred local embedding path's state, without loading it.

    One of ``"loaded"``, ``"loading"``, ``"unavailable"``, ``"not_started"``.
    Never triggers the (expensive) load itself — it is a status probe for
    ``/api/v1/health``, which must stay cheap.
    """
    if _st_model is not None:
        return _LOCAL_MODEL_LOADED
    return _st_state


async def prewarm_embedding_model() -> str:
    """Load and cache the query-embedding model outside the request path.

    ``generate_query_embedding_with_backend`` already caches the model in a
    module global, but it does so lazily — on the *first* VectorRoute request.
    Measured on this deployment that first call cost 29.9-185.8 s (the
    SentenceTransformer is fetched from the HuggingFace Hub and its 391 weight
    tensors are then materialised), against 154-185 ms once warm. The cost was
    therefore being charged to a user request.

    Called from the FastAPI lifespan so the download and load overlap startup
    instead of the first question. Safe to call when the model is already
    cached (returns immediately) and when it cannot be loaded at all: the
    per-request path still has the Ollama bge-m3 fallback, so a pre-warm failure
    degrades to that rather than to an error.

    Returns the backend that ended up serving embeddings: ``"local"`` when the
    SentenceTransformer is resident, ``"ollama"`` when only the HTTP fallback
    is available.
    """
    global _st_model, _st_state
    if _st_model is not None:
        return "local"
    settings = get_settings()
    try:
        async with _st_lock:
            if _st_model is None:
                _st_state = _LOCAL_MODEL_LOADING
                _st_model = await asyncio.to_thread(
                    _load_sentence_transformer, settings.embedding_model
                )
                _st_state = _LOCAL_MODEL_LOADED
        logger.info(
            "Embedding model '%s' pre-warmed (%d dims).",
            settings.embedding_model,
            settings.embedding_dimension,
        )
        return "local"
    except Exception as exc:  # noqa: BLE001 - availability probe, never fatal
        _st_state = _LOCAL_MODEL_UNAVAILABLE
        logger.warning(
            "Embedding model pre-warm failed (%s); VectorRoute will fall back to "
            "the Ollama '%s' endpoint per request until the model is available.",
            exc,
            settings.embedding_model,
        )
        return "ollama"
