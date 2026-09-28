"""Async database connection pool for FastAPI runtime.

Enforces invariants:
- Read-only connection settings (docs/08 §1.1)
- statement_timeout = 10s enforced server-side
- search_path = public enforced per session
- Comprehensive health check probing Silver, Gold, and pgvector readiness
"""

from __future__ import annotations

import asyncio
from typing import Optional
import asyncpg

from backend.app.core.config import dsn_with_sslmode, get_settings
from backend.app.core.logging import logger
from backend.app.models.health import DatabaseHealth

_pool: Optional[asyncpg.Pool] = None

CANONICAL_SILVER_TABLES = [
    "publications",
    "authors",
    "institutions",
    "keywords",
    "funding",
    "pub_author",
    "pub_institution",
    "publication_references",
    "chunks",
]

GOLD_TABLES = [
    "topics",
    "topic_evolution",
    "researcher_expertise",
]


async def create_pool(
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Create a new asyncpg connection pool with strict security parameters."""
    settings = get_settings()
    if not settings.db_url:
        raise ValueError("DB_URL is not set (checked process env + project .env).")

    timeout_s = settings.db_statement_timeout_ms / 1000.0
    return await asyncpg.create_pool(
        settings.db_url,
        min_size=min_size,
        max_size=max_size,
        ssl="require",
        command_timeout=timeout_s,
        server_settings={
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "search_path": "public",
        },
    )


async def init_pool() -> asyncpg.Pool:
    """Initialize singleton pool on FastAPI startup."""
    global _pool
    if _pool is None or _pool.is_closing():
        _pool = await create_pool()
        logger.info("Database connection pool initialized successfully.")
    return _pool


async def get_pool() -> asyncpg.Pool:
    """Get active singleton pool, recreating if loop has changed or closed."""
    global _pool
    current_loop = asyncio.get_running_loop()
    if _pool is None or _pool.is_closing() or getattr(_pool, "_loop", None) != current_loop:
        if _pool is not None and not _pool.is_closing():
            try:
                await _pool.close()
            except Exception:
                pass
        _pool = await create_pool()
    return _pool

async def close_pool() -> None:
    """Close singleton pool on FastAPI shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed.")


async def check_db_health() -> DatabaseHealth:
    """Probe database health, table readiness, and pgvector status."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            user = await conn.fetchval("SELECT current_user;")

            # 1. Check public base tables
            table_rows = await conn.fetch(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
                """
            )
            live_tables = {r["table_name"] for r in table_rows}

            # 2. Check 9 Silver tables
            silver_ready = all(t in live_tables for t in CANONICAL_SILVER_TABLES)

            # 3. Check Gold tables
            gold_ready = all(t in live_tables for t in GOLD_TABLES)

            # 4. Check pgvector extension and chunk embedding
            ext_row = await conn.fetchrow(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
            )
            pgvector_installed = ext_row is not None

            chunks_embedded = False
            if "chunks" in live_tables and pgvector_installed:
                try:
                    null_cnt = await conn.fetchval(
                        "SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;"
                    )
                    total_cnt = await conn.fetchval("SELECT COUNT(*) FROM chunks;")
                    chunks_embedded = total_cnt > 0 and null_cnt == 0
                except Exception:
                    chunks_embedded = False

            pgvector_ready = pgvector_installed and chunks_embedded

            return DatabaseHealth(
                status="connected",
                role=str(user),
                silver_tables_ready=silver_ready,
                gold_tables_ready=gold_ready,
                pgvector_ready=pgvector_ready,
                public_tables_count=len(live_tables),
                error=None,
            )
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return DatabaseHealth(
            status="disconnected",
            role="unknown",
            silver_tables_ready=False,
            gold_tables_ready=False,
            pgvector_ready=False,
            public_tables_count=0,
            error="Database connection unreachable",
        )
