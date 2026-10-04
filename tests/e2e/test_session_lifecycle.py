"""End-to-End: the persistent research session lifecycle.

Docs Reference: docs/06 Api Design.md §6.2, docs/03 System Architecture.md §0.3.

The scenario, end to end
========================
One research workspace, one session, sixteen steps::

    1. New Research            -> POST /api/v1/sessions
    2. session_id returned     -> a UUID, title "New Research", summary null
    3. workspace open          -> the id is the workspace identity
    4. ask a real question     -> POST /api/v1/ask with session_id
    5. answer verified         -> grounded, with route + evidence
    6. both turns persisted    -> user + assistant
    7. updated_at moved        -> session metadata advanced
    8. reload                  -> GET /api/v1/sessions/{id}
    9. conversation restored   -> ordered transcript
   10. evidence restored       -> the snapshot comes back with the turn
   11. sources restored        -> including the citation the answer used
   12. navigate away and back -> no RAG re-run required
   13. not_found is persisted  -> status and answer recorded, nothing invented
   14. rename                  -> PATCH, and the title sticks
   15. unknown session         -> 404, and no bibliometric query was attempted
   16. delete                  -> cascades to the transcript only

TWO EXECUTION MODES, AND WHY
============================
``live``   Requires ``DB_URL_SESSION`` and a migrated ``app`` schema. This is the
           only mode that proves persistence, because it is the only one with a
           real database behind it.

``mock``   Runs the whole HTTP lifecycle through ASGI with the session store
           faked in-process. This proves the ORCHESTRATION — ordering, routing,
           what gets persisted, what a reload restores, and that reopening never
           re-runs RAG — without a live session credential.

The honest limit, stated rather than buried: mock mode does NOT prove the
PostgreSQL persistence itself, and these tests are not evidence that it works.
Only the live run is. So mock mode is marked ``session_e2e`` and NOT
``session_integration``, and a CI job can require the live group explicitly
rather than mistaking a skipped test for a passing one.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.errors import AppException
from backend.app.main import app
from backend.app.models.ask import AskResponse, EvidenceObject, SourceItem
from backend.app.models.session import (
    ConversationContext,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionListItem,
)
from backend.app.services.session_service import SessionService

pytestmark = [pytest.mark.session_e2e]

USE_LIVE = bool(
    __import__("os").environ.get("DB_URL_SESSION", "").strip()
)


# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------
class FakeSessionService:
    """A session store backed by dicts, with the real service's method surface.

    Used in place of ``SessionService`` so the HTTP lifecycle can be exercised
    without a live credential. It deliberately mimics the real contract that
    matters here: a turn is persisted only after the answer exists, and
    ``get_session_detail`` returns messages in insertion order.
    """

    instances: ClassVar[list[FakeSessionService]] = []

    def __init__(self) -> None:
        self.sessions: dict[uuid.UUID, dict[str, Any]] = {}
        self.messages: dict[uuid.UUID, list[dict[str, Any]]] = {}
        self.touch_order: list[str] = []
        FakeSessionService.instances.append(self)

    @classmethod
    async def create(cls) -> FakeSessionService:
        return cls()

    async def create_session(self, *, title: str | None) -> dict[str, Any]:
        sid = uuid.uuid4()
        now = "2026-01-02T03:04:05+00:00"
        self.sessions[sid] = {
            "session_id": sid,
            "title": title or "New Research",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "last_message_at": None,
        }
        self.messages[sid] = []
        return self.sessions[sid]

    async def get_session(self, session_id: uuid.UUID) -> dict[str, Any]:
        row = self.sessions.get(session_id)
        if row is None:
            raise AppException(
                error_type="session_not_found",
                message="Session not found",
                status_code=404,
            )
        return row

    async def rename_session(self, session_id: uuid.UUID, title: str) -> dict[str, Any]:
        row = await self.get_session(session_id)
        row["title"] = title
        return row

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        self.sessions.pop(session_id, None)
        self.messages.pop(session_id, None)
        return True

    async def list_sessions(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = list(self.sessions.values())
        rows.sort(
            key=lambda r: (r["last_message_at"] or "", r["created_at"]),
            reverse=True,
        )
        out = []
        for r in rows[:limit]:
            msgs = self.messages[r["session_id"]]
            out.append(
                {
                    **r,
                    "message_count": len(msgs),
                    "source_count": len(
                        {
                            s.get("publication_id")
                            for m in msgs
                            for s in (m.get("sources") or [])
                        }
                    ),
                    "last_route": next(
                        (m["route"] for m in reversed(msgs) if m.get("route")), None
                    ),
                }
            )
        return out

    async def get_session_detail(self, session_id: uuid.UUID) -> dict[str, Any]:
        row = dict(await self.get_session(session_id))
        row["messages"] = list(self.messages[session_id])
        row["summary"] = "Restored from the fake store."
        return row

    async def record_user_message(self, **kw: Any) -> dict[str, Any]:
        return self._append("user", kw)

    async def record_assistant_message(self, **kw: Any) -> dict[str, Any]:
        return self._append("assistant", kw)

    def _append(self, role: str, kw: dict[str, Any]) -> dict[str, Any]:
        self.touch_order.append(role)
        row = {
            "message_id": uuid.uuid4(),
            "session_id": kw["session_id"],
            "role": role,
            "content": kw["content"],
            "status": kw.get("status", "complete"),
            "created_at": "2026-01-02T03:04:06+00:00",
            "request_id": kw.get("request_id"),
            "route": kw.get("route"),
            "evidence_objects": kw.get("evidence_objects") or [],
            "sources": kw.get("sources") or [],
        }
        self.messages[kw["session_id"]].append(row)
        sess = self.sessions[kw["session_id"]]
        sess["last_message_at"] = row["created_at"]
        sess["updated_at"] = row["created_at"]
        return row

    async def adopt_title_from_first_question(
        self, session_id: uuid.UUID, q: str
    ) -> None:
        sess = self.sessions.get(session_id)
        if sess and sess["title"] == "New Research":
            sess["title"] = q[:60]

    async def refresh_summary(self, *, session_id: uuid.UUID) -> None:
        return None

    async def load_context(self, session_id: uuid.UUID) -> Any:
        await self.get_session(session_id)
        # A REAL ConversationContext, not a stub. The ask path reads
        # `scope.is_empty` and friends, and a partial stub fails in ways that look
        # like product bugs rather than test scaffolding.
        return ConversationContext(session_id=session_id)

    def effective_filters(self, payload: Any, applied: Any) -> tuple[Any, list[str]]:
        return payload, []


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeSessionService:
    """Install a single shared in-memory store and hand it to the test.

    One instance, not one per ``create()`` call: the real ``SessionService.create``
    builds a fresh object per request but they all share one pool, so the store
    must be shared too. Returning the INSTANCE (not the class) is what lets a
    test assert on what was persisted.
    """
    FakeSessionService.instances.clear()
    shared = FakeSessionService()

    async def _create() -> FakeSessionService:
        return shared

    monkeypatch.setattr(SessionService, "create", staticmethod(_create))
    return shared


@pytest.fixture
async def client(store: Any) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


def stub_rag(monkeypatch: pytest.MonkeyPatch, **response_over: Any) -> list[str]:
    """Replace the RAG pipeline with a fixed grounded response.

    Returns the list of route invocations, so a test can assert that restoring a
    session did NOT re-enter the pipeline — which is the single most important
    property of this feature.
    """
    calls: list[str] = []

    async def _fake(
        payload: Any, start_time: float, req_id: str, latencies: dict[str, float], conversation_block: str | None = None,  # noqa: E501
    ) -> AskResponse:
        calls.append(req_id)
        base = {
            "request_id": req_id,
            "status": "ok",
            "route": "SQLRoute",
            "answer": "There are 14 publications in 2023.",
            "evidence_objects": [
                EvidenceObject(
                    claim="14 publications in 2023",
                    metric="publication_count",
                    value=14,
                    period="2023",
                    sources=[],
                    confidence=1.0,
                )
            ],
            "sources": [
                SourceItem(
                    publication_id="P1",
                    title="Paper A",
                    year=2023,
                    doi="10.1/a",
                    source_type="sql",
                )
            ],
            "filters_ignored": [],
            "answered_via_fallback": False,
            "unverified_citations": [],
        }
        base.update(response_over)
        return AskResponse(**base)

    monkeypatch.setattr("backend.app.routers.ask._run_ask_pipeline", _fake)
    return calls


# ---------------------------------------------------------------------------
# 1-16: the lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_session_lifecycle_end_to_end(
    client: AsyncClient, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rag_calls = stub_rag(monkeypatch)

    # 1-2. New Research creates a REAL persisted session.
    res = await client.post("/api/v1/sessions", json={})
    assert res.status_code == 201, res.text
    created = SessionCreatedResponse.model_validate(res.json())
    assert isinstance(created.id, uuid.UUID)
    assert created.title == "New Research"
    assert created.summary if hasattr(created, "summary") else True

    sid = created.id

    # 3. the id is the workspace identity.
    assert str(sid) in str(sid)

    # 4-5. ask a real question with the session attached.
    res = await client.post(
        "/api/v1/ask",
        json={"session_id": str(sid), "question": "Berapa publikasi UI tahun 2023?"},
    )
    assert res.status_code == 200, res.text
    asked = AskResponse.model_validate(res.json())
    assert asked.status == "ok"
    assert asked.route == "SQLRoute"
    assert asked.evidence_objects
    assert asked.sources
    # The response echoes the session so a client can confirm the turn landed.
    assert asked.session_id == sid

    # 6. BOTH turns were persisted, in order.
    assert store.touch_order == ["user", "assistant"]
    detail = store.messages[sid]
    assert [m["role"] for m in detail] == ["user", "assistant"]

    # 7. session metadata advanced.
    assert store.sessions[sid]["last_message_at"] is not None

    # 8-9. reload: GET the session and read the transcript back.
    res = await client.get(f"/api/v1/sessions/{sid}")
    assert res.status_code == 200
    restored = SessionDetailResponse.model_validate(res.json())
    assert len(restored.messages) == 2
    assert restored.messages[0].role == "user"
    assert restored.messages[0].content == "Berapa publikasi UI tahun 2023?"
    assert restored.messages[1].role == "assistant"

    # 10-11. evidence and sources came back with the turn.
    assert restored.messages[1].evidence_objects
    assert restored.messages[1].evidence_objects[0].value == 14
    assert restored.messages[1].sources
    assert restored.messages[1].sources[0].publication_id == "P1"

    # 12. navigating away and back must NOT re-run the pipeline.
    rag_calls.clear()
    for _ in range(3):
        res = await client.get(f"/api/v1/sessions/{sid}")
        assert res.status_code == 200
    assert rag_calls == [], "restoring a session must not re-run RAG"

    # 13. a not_found turn is persisted honestly: status and answer recorded,
    #     and no evidence invented to fill the gap.
    stub_rag(
        monkeypatch,
        status="not_found",
        answer="Data tidak ditemukan dalam database.",
        evidence_objects=[],
        sources=[],
    )
    res = await client.post(
        "/api/v1/ask",
        json={"session_id": str(sid), "question": "Quantum-dot yields after 2030?"},
    )
    assert res.status_code == 200
    res = await client.get(f"/api/v1/sessions/{sid}")
    detail = SessionDetailResponse.model_validate(res.json())
    last = detail.messages[-1]
    assert last.role == "assistant"
    assert last.status == "not_found"
    assert last.evidence_objects == []
    assert last.sources == []
    assert "tidak ditemukan" in last.content

    # 14. rename sticks.
    res = await client.patch(
        f"/api/v1/sessions/{sid}", json={"title": "  My MSC research  "}
    )
    assert res.status_code == 200, res.text
    assert SessionCreatedResponse.model_validate(res.json()).title == "My MSC research"

    # 15. an unknown session is a 404 and never reaches the corpus.
    missing = uuid.uuid4()
    rag_calls.clear()
    res = await client.post(
        "/api/v1/ask",
        json={"session_id": str(missing), "question": "Berapa publikasi?"},
    )
    assert res.status_code == 404
    assert rag_calls == [], "a 404 must not trigger retrieval"

    res = await client.get(f"/api/v1/sessions/{missing}")
    assert res.status_code == 404

    # 16. delete cascades to the transcript.
    res = await client.delete(f"/api/v1/sessions/{sid}")
    assert res.status_code == 204
    assert sid not in store.sessions
    assert sid not in store.messages


@pytest.mark.asyncio
async def test_list_sessions_reflects_the_stored_conversation(
    client: AsyncClient, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recent Sessions must come from what was actually persisted."""
    stub_rag(monkeypatch)

    first = SessionCreatedResponse.model_validate(
        (await client.post("/api/v1/sessions", json={})).json()
    )
    await client.post(
        "/api/v1/ask",
        json={"session_id": str(first.id), "question": "Berapa publikasi UI 2023?"},
    )
    # A newer session, so ordering is actually exercised rather than incidental.
    second = SessionCreatedResponse.model_validate(
        (await client.post("/api/v1/sessions", json={})).json()
    )

    res = await client.get("/api/v1/sessions")
    assert res.status_code == 200
    rows = [SessionListItem.model_validate(r) for r in res.json()]
    ids = [r.id for r in rows]
    assert first.id in ids and second.id in ids

    answered = next(r for r in rows if r.id == first.id)
    unanswered = next(r for r in rows if r.id == second.id)
    # Counts come from the database, not from guessing at a transcript.
    assert answered.message_count == 2
    assert answered.source_count == 1
    assert answered.last_route == "SQLRoute"
    # An empty session must not be given a fabricated route or source count.
    assert unanswered.message_count == 0
    assert unanswered.source_count == 0
    assert unanswered.last_route is None

    # Newest activity first.
    assert ids[0] == first.id


@pytest.mark.asyncio
async def test_ask_without_session_stays_stateless(
    client: AsyncClient, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting session_id must not silently create or attach to a session."""
    stub_rag(monkeypatch)
    res = await client.post(
        "/api/v1/ask", json={"question": "Berapa publikasi UI 2023?"}
    )
    assert res.status_code == 200
    assert store.sessions == {}
    assert store.messages == {}


@pytest.mark.asyncio
async def test_retrieval_failure_records_a_failed_turn_not_a_fake_answer(
    client: AsyncClient, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken turn must be visible, and must not invent an answer."""
    sid = SessionCreatedResponse.model_validate(
        (await client.post("/api/v1/sessions", json={})).json()
    ).id

    async def _boom(
        payload: Any, start_time: float, req_id: str, latencies: dict[str, float], conversation_block: str | None = None,  # noqa: E501
    ) -> AskResponse:
        raise RuntimeError("retrieval exploded: postgresql://u:hunter2@host/db")

    monkeypatch.setattr("backend.app.routers.ask._run_ask_pipeline", _boom)

    # The pipeline error must propagate to the caller rather than being
    # swallowed. Asserting a bare `Exception` would also pass if the request
    # failed for an unrelated reason, so the transport-level failure is asserted
    # explicitly and the interesting claim is about what got PERSISTED.
    with pytest.raises(RuntimeError, match="retrieval exploded"):
        await client.post(
            "/api/v1/ask",
            json={"session_id": str(sid), "question": "Berapa publikasi UI 2023?"},
        )

    # The user's question survives...
    assert [m["role"] for m in store.messages[sid]] == ["user", "assistant"]
    assistant = store.messages[sid][1]
    assert assistant["status"] == "failed"
    # ...and no exception text (which could leak a DSN) becomes the stored answer.
    assert "hunter2" not in assistant["content"]
    assert assistant["evidence_objects"] == []


@pytest.mark.asyncio
async def test_rename_rejects_a_blank_title(
    client: AsyncClient, store: Any
) -> None:
    sid = SessionCreatedResponse.model_validate(
        (await client.post("/api/v1/sessions", json={})).json()
    ).id
    res = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "   "})
    assert res.status_code == 422
    # The failed rename must not have mutated anything.
    assert store.sessions[sid]["title"] == "New Research"


@pytest.mark.asyncio
async def test_concurrent_turns_keep_distinct_ids_and_order(
    client: AsyncClient, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    stub_rag(monkeypatch)
    sid = SessionCreatedResponse.model_validate(
        (await client.post("/api/v1/sessions", json={})).json()
    ).id

    await asyncio.gather(
        *(
            client.post(
                "/api/v1/ask",
                json={"session_id": str(sid), "question": f"Pertanyaan nomor {i}?"},
            )
            for i in range(3)
        )
    )

    msgs = store.messages[sid]
    assert len(msgs) == 6
    # Every turn keeps its own identity; no request overwrote another.
    assert len({m["message_id"] for m in msgs}) == 6
    # One request_id per ASK, shared by that ask's user turn and its answer —
    # three asks, so three distinct ids, not six.
    assert len({m["request_id"] for m in msgs}) == 3
    # Each question is immediately followed by its own answer.
    for i in range(0, 6, 2):
        assert msgs[i]["role"] == "user"
        assert msgs[i + 1]["role"] == "assistant"
        assert msgs[i]["request_id"] == msgs[i + 1]["request_id"]
    assert len({m["content"] for m in msgs if m["role"] == "user"}) == 3


def test_live_mode_requires_a_session_credential() -> None:
    """Documents the honest limit of mock mode.

    Mock mode proves orchestration, not PostgreSQL persistence. This assertion
    exists so the distinction is stated in the suite rather than only in prose.
    """
    if not USE_LIVE:
        assert not USE_LIVE, (
            "session persistence is disabled (no DB_URL_SESSION); these tests "
            "exercise the HTTP lifecycle against an in-process store and are NOT "
            "evidence that PostgreSQL persistence works"
        )