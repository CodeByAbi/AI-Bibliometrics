"""SessionRepository — the ONLY module that touches conversation tables.

Docs Reference: docs/04 Database Schema.md §13, docs/08 §1.4.

Repository Boundary
===================
This module reads and writes ``app.research_sessions``,
``app.research_messages`` and ``app.research_session_summaries``. Nothing else.

It accepts a single ``asyncpg.Connection`` and imports NOTHING from
``services.retrievers`` or ``services.evidence``. That import boundary is the
enforcement mechanism for the Session Isolation Invariant: a developer cannot
reach the bibliometric corpus from here, and the retrieval layer cannot reach
the transcript from there. Both facts are additionally asserted by
``tests/unit/test_session_isolation_unit.py``.

Every statement is explicitly schema-qualified with the validated
``SESSION_SCHEMA`` rather than relying on ``search_path``. Qualifying is
belt-and-braces against a misconfigured search_path, but it also makes the
domain boundary greppable: every SQL string in this file names ``app``.

All parameters are bound (``$1``, ``$2``, ...). No identifier is ever
interpolated from request data — the only interpolated value is the validated
schema name, which ``Settings.validate_session_schema`` restricts to a plain SQL
identifier and forbids from being ``public``.

Concurrency notes
-----------------
``touch_session`` bumps the counters with a single in-place UPDATE rather than
a read-modify-write, so two concurrent turns cannot lose an increment.
``upsert_summary`` is an INSERT ... ON CONFLICT DO UPDATE, which is atomic by
construction: of two concurrent upserts, one simply wins, so a summary can never
be assembled from half of each.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, cast

import asyncpg

from backend.app.core.config import get_settings
from backend.app.core.errors import SessionNotFoundError
from backend.app.core.logging import logger
from backend.app.models.session import (
    SCOPE_KEY_ORDER,
    MessageRoleLiteral,
    MessageStatusLiteral,
    SessionStatusLiteral,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def _schema() -> str:
    """Validated application schema name (never ``public``; see validator)."""
    return get_settings().session_schema


def _q(table: str) -> str:
    """Schema-qualify a session table name for interpolation into SQL."""
    return f"{_schema()}.{table}"


def _sanitize_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a filter mapping to the inherit-scope allow-list, JSON-safe.

    Only the keys in ``SCOPE_INHERITABLE_KEYS`` survive, so whatever a caller
    hands in cannot smuggle an unexpected field into stored provenance or into
    a future ``FilterParams``. Values are coerced to primitives that
    ``json.dumps`` can always serialise, because a non-serialisable value would
    raise inside the INSERT and lose the user's turn — the one thing the
    transcript must never do.
    """
    if not filters:
        return None
    clean: dict[str, Any] = {}
    for key in SCOPE_KEY_ORDER:
        value = filters.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean or None


class SessionRepository:
    """Data access for conversation state, bound to ONE connection.

    Takes a ``Connection``, not a ``Pool``. That is deliberate and load-bearing:

    ``SessionService`` owns the pool and decides where a transaction begins. If
    this class held the pool instead, every method would call
    ``pool.fetchrow(...)``, which **acquires its own connection** — so a
    ``conn.transaction()`` opened by the caller would guard an empty scope while
    appearing to cover the writes. That exact bug shipped once: an earlier
    revision took a pool here, and the probe in
    ``tests/unit/test_session_transaction.py`` reported 0 statements executing
    inside the transaction.

    With a connection injected, dispatch is unconditional and the transaction
    boundary is simply "the connection the service passes in". There is no
    per-method branch deciding pool-vs-conn.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    async def create_session(
        self,
        *,
        title: str,
        status: SessionStatusLiteral = "active",
    ) -> dict[str, Any]:
        """Create a session row and return it."""
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO {_q("research_sessions")} (title, status)
            VALUES ($1, $2)
            RETURNING session_id, title, status, created_at, updated_at, last_message_at
            """,
            title,
            status,
        )
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields
            raise RuntimeError("INSERT ... RETURNING produced no row")
        return dict(row)

    async def get_session(self, session_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch session metadata, or None when it does not exist.

        Returns None rather than raising so the caller decides the HTTP shape;
        ``require_session`` is the raising convenience used on the ask path.
        """
        row = await self._conn.fetchrow(
            f"""
            SELECT session_id, title, status, created_at, updated_at, last_message_at
            FROM {_q("research_sessions")}
            WHERE session_id = $1
            """,
            session_id,
        )
        return dict(row) if row else None

    async def require_session(self, session_id: uuid.UUID) -> dict[str, Any]:
        """Fetch session metadata or raise :class:`SessionNotFoundError` (404)."""
        row = await self.get_session(session_id)
        if row is None:
            raise SessionNotFoundError()
        return row

    async def list_sessions(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        status: SessionStatusLiteral | None = None,
    ) -> list[dict[str, Any]]:
        """List sessions newest-activity-first for the Recent Sessions view.

        ``last_message_at DESC NULLS LAST`` puts a session that has never had a
        message at the end rather than the top, so a freshly created empty
        session does not masquerade as the most recent research. Returns
        metadata only — never the transcript.
        """
        capped = max(1, min(limit, MAX_LIST_LIMIT))
        sql = f"""
            SELECT session_id, title, status, created_at, updated_at, last_message_at
            FROM {_q("research_sessions")}
            {{where}}
            ORDER BY last_message_at DESC NULLS LAST, updated_at DESC, created_at DESC
            LIMIT $1
        """
        if status is None:
            return [
                dict(r)
                for r in await self._conn.fetch(
                    sql.format(where="WHERE status = 'active'"), capped
                )
            ]
        return [
            dict(r)
            for r in await self._conn.fetch(
                sql.format(where="WHERE status = $2"), capped, status
            )
        ]

    async def set_session_status(
        self, session_id: uuid.UUID, status: SessionStatusLiteral
    ) -> dict[str, Any]:
        """Archive or re-activate a session.

        Touches nothing but this row. ``updated_at`` moves so the Recent
        Sessions ordering reflects the lifecycle change.
        """
        row = await self._conn.fetchrow(
            f"""
            UPDATE {_q("research_sessions")}
            SET status = $2, updated_at = now()
            WHERE session_id = $1
            RETURNING session_id, title, status, created_at, updated_at, last_message_at
            """,
            session_id,
            status,
        )
        if row is None:
            raise SessionNotFoundError()
        return dict(row)

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        """Delete a session and, by ON DELETE CASCADE, its transcript + summary.

        Returns False when the session did not exist, so DELETE is idempotent at
        the HTTP layer without pretending to have deleted something.

        SCOPE OF THE CASCADE: `app.research_messages` and
        `app.research_session_summaries` only. Migration 005 declares no
        foreign key from `app` to `public`, so this statement is structurally
        incapable of reaching the bibliometric corpus (AC-SESSION-9).
        """
        row = await self._conn.fetchrow(
            f"DELETE FROM {_q('research_sessions')} "
            f"WHERE session_id = $1 RETURNING session_id",
            session_id,
        )
        return row is not None

    async def set_title_if_placeholder(
        self, session_id: uuid.UUID, title: str, placeholder: str
    ) -> bool:
        """Set the title only while it still equals ``placeholder``.

        The placeholder predicate lives in the WHERE clause rather than in
        Python so the check and the write are one atomic statement: two
        concurrent first-turns cannot both observe "Riset Baru" and then both
        write, so a title the user chose is never overwritten by a derived one.
        """
        return (
            await self._conn.fetchval(
                f"""
                UPDATE {_q("research_sessions")}
                SET title = $2, updated_at = now()
                WHERE session_id = $1 AND title = $3
                RETURNING session_id
                """,
                session_id,
                title,
                placeholder,
            )
        ) is not None

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    async def append_message(
        self,
        *,
        session_id: uuid.UUID,
        role: MessageRoleLiteral,
        content: str,
        status: MessageStatusLiteral = "complete",
        applied_filters: dict[str, Any] | None = None,
        request_id: str | None = None,
        route: str | None = None,
    ) -> dict[str, Any]:
        """Append one turn to the transcript.

        Pure INSERT: no conflict surface, so two concurrent turns cannot
        overwrite each other. The FK to research_sessions raises if the session
        vanished between validation and this write, which is the correct
        outcome — a turn cannot outlive the conversation it belongs to.
        """
        payload = _sanitize_filters(applied_filters)
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO {_q("research_messages")}
                (session_id, role, content, status, applied_filters, request_id, route)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING message_id, session_id, role, content, status,
                      applied_filters, request_id, route, created_at
            """,
            session_id,
            role,
            content,
            status,
            json.dumps(payload) if payload else None,
            request_id,
            route,
        )
        if row is None:  # pragma: no cover - INSERT ... RETURNING always yields
            raise RuntimeError("INSERT ... RETURNING produced no row")
        result = dict(row)
        # asyncpg decodes jsonb to a str by default; normalise so callers always
        # see a mapping (or None) and never a JSON string.
        raw_filters = result.get("applied_filters")
        if isinstance(raw_filters, str):
            try:
                result["applied_filters"] = json.loads(raw_filters)
            except json.JSONDecodeError:
                logger.warning(
                    "Stored applied_filters for message %s is not valid JSON; "
                    "treating as absent.",
                    result.get("message_id"),
                )
                result["applied_filters"] = None
        return result

    async def list_messages(
        self, session_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Full transcript, oldest first.

        Ordered by ``(created_at, seq)``: ``seq`` is a table-level identity, so
        it breaks same-millisecond ties deterministically. Two turns written
        inside one millisecond (concurrent requests) therefore still have a
        defined order instead of an arbitrary one.
        """
        rows = await self._conn.fetch(
            f"""
            SELECT message_id, session_id, role, content, status,
                   applied_filters, request_id, route, created_at
            FROM {_q("research_messages")}
            WHERE session_id = $1
            ORDER BY created_at, seq
            """,
            session_id,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            raw = d.get("applied_filters")
            if isinstance(raw, str):
                try:
                    d["applied_filters"] = json.loads(raw)
                except json.JSONDecodeError:
                    d["applied_filters"] = None
            out.append(d)
        return out

    async def list_recent_messages(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[dict[str, Any]]:
        """The newest ``limit`` turns, returned oldest-first.

        Sub-select ordered DESC then reversed in Python rather than wrapped in a
        CTE with ORDER BY/LIMIT: asyncpg buffers both identically, and this
        keeps the statement a single indexable range scan on
        (session_id, created_at).
        """
        rows = await self._conn.fetch(
            f"""
            SELECT message_id, session_id, role, content, status,
                   applied_filters, request_id, route, created_at
            FROM {_q("research_messages")}
            WHERE session_id = $1
            ORDER BY created_at DESC, seq DESC
            LIMIT $2
            """,
            session_id,
            max(1, limit),
        )
        out: list[dict[str, Any]] = []
        for r in reversed(rows):
            d = dict(r)
            raw = d.get("applied_filters")
            if isinstance(raw, str):
                try:
                    d["applied_filters"] = json.loads(raw)
                except json.JSONDecodeError:
                    d["applied_filters"] = None
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # Session metadata + summary
    # ------------------------------------------------------------------
    async def touch_session(self, session_id: uuid.UUID) -> datetime | None:
        """Bump ``updated_at`` / ``last_message_at`` for a persisted turn.

        Single in-place UPDATE — deliberately NOT a read-modify-write. Two
        concurrent turns on one session would race on a Python-side
        read-then-write; here the database applies both, so neither is lost.

        Returns the new ``updated_at`` (or None when the session is gone, which
        is the "session deleted mid-request" case the caller logs rather than
        raises on).
        """
        updated_at = await self._conn.fetchval(
            f"""
            UPDATE {_q("research_sessions")}
            SET updated_at = now(), last_message_at = now()
            WHERE session_id = $1
            RETURNING updated_at
            """,
            session_id,
        )
        return cast(datetime | None, updated_at)

    async def upsert_summary(
        self,
        *,
        session_id: uuid.UUID,
        summary: str,
        messages_covered: int,
    ) -> None:
        """Insert or replace the session's rolling summary.

        ``ON CONFLICT (session_id) DO UPDATE`` is the whole concurrency story
        for the summary: it is a single atomic statement, so two concurrent
        turns cannot interleave into a corrupt document. The loser simply
        overwrites with an equally-valid regeneration, and the next turn
        rebuilds from the transcript anyway.
        """
        await self._conn.execute(
            f"""
            INSERT INTO {_q("research_session_summaries")}
                (session_id, summary, messages_covered)
            VALUES ($1, $2, $3)
            ON CONFLICT (session_id) DO UPDATE
            SET summary = EXCLUDED.summary,
                messages_covered = EXCLUDED.messages_covered,
                updated_at = now()
            """,
            session_id,
            summary,
            max(0, messages_covered),
        )

    async def get_summary(self, session_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch the current summary row, or None when none has been built yet."""
        row = await self._conn.fetchrow(
            f"""
            SELECT session_id, summary, messages_covered, created_at, updated_at
            FROM {_q("research_session_summaries")}
            WHERE session_id = $1
            """,
            session_id,
        )
        return dict(row) if row else None