"""Second asyncpg pool: conversation state on schema ``app``, role ``app_session``.

Docs Reference: docs/08 §1.4, docs/04 §13.

WHY A SECOND POOL
=================
``backend/app/db/pool.py`` is wired to one DSN, pins ``search_path = public``,
and is expected to run as the unprivileged ``app_readonly`` role. Session
persistence needs INSERT/UPDATE/DELETE. Granting that on the same connection
would mean one credential can both run Text-to-SQL against ``publications`` and
rewrite them — a single compromised or buggy path becomes a corpus write, which
is precisely what the Session Isolation Invariant forbids (docs/03 §0.3 #5).

So the two domains get two pools, two DSNs, two roles:

    get_pool()          →  app_readonly   SELECT on public
    get_session_pool()  →  app_session    SELECT/INSERT/UPDATE/DELETE on app

The differences from the retrieval pool are deliberate, not accidental drift:

  * ``search_path`` is the SESSION_SCHEMA (``app``), not ``public``.
  * No pgvector codec is registered. The session role cannot read ``chunks``,
    so a vector binding has nothing to encode and no reason to exist.
  * ``statement_timeout`` defaults shorter (5s). Session writes are single-row
    point updates; anything slower is contention, not work.
  * **It returns ``None`` instead of raising when unconfigured.** Session
    persistence is opt-in via ``DB_URL_SESSION``. Raising would crash-loop the
    whole gateway on a missing optional variable; returning None lets callers
    serve an explicit 503 while /api/v1/ask stays fully functional and
    stateless, which is the documented bootstrap state.
"""

from __future__ import annotations

import asyncio

import asyncpg

from backend.app.core.config import get_settings
from backend.app.core.logging import logger

_session_pool: asyncpg.Pool | None = None


async def create_session_pool(
    *,
    min_size: int = 1,
    max_size: int = 5,
) -> asyncpg.Pool | None:
    """Create the conversation-state pool, or return None if not configured.

    Returns ``None`` when ``DB_URL_SESSION`` is unset. Never raises for the
    unconfigured case — see the module docstring.
    """
    settings = get_settings()
    if not settings.session_persistence_enabled:
        return None

    timeout_s = settings.session_statement_timeout_ms / 1000.0
    schema = settings.session_schema
    return await asyncpg.create_pool(
        settings.db_url_session,
        min_size=min_size,
        max_size=max_size,
        ssl="require",
        command_timeout=timeout_s,
        server_settings={
            "statement_timeout": f"{settings.session_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": "30s",
            "search_path": schema,
        },
    )


async def init_session_pool() -> asyncpg.Pool | None:
    """Initialize the session pool on FastAPI startup. Idempotent per event loop."""
    global _session_pool
    current_loop = asyncio.get_running_loop()
    if (
        _session_pool is None
        or _session_pool.is_closing()
        or getattr(_session_pool, "_loop", None) is not current_loop
    ):
        if _session_pool is not None and not _session_pool.is_closing():
            try:
                _session_pool.terminate()
            except Exception as exc:
                logger.warning(
                    "Stale session DB pool terminate failed during init: %s",
                    exc,
                    exc_info=True,
                )
        try:
            _session_pool = await create_session_pool()
            if _session_pool is None:
                logger.info(
                    "Session persistence DISABLED: DB_URL_SESSION is not set. "
                    "/api/v1/sessions will answer 503 and /api/v1/ask remains "
                    "stateless (docs/08 §1.4)."
                )
            else:
                logger.info(
                    "Session persistence pool initialized (schema=%s).",
                    get_settings().session_schema,
                )
        except Exception as exc:
            # Log and degrade rather than propagate: a session-store outage must
            # not take the bibliometric retrieval gateway down with it.
            _session_pool = None
            logger.error(
                "Failed to initialize session DB pool; session persistence "
                "degraded to disabled: %s",
                exc,
                exc_info=True,
            )
    return _session_pool


async def get_session_pool() -> asyncpg.Pool | None:
    """Get the session pool, or None when session persistence is unconfigured.

    Mirrors ``get_pool()``'s lazy-recreate behaviour so a pool closed by a test
    or by a reconnect is rebuilt on next use instead of raising.
    """
    global _session_pool
    settings = get_settings()
    if not settings.session_persistence_enabled:
        return None
    current_loop = asyncio.get_running_loop()
    if (
        _session_pool is None
        or _session_pool.is_closing()
        or getattr(_session_pool, "_loop", None) is not current_loop
    ):
        if _session_pool is not None and not _session_pool.is_closing():
            try:
                _session_pool.terminate()
            except Exception as exc:
                logger.warning(
                    "Stale session DB pool terminate failed during get: %s",
                    exc,
                    exc_info=True,
                )
        _session_pool = await create_session_pool()
    return _session_pool


async def close_session_pool() -> None:
    """Close the session pool on FastAPI shutdown."""
    global _session_pool
    if _session_pool is not None:
        await _session_pool.close()
        _session_pool = None
        logger.info("Session database connection pool closed.")