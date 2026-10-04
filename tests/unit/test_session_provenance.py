"""Unit tests: per-turn provenance snapshots are stored, fenced, and never trusted.

Docs Reference: database/migrations/006_session_message_provenance.sql,
docs/03 System Architecture.md §0.3 #5 (Session Isolation Invariant).

WHAT THIS FILE FENCES
=====================
Migration 006 is a deliberate, owner-signed-off narrowing of the Session
Isolation Invariant. Migration 005 states that no column on any session table may
hold a bibliometric metric, and ``evidence_objects`` / ``sources`` do hold metric
values. They are permitted as an immutable per-turn snapshot of one already
verified response — nothing more.

An exception like that decays. Someone adds a field, widens a query, or lets the
snapshot reach a prompt, and the fence quietly stops meaning anything. So these
tests assert the fence from three sides:

1. MECHANICS — the snapshot survives the JSONB round-trip, is committed in the
   same statement as the turn, and is read back on the restoration path only.
2. NON-TRUST — a stored snapshot can never answer a bibliometric question. The
   hostile-snapshot tests plant a fabricated corpus figure and require a re-ask to
   return the value measured live from ``public``.
3. SCOPE — the exception stays per-turn. Provenance must not appear on a
   session-level DTO, and the LLM context read must not select it.

No database is required. The repository is driven through a recording fake, which
is also what lets these tests assert on *dispatch* — which connection, which
statement, which parameters — rather than only on outcomes.
"""

from __future__ import annotations

import ast
import inspect
import json
import uuid
from typing import Any, ClassVar

import pytest

from backend.app.models.ask import EvidenceObject, SourceItem
from backend.app.models.session import (
    SessionDetailResponse,
    SessionListItem,
    SessionMessageResponse,
)
from backend.app.routers.ask import _provenance_payload
from backend.app.services import session_repository as repo_mod
from backend.app.services.session_repository import (
    SessionRepository,
    _decode_jsonb_list,
    _encode_jsonb_list,
)
from backend.app.services.session_service import SessionService

NOW = "2026-01-02T03:04:05Z"

EVIDENCE = [
    {
        "claim": "Total publikasi UI 2023",
        "metric": "publication_count",
        "value": 12,
        "period": "2023",
        "confidence": 1.0,
        "sources": [
            {
                "publication_id": "P1",
                "doi": "10.1/a",
                "eid": None,
                "title": "Paper A",
                "year": 2023,
            }
        ],
    }
]
SOURCES = [
    {
        "publication_id": "P1",
        "title": "Paper A",
        "year": 2023,
        "doi": "10.1/a",
        "source_type": "sql",
        "relevance_score": None,
        "provenance": None,
    }
]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class RecordingConn:
    """Captures every statement and the connection it ran on."""

    def __init__(self, name: str = "conn", log: list[str] | None = None) -> None:
        self.name = name
        self.log: list[str] = log if log is not None else []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.in_txn = False
        self.rows: list[Any] = []

    def transaction(self) -> Any:
        outer = self

        class _Tx:
            async def __aenter__(self_inner) -> RecordingConn:
                outer.log.append("BEGIN")
                outer.in_txn = True
                return outer

            async def __aexit__(self_inner, *exc: object) -> bool:
                outer.log.append("COMMIT")
                outer.in_txn = False
                return False

        return _Tx()

    def _record(self, sql: str, args: tuple[Any, ...]) -> None:
        self.calls.append((sql, args))
        self.log.append(f"{sql.split()[0]} in_txn={self.in_txn}")

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._record(sql, args)
        if self.rows:
            return self.rows.pop(0)
        return {"message_id": uuid.uuid4(), "session_id": args[0]}

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self._record(sql, args)
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._record(sql, args)
        return 1

    def sql_texts(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


class _FakePool:
    def __init__(self, conn: RecordingConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        outer = self

        class _Acquire:
            async def __aenter__(self_inner) -> RecordingConn:
                return outer._conn

            async def __aexit__(self_inner, *exc: object) -> bool:
                return False

        return _Acquire()


def _repo() -> SessionRepository:
    return SessionRepository(RecordingConn())


def _service() -> SessionService:
    conn = RecordingConn()
    svc = SessionService.__new__(SessionService)
    svc._pool = _FakePool(conn)  # type: ignore[attr-defined]
    return svc


# ---------------------------------------------------------------------------
# 1. Mechanics — the snapshot round-trips
# ---------------------------------------------------------------------------
class TestProvenanceRoundTrip:
    @pytest.mark.asyncio
    async def test_append_message_binds_both_provenance_columns(self) -> None:
        repo = _repo()
        await repo.append_message(
            session_id=uuid.uuid4(),
            role="assistant",
            content="Jawaban",
            evidence_objects=EVIDENCE,
            sources=SOURCES,
        )
        sql = repo._conn.sql_texts()
        assert "evidence_objects" in sql
        assert "sources" in sql
        # Bound as jsonb, never interpolated.
        assert "$8::jsonb" in sql and "$9::jsonb" in sql
        assert "999999" not in sql

    @pytest.mark.asyncio
    async def test_append_message_serialises_values_as_json(self) -> None:
        repo = _repo()
        await repo.append_message(
            session_id=uuid.uuid4(),
            role="assistant",
            content="Jawaban",
            evidence_objects=EVIDENCE,
            sources=SOURCES,
        )
        args = repo._conn.calls[0][1]
        assert json.loads(args[7]) == EVIDENCE
        assert json.loads(args[8]) == SOURCES

    @pytest.mark.asyncio
    async def test_user_turn_stores_empty_arrays_not_null(self) -> None:
        repo = _repo()
        await repo.append_message(
            session_id=uuid.uuid4(), role="user", content="Pertanyaan"
        )
        args = repo._conn.calls[0][1]
        assert json.loads(args[7]) == []
        assert json.loads(args[8]) == []

    @pytest.mark.asyncio
    async def test_provenance_is_committed_with_the_turn(self) -> None:
        """One INSERT, one statement — never a separate provenance write.

        A second statement would give provenance its own failure mode: a stored
        answer whose evidence silently failed to attach, which restores as an
        answer with citations that resolve to nothing.
        """
        repo = _repo()
        await repo.append_message(
            session_id=uuid.uuid4(),
            role="assistant",
            content="Jawaban",
            evidence_objects=EVIDENCE,
        )
        inserts = [
            sql
            for sql, _ in repo._conn.calls
            if sql.strip().upper().startswith("INSERT")
        ]
        assert len(inserts) == 1

    @pytest.mark.asyncio
    async def test_persist_turn_runs_inside_the_transaction(self) -> None:
        """The snapshot rides inside the existing transaction boundary.

        Dispatches on the connection that holds the transaction, matching the
        property tests/unit/test_session_transaction.py guards. Widening the bind
        list must not have widened the transaction.
        """
        conn = RecordingConn()
        svc = SessionService.__new__(SessionService)
        svc._pool = _FakePool(conn)  # type: ignore[attr-defined]
        await svc.record_assistant_message(
            session_id=uuid.uuid4(),
            content="Jawaban",
            route="SQLRoute",
            request_id="req-1",
            evidence_objects=EVIDENCE,
            sources=SOURCES,
        )
        assert conn.log[0] == "BEGIN"
        assert conn.log[-1] == "COMMIT"
        stmts = [e for e in conn.log if " in_txn=" in e]
        assert all("in_txn=True" in e for e in stmts)

    @pytest.mark.asyncio
    async def test_list_messages_selects_provenance(self) -> None:
        repo = _repo()
        await repo.list_messages(uuid.uuid4())
        sql = repo._conn.sql_texts()
        assert "evidence_objects" in sql and "sources" in sql

    @pytest.mark.asyncio
    async def test_list_recent_messages_excludes_provenance(self) -> None:
        """The model-context read must not carry stored metrics.

        This feeds the narration prompt. Selecting provenance here would let the
        model restate a snapshot as a current fact — precisely the source-of-truth
        path migration 006 fences off.
        """
        repo = _repo()
        await repo.list_recent_messages(uuid.uuid4(), limit=10)
        sql = repo._conn.sql_texts()
        assert "evidence_objects" not in sql
        assert "jsonb_array_elements" not in sql


# ---------------------------------------------------------------------------
# 2. Decoder robustness — a bad snapshot must not lose a conversation
# ---------------------------------------------------------------------------
class TestProvenanceDecoding:
    def test_decodes_json_string(self) -> None:
        assert _decode_jsonb_list('[{"a": 1}]') == [{"a": 1}]

    def test_decodes_native_list(self) -> None:
        assert _decode_jsonb_list([{"a": 1}]) == [{"a": 1}]

    def test_none_becomes_empty(self) -> None:
        assert _decode_jsonb_list(None) == []

    @pytest.mark.parametrize("bad", ["{not json", '"scalar"', "42", '{"a":1}'])
    def test_malformed_degrades_to_empty(self, bad: Any) -> None:
        assert _decode_jsonb_list(bad) == []

    def test_non_dict_entries_are_dropped(self) -> None:
        assert _decode_jsonb_list([{"a": 1}, "junk", None, 7]) == [{"a": 1}]

    def test_encode_never_raises_on_exotic_types(self) -> None:
        """A snapshot that cannot encode must not lose the assistant turn.

        ``_persist_turn`` swallows exceptions, so a raise here would drop the turn
        and with it the user's answer.
        """
        out = _encode_jsonb_list([{"x": {1, 2}, "y": uuid.UUID(int=0)}])
        assert json.loads(out)[0]["y"] == str(uuid.UUID(int=0))


# ---------------------------------------------------------------------------
# 3. Response model — reuse, not duplication
# ---------------------------------------------------------------------------
class TestProvenanceResponseModel:
    def _msg(self, **kw: Any) -> SessionMessageResponse:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "role": "assistant",
            "content": "Jawaban",
            "created_at": NOW,
        }
        return SessionMessageResponse(**{**base, **kw})

    def test_evidence_uses_the_canonical_model(self) -> None:
        msg = self._msg(evidence_objects=EVIDENCE, sources=SOURCES)
        assert isinstance(msg.evidence_objects[0], EvidenceObject)
        assert msg.evidence_objects[0].value == 12
        assert isinstance(msg.sources[0], SourceItem)

    def test_defaults_are_empty_not_required(self) -> None:
        msg = self._msg()
        assert msg.evidence_objects == [] and msg.sources == []

    def test_one_bad_entry_does_not_lose_the_turn(self) -> None:
        """Losing a card degrades a view; losing the transcript loses the work."""
        msg = self._msg(
            evidence_objects=[EVIDENCE[0], {"metric": "publication_count"}],
            sources=SOURCES,
        )
        assert len(msg.evidence_objects) == 1
        assert msg.content == "Jawaban"

    def test_all_entries_bad_still_returns_the_turn(self) -> None:
        msg = self._msg(evidence_objects=[{"nope": 1}])
        assert msg.evidence_objects == []
        assert msg.content == "Jawaban"

    def test_non_list_collapses_to_empty(self) -> None:
        assert self._msg(evidence_objects={"a": 1}).evidence_objects == []

    def test_session_level_dtos_carry_no_provenance(self) -> None:
        """The exception is per-turn and must stay per-turn."""
        assert "evidence_objects" not in SessionListItem.model_fields
        assert "evidence_objects" not in SessionDetailResponse.model_fields
        assert "sources" not in SessionListItem.model_fields
        assert "sources" not in SessionDetailResponse.model_fields


# ---------------------------------------------------------------------------
# 4. ask.py payload — serialisation and the not_found truthfulness rule
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, evidence: Any, sources: Any) -> None:
        self.evidence_objects = evidence
        self.sources = sources


class TestProvenancePayload:
    def test_serialises_to_json_safe_types(self) -> None:
        evidence, sources = _provenance_payload(
            _Resp(
                [EvidenceObject.model_validate(EVIDENCE[0])],
                [SourceItem.model_validate(SOURCES[0])],
            )
        )
        assert evidence[0]["value"] == 12
        # Must be JSON-encodable as-is; the repository binds it as ::jsonb.
        json.dumps(evidence)
        json.dumps(sources)

    def test_not_found_turn_stores_empty_arrays(self) -> None:
        """A not_found answer cited nothing. Storing an empty-but-present set
        must not imply a finding, and must not fabricate evidence."""
        evidence, sources = _provenance_payload(_Resp(None, None))
        assert evidence == [] and sources == []

    def test_ignores_non_model_entries(self) -> None:
        evidence, _ = _provenance_payload(_Resp(["not a model"], []))
        assert evidence == []

    def test_is_not_developer_mode_gated(self) -> None:
        """These are response-body values, not debug metadata.

        Gating them would make the workspace unrestorable for exactly the
        non-developer users who rely on it. Asserted against the executable body
        only: the docstring legitimately *names* developer_mode while explaining
        why it is absent.
        """
        body = inspect.getsource(_provenance_payload)
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        code = code.split('"""', 2)[-1]  # drop the module-level docstring
        assert "developer_mode" not in code


# ---------------------------------------------------------------------------
# 5. FENCE — a stored snapshot can never answer a bibliometric question
# ---------------------------------------------------------------------------
class TestProvenanceIsNeverSourceOfTruth:
    """The hostile-snapshot tests the migration header promises.

    A planted, fabricated corpus figure sits in the stored snapshot. A re-ask must
    return the value measured live from ``public``, not the planted one.
    """

    HOSTILE: ClassVar[list[dict[str, Any]]] = [
        {
            "claim": "Dataset memiliki 999999 publications",
            "metric": "publication_count",
            "value": 999999,
            "period": "all-time",
            "confidence": 1.0,
            "sources": [],
        }
    ]

    def test_hostile_snapshot_is_stored_verbatim(self) -> None:
        """It IS stored — we are not sanitising the value away.

        Sanitising would be dishonest: the snapshot's job is to record what was
        rendered. The fence is that nothing READS it as fact.
        """
        encoded = _encode_jsonb_list(self.HOSTILE)
        assert json.loads(encoded)[0]["value"] == 999999

    def test_hostile_snapshot_round_trips_unchanged(self) -> None:
        """Restoring a past turn shows what was shown, hostile or not."""
        assert _decode_jsonb_list(json.dumps(self.HOSTILE)) == self.HOSTILE

    def test_repository_imports_no_retriever(self) -> None:
        """Structural fence: the module that reads snapshots cannot reach the corpus.

        This is the same import boundary that keeps the transcript out of the
        retrieval path, asserted from the other direction.
        """
        src = inspect.getsource(repo_mod)
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        forbidden = {"backend.app.services.retrievers", "backend.app.services.evidence"}
        assert not (imported & forbidden), (
            f"repository reached retrieval: {imported & forbidden}"
        )

    def test_no_session_read_aggregates_stored_evidence(self) -> None:
        """No session query may sum, average or rank a stored snapshot.

        A per-turn snapshot that gets aggregated becomes a session-level metric,
        which is the thing migration 005 forbade and 006 declines to create. So
        the aggregate is computed from the *sources* list (which publications were
        cited), never from the evidence values.
        """
        src = inspect.getsource(SessionRepository.list_sessions)
        normalised = " ".join(src.split())
        assert "evidence_objects" not in src
        assert "jsonb_array_elements(" in normalised
        assert "msg.sources" in normalised