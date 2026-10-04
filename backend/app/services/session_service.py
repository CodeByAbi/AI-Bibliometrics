"""SessionService — conversation lifecycle and context assembly.

Docs Reference: docs/04 Database Schema.md §13, docs/06 Api Design.md §7,
docs/03 §0.3 invariant 5.

TRANSACTION BOUNDARY (the important part of this file)
======================================================
Per the spec's preferred ordering, a turn is persisted in two short
transactions with retrieval and synthesis running OUTSIDE both:

    1. INSERT user message + UPDATE session metadata;  COMMIT   (_persist_turn)
    2. retrieve evidence  ────────  (bibliometric pool, read-only)
       generate answer   ────────  (Ollama; seconds, not milliseconds)
    3. INSERT assistant message + UPDATE session metadata;  COMMIT  (_persist_turn)
    4. refresh summary (best effort; single upsert, so no transaction needed)

Why not one transaction around everything: an LLM call is 3-30 s against a
10 s statement_timeout and a 5 s session timeout. Holding a transaction open
across it would pin a connection for the whole generation, guarantee a timeout
failure at the end, and — worse — roll the user's own message away when it did.
Committing the user turn first means a failed request still leaves the question
on the record, which is the behaviour the spec's failure handling requires.

Each of steps 1 and 3 is a genuine two-statement transaction: the turn INSERT and
the session UPDATE either both land or neither does, so a failure cannot leave a
persisted turn with a stale ``last_message_at``. ``_persist_turn`` is the only
method that opens one; everything else is a single statement or a pure read, and
is already atomic on its own.

Step 4 is best effort. A summary failure must never fail the request: the
answer is already correct without it, and the next turn regenerates from the
transcript regardless.

WHAT CONTEXT IS AND IS NOT ALLOWED TO DO
========================================
``ConversationContext`` informs interpretation and nothing else:

  * It may fill in a filter the current question left unspecified, so
    "siapa yang paling produktif?" inside an "AI di Indonesia" session stays
    scoped to Indonesia.
  * It may NOT supply, override, short-circuit or hint at a bibliometric
    value. No number ever travels from the transcript into retrieval.
  * ``payload.question`` is never rewritten. Routing, Text-to-SQL generation,
    ``EvidenceSet.query`` and citation verification all continue to see exactly
    the text the user typed, so conversational history cannot change what SQL is
    generated or which citations are accepted.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from backend.app.core.config import get_settings
from backend.app.core.errors import SessionStoreUnavailableError
from backend.app.core.logging import logger
from backend.app.db.session_pool import get_session_pool
from backend.app.models.ask import FilterParams
from backend.app.models.session import (
    SCOPE_KEY_ORDER,
    ConversationContext,
    ConversationMessage,
    ConversationScope,
    MessageRoleLiteral,
    MessageStatusLiteral,
    SessionStatusLiteral,
    merge_applied_filters,
    render_scope_parts,
)
from backend.app.services.session_repository import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    SessionRepository,
)
from backend.app.services.session_summary_service import SessionSummaryService

#: Title assigned to a session created without one. ``adopt_title_from_first_question``
#: replaces it on the first turn, guarded by a WHERE-clause predicate rather than a
#: Python check, so a title the user chose is never overwritten.
UNTITLED_PLACEHOLDER = "Riset Baru"

#: Per-message cap when rendering conversation context for the LLM. The
#: retrieval path never sees this text, so a long answer costs bounded prompt
#: tokens rather than unbounded ones.
MAX_RENDERED_MESSAGE_CHARS = 400


class SessionService:
    """Owns the conversation lifecycle and the transaction boundaries.

    Holds the ``Pool`` and constructs a connection-bound :class:`SessionRepository`
    per unit of work. Owning the pool here — rather than letting the repository
    hold it — is what makes the transaction boundary expressible: the service
    picks the connection, opens the transaction on it, and hands that same
    connection to the repository.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @staticmethod
    def _repo(conn: asyncpg.Connection) -> SessionRepository:
        """Repository bound to ``conn`` — the connection a transaction runs on."""
        return SessionRepository(conn)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    async def create(cls) -> SessionService:
        """Build a service over the session pool.

        Raises :class:`SessionStoreUnavailableError` (503) when session
        persistence is not configured. Callers in /api/v1/sessions translate that
        straight into the HTTP response; /api/v1/ask checks the flag first and
        only builds a service when the caller actually supplied a session_id.
        """
        pool = await get_session_pool()
        if pool is None:
            raise SessionStoreUnavailableError()
        return cls(pool)

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------
    async def create_session(self, *, title: str | None) -> dict[str, Any]:
        """Create a session, defaulting the title to the placeholder."""
        async with self._pool.acquire() as conn:
            return await self._repo(conn).create_session(
                title=title or UNTITLED_PLACEHOLDER
            )

    async def get_session(self, session_id: uuid.UUID) -> dict[str, Any]:
        """Fetch session metadata, raising 404 when absent."""
        async with self._pool.acquire() as conn:
            return await self._repo(conn).require_session(session_id)

    async def list_sessions(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        status: SessionStatusLiteral | None = None,
    ) -> list[dict[str, Any]]:
        """Recent sessions, newest activity first, metadata only."""
        async with self._pool.acquire() as conn:
            return await self._repo(conn).list_sessions(
                limit=max(1, min(limit, MAX_LIST_LIMIT)), status=status
            )

    async def get_session_detail(self, session_id: uuid.UUID) -> dict[str, Any]:
        """Session metadata plus the full transcript and current summary.

        One connection for all three reads: they are consistent with each other
        (no interleaved write can land between them) and it costs one acquire
        instead of three.
        """
        async with self._pool.acquire() as conn:
            repo = self._repo(conn)
            session = await repo.require_session(session_id)
            messages = await repo.list_messages(session_id)
            summary_row = await repo.get_summary(session_id)
        session["messages"] = messages
        session["summary"] = summary_row["summary"] if summary_row else None
        return session

    async def archive_session(
        self, session_id: uuid.UUID, status: SessionStatusLiteral = "archived"
    ) -> dict[str, Any]:
        """Set the lifecycle status. Touches nothing outside this row."""
        async with self._pool.acquire() as conn:
            return await self._repo(conn).set_session_status(session_id, status)

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        """Delete the session; cascades to its transcript and summary only.

        The ON DELETE CASCADE fires inside the single DELETE statement, so it is
        already atomic — no explicit transaction needed.
        """
        async with self._pool.acquire() as conn:
            return await self._repo(conn).delete_session(session_id)

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------
    async def load_context(self, session_id: uuid.UUID) -> ConversationContext:
        """Assemble the bounded context for one turn.

        Window = summary + the newest ``SESSION_RECENT_MESSAGES_LIMIT`` turns +
        the current question. The limit is what keeps prompt size independent of
        session length; without it a 500-turn session would grow the LLM prompt
        by ~50 KB per request.
        """
        settings = get_settings()
        async with self._pool.acquire() as conn:
            repo = self._repo(conn)
            summary_row = await repo.get_summary(session_id)
            recent = await repo.list_recent_messages(
                session_id, limit=settings.session_recent_messages_limit
            )
        messages = [
            ConversationMessage(
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                applied_filters=row.get("applied_filters") or {},
            )
            for row in recent
        ]
        return ConversationContext(
            session_id=session_id,
            summary=summary_row["summary"] if summary_row else None,
            recent_messages=messages,
            scope=derive_scope(messages),
        )

    # ------------------------------------------------------------------
    # Turn persistence
    # ------------------------------------------------------------------
    async def _persist_turn(
        self,
        *,
        session_id: uuid.UUID,
        role: MessageRoleLiteral,
        content: str,
        status: MessageStatusLiteral,
        applied_filters: dict[str, Any] | None,
        request_id: str | None,
        route: str | None = None,
        evidence_objects: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """INSERT one turn and bump session metadata, atomically.

        This is the ONLY place a session transaction is opened, and it is the
        reason :class:`SessionRepository` is connection-bound rather than
        pool-bound: the repository is constructed on ``conn`` — the very
        connection ``conn.transaction()`` runs on — so both statements are
        genuinely inside the transaction.

        An earlier revision held the *pool* in the repository and called
        ``pool.fetchrow()``, which acquires a separate connection per call. The
        transaction then committed an empty scope while appearing to cover the
        writes, and a crash between the INSERT and the UPDATE left a persisted
        turn with a stale ``last_message_at``. ``tests/unit/
        test_session_transaction.py`` reproduces that dispatch and fails against
        the old shape.

        Swallowing errors is deliberate: a persistence failure must never turn a
        produced answer into a 500, nor lose the request. Returns None on
        failure, including the "session deleted while the request was in flight"
        case, where the request still answers — it just cannot record history
        for a conversation that no longer exists.

        ``evidence_objects`` / ``sources`` ride along in the SAME INSERT, not a
        second statement. Adding a separate write here would put a third
        statement inside this transaction boundary and give the provenance a
        separate failure mode from the turn it describes — a stored answer whose
        evidence silently failed to attach. One row, one statement, one
        transaction: the snapshot is committed atomically with the turn or not at
        all. The transaction scope itself is unchanged.
        """
        try:
            async with (
                self._pool.acquire() as conn,
                conn.transaction(),
            ):
                repo = self._repo(conn)
                row = await repo.append_message(
                    session_id=session_id,
                    role=role,
                    content=content,
                    status=status,
                    applied_filters=applied_filters,
                    request_id=request_id,
                    route=route,
                    evidence_objects=evidence_objects,
                    sources=sources,
                )
                await repo.touch_session(session_id)
            return row
        except Exception as exc:
            logger.warning(
                "Failed to persist %s message for session %s: %s",
                role,
                session_id,
                exc,
                exc_info=True,
            )
            return None

    async def record_user_message(
        self,
        *,
        session_id: uuid.UUID,
        content: str,
        applied_filters: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist the user turn. Transaction 1 — committed BEFORE retrieval.

        Ordering is the point: an LLM call runs for seconds, so committing the
        user's question first means a downstream failure cannot roll it away.
        """
        return await self._persist_turn(
            session_id=session_id,
            role="user",
            content=content,
            status="complete",
            applied_filters=applied_filters,
            request_id=request_id,
        )

    async def record_assistant_message(
        self,
        *,
        session_id: uuid.UUID,
        content: str,
        route: str | None,
        request_id: str | None,
        applied_filters: dict[str, Any] | None = None,
        status: MessageStatusLiteral = "complete",
    ) -> dict[str, Any] | None:
        """Persist the assistant turn. Transaction 3 — committed AFTER synthesis."""
        return await self._persist_turn(
            session_id=session_id,
            role="assistant",
            content=content,
            status=status,
            applied_filters=applied_filters,
            request_id=request_id,
            route=route,
        )

    async def adopt_title_from_first_question(
        self, session_id: uuid.UUID, question: str
    ) -> None:
        """Name an untitled session after its first question. Best effort.

        Only fires while the title is still the placeholder, so it can never
        overwrite a title the user chose. The derived title is a truncation of
        the question, never a summary containing metrics — see
        ``SessionSummaryService.title_from_question``.

        Single UPDATE with a WHERE predicate, so it needs no transaction.
        """
        try:
            title = SessionSummaryService.title_from_question(question)
            async with self._pool.acquire() as conn:
                await self._repo(conn).set_title_if_placeholder(
                    session_id, title, UNTITLED_PLACEHOLDER
                )
        except Exception as exc:
            logger.warning(
                "Failed to derive session title for %s: %s",
                session_id,
                exc,
                exc_info=True,
            )

    async def refresh_summary(
        self,
        *,
        session_id: uuid.UUID,
        messages: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Rebuild the rolling summary from a bounded tail of the transcript.

        Called AFTER the assistant turn is committed, so the summary reflects a
        complete exchange rather than a dangling question. Every failure path
        returns False and logs; nothing here can fail the request.

        Deterministic by construction: the same transcript always renders the
        same summary, so a concurrent regeneration produces an equally-valid
        document and the upsert resolves the tie atomically.

        Reads at most ``session_recent_messages_limit`` rows — the same window
        ``load_context`` uses. It previously called ``list_messages`` (no LIMIT),
        so every session-aware request pulled the entire transcript into memory
        and into the renderer, and the cost of a request grew without bound as
        the conversation did. The renderer only ever consumes the newest turns
        (current question plus ``MAX_QUESTIONS`` prior questions) and merges scope
        forward, so a bounded tail is both sufficient and the intended design.
        """
        settings = get_settings()
        try:
            async with self._pool.acquire() as conn:
                repo = self._repo(conn)
                rows = (
                    messages
                    if messages is not None
                    else await repo.list_recent_messages(
                        session_id, limit=settings.session_recent_messages_limit
                    )
                )
                summary = SessionSummaryService.build(
                    rows, max_chars=settings.session_summary_max_chars
                )
                if not summary:
                    return False
                await repo.upsert_summary(
                    session_id=session_id,
                    summary=summary,
                    # Window size actually covered, NOT the session total — the
                    # read is bounded, so this is the honest number.
                    messages_covered=len(rows),
                )
            return True
        except Exception as exc:
            logger.warning(
                "Session summary refresh failed for %s (answer unaffected): %s",
                session_id,
                exc,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Scope derivation
    # ------------------------------------------------------------------
    def effective_filters(
        self, explicit: FilterParams, scope: ConversationScope
    ) -> tuple[FilterParams, list[str]]:
        """Merge conversation scope under the caller's explicit filters.

        The caller's own filters always win. Scope only fills gaps, so a user
        who narrows to a different country on turn 3 gets that country, not the
        union of turn 1 and turn 3.

        Returns the merged filters and the list of keys that came from the
        session, so the caller can report them in ``DebugInfo`` — an
        auto-applied filter the user cannot see is indistinguishable, from the
        outside, from a wrong answer.
        """
        if scope.is_empty:
            return explicit, []

        explicit_set = {
            k
            for k in SCOPE_KEY_ORDER
            if getattr(explicit, k, None) not in (None, "")
        }
        inherited: dict[str, Any] = {}
        applied: list[str] = []
        for key, value in scope.as_filter_kwargs().items():
            if key in explicit_set:
                continue
            inherited[key] = value
            applied.append(key)

        if not inherited:
            return explicit, []

        return explicit.model_copy(update=inherited), applied


# ----------------------------------------------------------------------
# Pure helpers (module level so they are trivially unit-testable)
# ----------------------------------------------------------------------
def derive_scope(messages: list[ConversationMessage]) -> ConversationScope:
    """Resolve the conversation's current scope from the recent turns.

    Deliberately reads ``applied_filters`` — the resolved scope each turn
    actually used — rather than re-parsing message text. Two reasons: it cannot
    invent a constraint the user never stated, and it needs no NLP heuristics
    that would silently change behaviour when wording changes.
    """
    merged = merge_applied_filters([m.applied_filters for m in messages])
    if not merged:
        return ConversationScope()
    return ConversationScope(**merged)


def render_conversation_block(context: ConversationContext) -> str:
    """Render the context for the LLM narration step. Untrusted, never evidence.

    Reaches ONLY the optional ``llm_synthesis`` narration path, never
    retrieval. Concretely, this text cannot change which SQL is generated,
    which rows are returned, or which citations are accepted — those all happen
    before this function's output is used, against `public`.

    Ordering inside the block mirrors the instruction hierarchy
    (docs/08 §2.2): the summary is stated first as the background, the recent
    exchange provides referential context ("who was most productive?"), and the
    current question closes the block so the model reads it last.

    Assistant turns are included on purpose: a follow-up like "how about 2024?"
    only makes sense against the previous answer. They are marked so the model
    can tell a prior *claim* from retrieved evidence.

    Returns "" when there is nothing to say, which makes the caller omit the
    block entirely rather than send an empty one.
    """
    if context.is_empty:
        return ""

    parts: list[str] = []

    if context.summary:
        parts.append(f"[Ringkasan percakapan]\n{context.summary.strip()}")

    if not context.scope.is_empty:
        scope_parts = render_scope_parts(context.scope.as_filter_kwargs())
        if scope_parts:
            parts.append(f"[Cakupan sesi]\n{'; '.join(scope_parts)}")

    if context.recent_messages:
        lines = []
        for msg in context.recent_messages:
            if msg.role == "user":
                speaker = "Pengguna"
            else:
                # Marked as a prior claim so the model can tell a recollection
                # from retrieved evidence.
                speaker = "Asisten (jawaban sebelumnya, BUKAN bukti)"
            body = " ".join(msg.content.split())
            if len(body) > MAX_RENDERED_MESSAGE_CHARS:
                body = body[: MAX_RENDERED_MESSAGE_CHARS - 1].rstrip() + "…"
            lines.append(f"{speaker}: {body}")
        parts.append("[Percakapan sebelumnya]\n" + "\n".join(lines))

    return "\n\n".join(parts)