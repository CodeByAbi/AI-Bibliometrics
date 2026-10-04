"""Unit tests: PATCH /api/v1/sessions/{id} — validation, dispatch, error semantics.

Docs Reference: docs/06 Api Design.md §6.2.

Why rename gets its own file: it is the only session mutation where the caller's
entire request IS the bookkeeping. Every other write in ``SessionService`` runs
inside ``/api/v1/ask`` and swallows storage errors on purpose, because losing
bookkeeping must never turn a produced answer into a 500. A rename has no such
excuse — a silent failure there returns 200 with the old title still in place,
and the user reasonably believes it saved.

So these tests pin the two things that could quietly regress: that validation is
owned by exactly one layer, and that a storage failure propagates instead of being
swallowed.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.core.errors import SessionNotFoundError
from backend.app.models.session import (
    SessionCreatedResponse,
    SessionCreateRequest,
    SessionUpdateRequest,
)
from backend.app.routers.sessions import update_session
from backend.app.services.session_repository import SessionRepository
from backend.app.services.session_service import SessionService


class _Conn:
    def __init__(self, row: Any = None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.row


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        outer = self

        class _Acquire:
            async def __aenter__(self_inner) -> _Conn:
                return outer._conn

            async def __aexit__(self_inner, *exc: object) -> bool:
                return False

        return _Acquire()


def _service(conn: _Conn) -> SessionService:
    svc = SessionService.__new__(SessionService)
    svc._pool = _Pool(conn)  # type: ignore[attr-defined]
    return svc


# ---------------------------------------------------------------------------
# Validation — owned by the request model, and only by it
# ---------------------------------------------------------------------------
class TestRenameValidation:
    def test_trims_and_collapses_whitespace(self) -> None:
        assert SessionUpdateRequest(title="  My   MSC research \n").title == (
            "My MSC research"
        )

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_rejects_blank_titles(self, bad: str) -> None:
        """Emptiness is checked AFTER trimming.

        Checking it before would let "   " satisfy min_length=1 and fail later
        against the database CHECK as a 500 with no field path, instead of a 422
        naming the field.
        """
        with pytest.raises(ValidationError):
            SessionUpdateRequest(title=bad)

    def test_rejects_oversized_title(self) -> None:
        with pytest.raises(ValidationError):
            SessionUpdateRequest(title="a" * 201)

    def test_accepts_max_length(self) -> None:
        assert len(SessionUpdateRequest(title="a" * 200).title) == 200

    def test_rejects_unknown_fields(self) -> None:
        """A rename must not be a general-purpose update.

        ``extra="forbid"`` is what stops a stray ``status`` or ``summary`` in the
        body from reaching a session lifecycle or overwrite path.
        """
        payloads = (
            {"title": "ok", "status": "archived"},
            {"title": "ok", "id": "x"},
        )
        for payload in payloads:
            with pytest.raises(ValidationError):
                SessionUpdateRequest.model_validate(payload)

    def test_title_is_required(self) -> None:
        with pytest.raises(ValidationError):
            SessionUpdateRequest.model_validate({})

    def test_validation_is_not_duplicated_in_the_service(self) -> None:
        """One layer owns "what is a valid title".

        Re-checking in the service would give two answers that could drift, and a
        request accepted by one while rejected by the other.
        """
        src = inspect.getsource(SessionService.rename_session)
        assert "strip" not in src and "btrim" not in src

    def test_create_and_update_normalise_consistently(self) -> None:
        """Both title paths collapse whitespace the same way."""
        raw = "  a   b  "
        assert SessionCreateRequest(title=raw).title == "a b"
        assert SessionUpdateRequest(title=raw).title == "a b"


# ---------------------------------------------------------------------------
# Dispatch — a rename is one unconditional UPDATE
# ---------------------------------------------------------------------------
class TestRenameDispatch:
    @pytest.mark.asyncio
    async def test_issues_a_single_bounded_update(self) -> None:
        conn = _Conn(row=None)
        repo = SessionRepository(conn)
        with pytest.raises(SessionNotFoundError):
            await repo.set_title(uuid.uuid4(), "My MSC research")
        assert len(conn.calls) == 1
        sql, args = conn.calls[0]
        assert "UPDATE" in sql.upper()
        # title must be bound, never interpolated
        assert "My MSC research" not in sql
        assert args[1] == "My MSC research"

    @pytest.mark.asyncio
    async def test_unknown_session_raises_not_found(self) -> None:
        repo = SessionRepository(_Conn(row=None))
        with pytest.raises(SessionNotFoundError):
            await repo.set_title(uuid.uuid4(), "x")

    @pytest.mark.asyncio
    async def test_no_placeholder_predicate_in_set_title(self) -> None:
        """Structural: the WHERE clause must not carry a title equality guard.

        If a placeholder guard ever leaked into this statement, a user renaming a
        session whose title had already been auto-derived would silently 404.
        """
        src = inspect.getsource(SessionRepository.set_title)
        body = src.split('"""', 2)[-1]
        assert "title = $3" not in body
        assert "AND title" not in body

    @pytest.mark.asyncio
    async def test_touches_nothing_but_the_title(self) -> None:
        """A rename must not move last_message_at or the lifecycle status.

        Asserted against the SET clause only. ``last_message_at`` legitimately
        appears in RETURNING — reading a column is not writing it, and the
        response needs it to render the sidebar.
        """
        src = inspect.getsource(SessionRepository.set_title)
        body = src.split('"""', 2)[-1]
        set_clause = body.split("SET", 1)[1].split("WHERE", 1)[0]
        assert "last_message_at" not in set_clause
        assert "status" not in set_clause
        assert "title" in set_clause


# ---------------------------------------------------------------------------
# Error semantics — a rename propagates; the ask-time writes do not
# ---------------------------------------------------------------------------
class TestRenameErrorSemantics:
    @pytest.mark.asyncio
    async def test_storage_failure_propagates(self) -> None:
        """A silent failure here would report success with the old title."""

        class Boom:
            def acquire(self) -> Any:
                raise RuntimeError("connection reset")

        svc = SessionService.__new__(SessionService)
        svc._pool = Boom()  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError):
            await svc.rename_session(uuid.uuid4(), "x")

    @pytest.mark.asyncio
    async def test_persist_turn_still_swallows(self) -> None:
        """Contrast case: the ask-time write must keep swallowing.

        Otherwise the guard above would be "fixed" by removing error handling
        where it is genuinely required, turning a bookkeeping failure into a 500
        on an otherwise-correct answer.
        """
        class Boom:
            def acquire(self) -> Any:
                raise RuntimeError("connection reset")

        svc = SessionService.__new__(SessionService)
        svc._pool = Boom()  # type: ignore[attr-defined]
        assert (
            await svc.record_assistant_message(
                session_id=uuid.uuid4(), content="a", route="SQLRoute", request_id="r"
            )
            is None
        )


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------
class TestRenameEndpoint:
    @pytest.mark.asyncio
    async def test_returns_the_renamed_session(self, monkeypatch: Any) -> None:
        now = "2026-01-02T03:04:05Z"
        sid = uuid.uuid4()
        svc = _service(
            _Conn(
                row={
                    "session_id": sid,
                    "title": "My MSC research",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "last_message_at": None,
                }
            )
        )

        async def _create() -> SessionService:
            return svc

        monkeypatch.setattr(
            "backend.app.routers.sessions.SessionService.create", _create
        )
        request = SessionUpdateRequest(title="  My MSC research ")
        out = await update_session(sid, request)
        assert isinstance(out, SessionCreatedResponse)
        assert out.title == "My MSC research"
        assert out.id == sid

    @pytest.mark.asyncio
    async def test_missing_session_surfaces_404(self, monkeypatch: Any) -> None:
        svc = _service(_Conn(row=None))

        async def _create() -> SessionService:
            return svc

        monkeypatch.setattr(
            "backend.app.routers.sessions.SessionService.create", _create
        )
        with pytest.raises(SessionNotFoundError):
            await update_session(uuid.uuid4(), SessionUpdateRequest(title="x"))


# ---------------------------------------------------------------------------
# Structural: rename must not have become a back door
# ---------------------------------------------------------------------------
class TestRenameHasNoSideEffects:
    def test_endpoint_cannot_reach_the_transcript(self) -> None:
        """A rename touches one row. It must not read or write messages."""
        tree = ast.parse(inspect.getsource(update_session))
        forbidden = {
            "list_messages",
            "append_message",
            "delete_session",
            "list_sessions",
            "refresh_summary",
        }
        called = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
        }
        assert not (called & forbidden), f"rename reached {called & forbidden}"

    def test_endpoint_is_patch_not_put(self) -> None:
        """PATCH is partial update semantics; PUT would promise full replacement."""
        src = inspect.getsource(update_session)
        assert "PUT" not in src
        assert "_SCR" not in src  # placeholder guard against accidental reuse

    def test_session_create_title_limit_matches_rename(self) -> None:
        """Both title paths must reject the same over-long value.

        A rename that accepted a title creation would reject produces a session
        the user cannot then edit.
        """

        def max_len(field: Any) -> int | None:
            for constraint in field.metadata:
                limit = getattr(constraint, "max_length", None)
                if limit is not None:
                    return int(limit)
            return None

        create_field = SessionCreateRequest.model_fields["title"]
        update_field = SessionUpdateRequest.model_fields["title"]
        assert max_len(create_field) == 200
        assert max_len(create_field) == max_len(update_field)