"""Integration tests for async database pool and security invariants.

Docs Reference: docs/08 Security.md §1, docs/11 Roadmap.md §4 (Fase 2).
"""

from __future__ import annotations

import pytest
import asyncpg
from backend.app.core.config import get_settings
from backend.app.db.pool import check_db_health, create_pool


@pytest.mark.asyncio
async def test_db_pool_lifecycle_and_invariants():
    """Verify pool creation, search_path enforcement, and statement_timeout."""
    pool = await create_pool(min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # 1. Verify search_path is set to public
            path = await conn.fetchval("SHOW search_path;")
            assert "public" in path

            # 2. Verify statement timeout is active
            timeout = await conn.fetchval("SHOW statement_timeout;")
            assert timeout is not None
            assert timeout != "0"

            # 3. Verify user query works
            user = await conn.fetchval("SELECT current_user;")
            assert user is not None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_db_health_check():
    """Verify check_db_health returns fully populated DatabaseHealth object."""
    health = await check_db_health()
    assert health.status == "connected"
    assert health.silver_tables_ready is True
    assert health.pgvector_ready is True
    assert health.public_tables_count >= 11
    assert health.error is None


@pytest.mark.asyncio
async def test_db_statement_timeout_enforcement():
    """Verify statement_timeout aborts runaway queries (isolated, non-pooled conn)."""
    settings = get_settings()
    conn = await asyncpg.connect(settings.db_url, command_timeout=5)
    try:
        await conn.execute("SET statement_timeout = '100ms';")
        with pytest.raises((asyncpg.QueryCanceledError, asyncpg.PostgresError)):
            await conn.execute("SELECT pg_sleep(1);")
    finally:
        try:
            await conn.execute("RESET statement_timeout;")
        except Exception:
            pass
        await conn.close()
