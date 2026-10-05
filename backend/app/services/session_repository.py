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


def _decode_jsonb_list(raw: Any) -> list[dict[str, Any]]:
    """Normalise a jsonb column that must hold an array of objects.

    asyncpg decodes jsonb to ``str`` by default, so a caller reading this column
    through a pool gets a JSON string rather than a list. This coerces it back.

    Robustness is deliberate rather than incidental. A provenance snapshot is
    *rendering data*, so a malformed value must never be able to fail the read
    that restores a user's conversation: anything that is not an array of objects
    degrades to ``[]`` and is logged, exactly as a corrupt ``applied_filters``
    degrades to absent. The alternative — raising — would mean one bad row makes
    a whole session unopenable.

    Note this is the *read* side of the fence, not the source-of-truth rule. It
    only makes stored bytes renderable; nothing may treat the result as an
    authoritative bibliometric value. See migration 006 and
    ``tests/unit/test_session_provenance.py``.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Stored provenance snapshot is not valid JSON; rendering it as absent."
            )
            return []
    if not isinstance(raw, list):
        logger.warning(
            "Stored provenance snapshot is not a JSON array (got %s); "
            "rendering it as absent.",
            type(raw).__name__,
        )
        return []
    return [item for item in raw if isinstance(item, dict)]


def _encode_jsonb_list(items: list[dict[str, Any]] | None) -> str:
    """Serialise a provenance snapshot for storage, tolerating non-JSON values.

    ``json.dumps`` with ``default=str`` is a safety net, not a licence: a
    snapshot that would raise must not be able to lose the assistant turn it
    belongs to. Losing provenance degrades the history rendering; losing the
    turn would silently truncate a user's conversation, which is strictly worse.
    """
    return json.dumps(items or [], default=str)


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
        metadata plus per-session counts — never the transcript, and never the
        evidence snapshots (those are per-turn, and would multiply the payload of
        a list view by the length of every conversation in it).

        The counts are computed here, in the database, on purpose. ``message_count``
        and ``source_count`` are the Recent Sessions sidebar's only content, and a
        client that had to derive them would be deriving them from a transcript it
        did not fetch — i.e. guessing. Deterministic and authoritative beats
        approximately-right.

        Count semantics, stated so the sidebar and the API cannot disagree:

        ``message_count``  every persisted message in the session, user turns
                          included, ``failed`` turns included. It is "how long is
                          this transcript", so it must not quietly shrink when a
                          turn failed.
        ``source_count``   DISTINCT publications the session's assistant turns
                          rest on. Identity is ``publication_id``, falling back to
                          ``doi`` then ``title`` for a record that predates
                          publication_id. Counting every citation instead would
                          inflate with repetition, so a session citing the same 3
                          papers 10 times reports 3, not 30.
        ``last_route``    the route of the most recent assistant turn that had
                          one. NULL for a session with no answered turn, which is
                          why the sidebar must tolerate an absent value rather
                          than printing a route for an empty conversation.

        One LEFT JOIN LATERAL rather than a GROUP BY over the joined transcript:
        the lateral is evaluated per session and touches
        ``idx_research_messages_session_created`` once, where a grouped join
        would fan every transcript row out before aggregating.
        """
        capped = max(1, min(limit, MAX_LIST_LIMIT))
        sql = f"""
            SELECT s.session_id, s.title, s.status,
                   s.created_at, s.updated_at, s.last_message_at,
                   COALESCE(m.message_count, 0)    AS message_count,
                   COALESCE(m.source_count, 0)     AS source_count,
                   m.last_route
            FROM {_q("research_sessions")} s
            LEFT JOIN LATERAL (
                SELECT
                    -- COUNT(DISTINCT msg.message_id), NOT COUNT(*): the
                    -- jsonb_array_elements join below fans one message row out
                    -- into one row per source, so a 2-message session with 3
                    -- sources each would count 6. message_id is the primary
                    -- key, so DISTINCT on it is both exact and fan-out proof.
                    COUNT(DISTINCT msg.message_id) AS message_count,
                    COUNT(DISTINCT COALESCE(
                        src->>'publication_id',
                        src->>'doi',
                        src->>'title'
                    )) FILTER (WHERE src IS NOT NULL) AS source_count,
                    (ARRAY_AGG(msg.route ORDER BY msg.created_at DESC, msg.seq DESC)
                        FILTER (WHERE msg.role = 'assistant' AND msg.route IS NOT NULL)
                    )[1] AS last_route
                FROM {_q("research_messages")} msg
                LEFT JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(msg.sources) = 'array'
                         THEN msg.sources ELSE '[]'::jsonb END
                ) AS src ON TRUE
                WHERE msg.session_id = s.session_id
            ) m ON TRUE
            {{where}}
            ORDER BY s.last_message_at DESC NULLS LAST, s.updated_at DESC,
                     s.created_at DESC
            LIMIT $1
        """
        if status is None:
            return [
                dict(r)
                for r in await self._conn.fetch(
                    sql.format(where="WHERE s.status = 'active'"), capped
                )
            ]
        return [
            dict(r)
            for r in await self._conn.fetch(
                sql.format(where="WHERE s.status = $2"), capped, status
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

    async def set_title(self, session_id: uuid.UUID, title: str) -> dict[str, Any]:
        """Set a session's title unconditionally (explicit user rename).

        Distinct from :meth:`set_title_if_placeholder`, which only fires while the
        title is still the placeholder. This one is the user saying "call it
        this", so it always wins — including over a previously derived title, and
        including over the placeholder, so a user can restore a name that
        auto-titling would otherwise keep replacing.

        ``updated_at`` moves so the Recent Sessions ordering reflects the rename.

        Validation of ``title`` (trim, non-empty, length cap) happens in the
        request model, not here: this method is reached only through
        ``PATCH /sessions/{id}``, and a CHECK-constraint backstop for
        ``btrim(title) <> ''`` already exists in migration 005. Re-validating in
        both layers would give two answers to "what is a valid title".
        """
        row = await self._conn.fetchrow(
            f"""
            UPDATE {_q("research_sessions")}
            SET title = $2, updated_at = now()
            WHERE session_id = $1
            RETURNING session_id, title, status, created_at, updated_at,
                      last_message_at
            """,
            session_id,
            title,
        )
        if row is None:
            raise SessionNotFoundError()
        return dict(row)

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
        evidence_objects: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Append one turn to the transcript.

        Pure INSERT: no conflict surface, so two concurrent turns cannot
        overwrite each other. The FK to research_sessions raises if the session
        vanished between validation and this write, which is the correct
        outcome — a turn cannot outlive the conversation it belongs to.

        ``evidence_objects`` / ``sources`` (migration 006) are an immutable
        per-turn snapshot of what the verified response carried, stored so the
        workspace can be reopened without re-running RAG. They are written once,
        never recomputed, never aggregated across turns, and never read as
        source-of-truth data — see the fence in migration 006. Defaults to an
        empty array, which is the correct value for a ``user`` turn.
        """
        payload = _sanitize_filters(applied_filters)
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO {_q("research_messages")}
                (session_id, role, content, status, applied_filters, request_id, route,
                 evidence_objects, sources)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9::jsonb)
            RETURNING message_id, session_id, role, content, status,
                      applied_filters, request_id, route, created_at,
                      evidence_objects, sources
            """,
            session_id,
            role,
            content,
            status,
            json.dumps(payload) if payload else None,
            request_id,
            route,
            _encode_jsonb_list(evidence_objects),
            _encode_jsonb_list(sources),
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
        result["evidence_objects"] = _decode_jsonb_list(result.get("evidence_objects"))
        result["sources"] = _decode_jsonb_list(result.get("sources"))
        return result

    async def list_messages(
        self, session_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Full transcript, oldest first, INCLUDING provenance snapshots.

        Ordered by ``(created_at, seq)``: ``seq`` is a table-level identity, so
        it breaks same-millisecond ties deterministically. Two turns written
        inside one millisecond (concurrent requests) therefore still have a
        defined order instead of an arbitrary one.

        This is the restoration read path — ``GET /api/v1/sessions/{id}`` — so
        it selects ``evidence_objects`` and ``sources``. Those snapshots are what
        let a reopened session redraw the evidence rail exactly as it was shown,
        with no RAG re-run.
        """
        rows = await self._conn.fetch(
            f"""
            SELECT message_id, session_id, role, content, status,
                   applied_filters, request_id, route, created_at,
                   evidence_objects, sources
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
            d["evidence_objects"] = _decode_jsonb_list(d.get("evidence_objects"))
            d["sources"] = _decode_jsonb_list(d.get("sources"))
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

        DELIBERATELY EXCLUDES ``evidence_objects`` and ``sources``, unlike
        :meth:`list_messages`. This read feeds ``SessionService.load_context``,
        which assembles the conversation block handed to the optional narration
        step. Injecting stored metric snapshots into that prompt would create
        exactly the source-of-truth path migration 006 fences off: the model
        could restate a snapshot as a current fact instead of re-deriving it.

        So the two reads differ by design, not by oversight:
          * ``list_messages``         → rendering. Provenance included.
          * ``list_recent_messages``  → model context. Provenance excluded;
                                       conversation text only, and that text is
                                       already labelled untrusted.
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