"""FastAPI application entrypoint for AI-Bibliometrics API Gateway.

Docs Reference: docs/06 Api Design.md, docs/08 Security.md, docs/11 Roadmap.md (Fase 2).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from backend.app.core.config import get_settings
from backend.app.core.errors import register_error_handlers
from backend.app.core.http import close_http_client, get_http_client
from backend.app.core.logging import logger
from backend.app.core.middleware import RateLimitingMiddleware, RequestTracingMiddleware
from backend.app.db.pool import close_pool, init_pool
from backend.app.db.session_pool import close_session_pool, init_session_pool
from backend.app.routers.ask import router as ask_router
from backend.app.routers.health import router as health_router
from backend.app.routers.sessions import router as sessions_router
from backend.app.services.embedding import prewarm_embedding_model


async def _prewarm_embeddings() -> None:
    """Load the query-embedding model in the background during startup.

    Awaiting the model inline would hold ``app.state.startup`` for the whole
    HuggingFace fetch plus weight load, which measured 29.9-185.8 s on this
    deployment — long enough that an orchestrator would kill the container
    before it ever served a request. Running it as a detached task lets the
    gateway accept traffic immediately; a VectorRoute request arriving before
    the load finishes still resolves correctly, it just serialises on the same
    lock the pre-warm holds.
    """
    try:
        backend = await prewarm_embedding_model()
        logger.info("Embedding pre-warm finished (backend=%s).", backend)
    except asyncio.CancelledError:  # pragma: no cover - shutdown race
        raise
    except Exception as exc:  # noqa: BLE001 - pre-warm must never kill startup
        logger.warning("Embedding pre-warm task failed: %s", exc, exc_info=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle context manager managing DB pool initialization and teardown."""
    logger.info("Starting AI-Bibliometrics FastAPI gateway...")
    # Initialize asyncpg DB pool
    try:
        await init_pool()
    except Exception as exc:
        logger.error("Failed to initialize database pool on startup: %s", exc)
    # Session persistence pool (schema `app`, role `app_session`). Optional:
    # returns None when DB_URL_SESSION is unset and the gateway still serves
    # every bibliometric route. Wrapped in try/except for the same reason as
    # above — a session-store outage must not take retrieval down with it.
    try:
        await init_session_pool()
    except Exception as exc:  # noqa: BLE001 - session store must not kill startup
        logger.error("Failed to initialize session DB pool on startup: %s", exc)
    # Pre-warm shared httpx client (TCP keep-alive for Ollama calls)
    get_http_client()
    # Pre-warm the embedding model off the request path.
    prewarm_task = asyncio.create_task(_prewarm_embeddings())

    yield

    logger.info("Shutting down AI-Bibliometrics FastAPI gateway...")
    prewarm_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await prewarm_task
    await close_pool()
    await close_session_pool()
    await close_http_client()


def create_app() -> FastAPI:
    """Application factory configuring middleware, routes, and security boundaries."""

    settings = get_settings()

    app = FastAPI(
        title="AI-Bibliometrics Research Intelligence API",
        description="Evidence-grounded research intelligence and STI policy assistant API.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 1. Register global exception handlers (sanitizes error outputs)
    register_error_handlers(app)

    # 2. Add HTTP Middlewares (Starlette executes last-added outermost,
    # so add inner-most first: RateLimit -> Tracing -> CORS gives
    # CORS -> Tracing -> RateLimit execution, keeping X-Request-ID on 429s)
    # Rate limiting: per-IP sliding window, docs/08 section 3. Value comes from
    # RATE_LIMIT_RPM (default 60) rather than a literal, so a deployment can
    # retune without a code change.
    # (innermost, runs inside tracing)
    app.add_middleware(
        RateLimitingMiddleware, requests_per_minute=settings.rate_limit_rpm
    )

    # Tracing: UUIDv4 request_id generation & latency measurement
    app.add_middleware(RequestTracingMiddleware)

    # CORS: explicit allow-list from CORS_ORIGINS (default: local dev origins
    # only). Never use a wildcard here — the API is credentialed
    # (allow_credentials=True), and this endpoint must not become reachable from
    # an arbitrary page once deployed.
    # DELETE is required by DELETE /api/v1/sessions/{session_id}; without it the
    # browser preflight rejects the request before it reaches the router.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # 3. Mount Routers
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(ask_router, prefix="/api/v1")
    # Session CRUD lives in the application domain (schema `app`), never in the
    # bibliometric read path. Mounted last so a path collision with /ask or
    # /health would be impossible rather than merely unlikely.
    app.include_router(sessions_router, prefix="/api/v1")

    # Prometheus exposition for the synthesis fallback counters. Imported lazily
    # so a missing prometheus_client degrades only this endpoint instead of
    # preventing the whole gateway from booting.
    @app.get("/metrics", tags=["Observability"])
    async def metrics() -> Response:
        """Expose synthesis counters in Prometheus text exposition format."""
        from prometheus_client import CONTENT_TYPE_LATEST

        from backend.app.core.metrics import render_metrics

        return Response(
            content=render_metrics(), media_type=CONTENT_TYPE_LATEST
        )

    # Root alias endpoint
    @app.get("/", tags=["Root"])
    async def root_status():
        return {
            "name": "AI-Bibliometrics API",
            "version": "1.0.0",
            "status": "operational",
            "docs": "/docs",
            "health": "/api/v1/health",
            "metrics": "/metrics",
        }

    return app


app = create_app()
