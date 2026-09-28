"""FastAPI application entrypoint for AI-Bibliometrics API Gateway.

Docs Reference: docs/06 Api Design.md, docs/08 Security.md, docs/11 Roadmap.md (Fase 2).
"""

from __future__ import annotations

import contextlib
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.errors import register_error_handlers
from backend.app.core.logging import logger
from backend.app.core.middleware import RateLimitingMiddleware, RequestTracingMiddleware
from backend.app.db.pool import close_pool, init_pool
from backend.app.routers.ask import router as ask_router
from backend.app.routers.health import router as health_router


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle context manager managing DB pool initialization and teardown."""
    logger.info("Starting AI-Bibliometrics FastAPI gateway...")
    # Initialize asyncpg DB pool
    try:
        await init_pool()
    except Exception as exc:
        logger.error("Failed to initialize database pool on startup: %s", exc)

    yield

    logger.info("Shutting down AI-Bibliometrics FastAPI gateway...")
    await close_pool()


def create_app() -> FastAPI:
    """Application factory configuring middleware, routes, and security boundaries."""

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
    # Rate limiting: 60 requests/min per IP (innermost, runs inside tracing)
    app.add_middleware(RateLimitingMiddleware, requests_per_minute=60)

    # Tracing: UUIDv4 request_id generation & latency measurement
    app.add_middleware(RequestTracingMiddleware)

    # CORS: Restricted origins
    origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # 3. Mount Routers
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(ask_router, prefix="/api/v1")

    # Root alias endpoint
    @app.get("/", tags=["Root"])
    async def root_status():
        return {
            "name": "AI-Bibliometrics API",
            "version": "1.0.0",
            "status": "operational",
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()
