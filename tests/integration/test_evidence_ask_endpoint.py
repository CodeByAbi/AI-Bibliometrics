"""Integration tests for POST /api/v1/ask through the Phase 5 Evidence Layer.

All tests mock the DB pool (no live Supabase connection required) and verify
the canonical flow:
    Retrieval (SqlRetriever / VectorRetriever)
        → EvidenceUnifier.from_sql / from_vector (normalized EvidenceSet)
        → AnswerSynthesizer (reads ONLY EvidenceSet, never raw rows)
        → CitationVerifier
        → AskResponse (evidence_objects + sources + debug.evidence_set)

Docs Reference: docs/11 Roadmap.md Fase 5 (Task 7), docs/05 §4, docs/06 §5.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.models.ask import CandidateItem
from backend.app.services.retrievers.sql_retriever import SqlRetrievalResult
from backend.app.services.retrievers.vector_retriever import (
    VectorMatchItem,
    VectorRetrievalResult,
)
from backend.app.services.router import EntityResolutionResult, RouteDecision


class _FakeConn:
    """Pool-connection proxy consumed by ``async with pool.acquire() as conn``."""

    def __init__(self, inner: AsyncMock) -> None:
        self._inner = inner
        self._cm = inner.__aenter__()

    async def __aenter__(self) -> AsyncMock:
        return await self._cm

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._inner.__aexit__(exc_type, exc, tb)


def _fake_pool(conn: AsyncMock) -> MagicMock:
    def acquire() -> "_FakeConn":
        return _FakeConn(conn)

    pool = MagicMock()
    pool.acquire.side_effect = acquire
    return pool


def _entity_ok() -> EntityResolutionResult:
    return EntityResolutionResult(
        status="ok",
        resolved_author_id=None,
        resolved_author_name=None,
        resolved_institution_id=None,
        resolved_institution_name=None,
    )


def _patch_gate(resolution: EntityResolutionResult, monkeypatch) -> None:
    """Patch EntityResolutionGate.resolve_entities to return a fixed resolution (no DB read)."""
    import backend.app.routers.ask as ask_module

    async def _resolve(conn, question: str, filters=None) -> EntityResolutionResult:
        return resolution

    monkeypatch.setattr(ask_module.EntityResolutionGate, "resolve_entities", staticmethod(_resolve))


async def _post(client: AsyncClient, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = await client.post("/api/v1/ask", json=body)
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_evidence_layer_sql_route_returns_canonical_evidence_objects(monkeypatch):
    """SQLRoute: evidence_objects + sources are produced via EvidenceUnifier.from_sql."""
    sql_result = SqlRetrievalResult(
        sql_executed="SELECT COUNT(DISTINCT p.publication_id) AS total_publications FROM publications p",
        columns=["total_publications"],
        rows=[{"total_publications": 42}],
        row_count=1,
        filters_ignored=[],
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=sql_result),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(client, {"question": "Berapa total publikasi pada tahun 2025?"})

    assert data["status"] == "ok"
    assert data["route"] == "SQLRoute"
    assert len(data["evidence_objects"]) == 1
    ev = data["evidence_objects"][0]
    assert ev["metric"] == "publication_count"
    assert ev["value"] == 42
    assert ev["confidence"] == 1.0
    assert "42" in ev["claim"]
    assert data["sources"] == []
    assert data["unverified_citations"] == []


@pytest.mark.asyncio
async def test_evidence_layer_vector_route_returns_canonical_evidence_objects(monkeypatch):
    """VectorRoute: sources + evidence_objects are produced via EvidenceUnifier.from_vector."""
    vec_result = VectorRetrievalResult(
        matches=[
            VectorMatchItem(
                publication_id="PUB_A",
                title="Oxidative Stress Review",
                year=2023,
                doi="10.1000/ox.1",
                eid="2-s2.0-111",
                citation_count=15,
                chunk_id="PUB_A_CH1",
                chunk_text="Oxidative stress markers in mesenchymal stromal cells.",
                similarity_score=0.87,
            ),
            VectorMatchItem(
                publication_id="PUB_B",
                title="Stem Cell Therapy",
                year=2022,
                doi=None,
                eid=None,
                citation_count=3,
                chunk_id="PUB_B_CH1",
                chunk_text="Stem cell therapy for cartilage.",
                similarity_score=0.71,
            ),
        ],
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT DISTINCT ON (p.publication_id) ...",
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.VectorRetriever.retrieve",
        AsyncMock(return_value=vec_result),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(client, {"question": "Paper yang membahas stres oksidatif pada Wharton's jelly"})

    assert data["status"] == "ok"
    assert data["route"] == "VectorRoute"
    # Deterministic rank: highest similarity first.
    assert [s["publication_id"] for s in data["sources"]] == ["PUB_A", "PUB_B"]
    assert data["sources"][0]["source_type"] == "vector"
    assert data["sources"][0]["relevance_score"] == 0.87
    assert data["sources"][0]["provenance"] == "chunk_id:PUB_A_CH1"

    ev = data["evidence_objects"][0]
    assert ev["metric"] == "similarity_score"
    assert ev["value"] == 0.87
    assert ev["confidence"] == 0.87
    # Canonical citation tag rendered from the normalized EvidenceObject.
    assert "[Oxidative Stress Review, 2023, 10.1000/ox.1]" in data["answer"]
    assert data["unverified_citations"] == []


@pytest.mark.asyncio
async def test_evidence_layer_zero_match_short_circuit_not_found(monkeypatch):
    """0 evidence → status not_found, 0 LLM call, empty evidence_objects (AC-RAG-4)."""
    empty_sql = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) FROM publications",
        columns=[],
        rows=[],
        row_count=0,
        filters_ignored=[],
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=empty_sql),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(client, {"question": "Daftar publikasi pada tahun 1950"})

    assert data["status"] == "not_found"
    assert data["route"] == "SQLRoute"
    assert data["evidence_objects"] == []
    assert data["sources"] == []
    assert "tidak ditemukan" in data["answer"].lower()
    assert data["unverified_citations"] == []


@pytest.mark.asyncio
async def test_evidence_layer_needs_clarification_short_circuit(monkeypatch):
    """Ambiguous entity gate short-circuits before any retrieval (evidence empty)."""
    candidates = [
        CandidateItem(id="AUTH_1", name="J. Wang A", type="author", publication_count=3),
        CandidateItem(id="AUTH_2", name="J. Wang B", type="author", publication_count=2),
    ]
    resolution = EntityResolutionResult(
        status="needs_clarification",
        candidates=candidates,
        clarification_message="Beberapa penulis cocok dengan 'J. Wang'.",
    )
    _patch_gate(resolution, monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(client, {"question": "Berapa publikasi dari J. Wang?"})

    assert data["status"] == "needs_clarification"
    assert data["evidence_objects"] == []
    assert data["sources"] == []
    assert data["candidates"] is not None
    assert len(data["candidates"]) == 2


@pytest.mark.asyncio
async def test_evidence_layer_filters_ignored_preserved_through_set(monkeypatch):
    """filters_ignored from the retrieval result is propagated into AskResponse verbatim."""
    sql_result = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) AS total_publications FROM publications",
        columns=["total_publications"],
        rows=[{"total_publications": 7}],
        row_count=1,
        filters_ignored=["topic_name", "keyword"],
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=sql_result),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(
                client,
                {
                    "question": "Berapa total publikasi?",
                    "filters": {"topic_name": "Stem Cell", "keyword": "oxidative"},
                },
            )

    assert data["status"] == "ok"
    assert "topic_name" in data["filters_ignored"]
    assert "keyword" in data["filters_ignored"]


@pytest.mark.asyncio
async def test_evidence_layer_developer_mode_exposes_evidence_set(monkeypatch):
    """developer_mode=true surfaces the normalized EvidenceSet in debug.evidence_set."""
    sql_result = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) AS total_publications FROM publications",
        columns=["total_publications"],
        rows=[{"total_publications": 5}],
        row_count=1,
        filters_ignored=[],
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=sql_result),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(
                client,
                {"question": "Berapa total publikasi?", "developer_mode": True},
            )

    assert data["debug"] is not None
    breakdown = data["debug"]["latency_breakdown_ms"]
    for key in ("routing_ms", "entity_resolution_ms", "sql_retrieval_ms", "evidence_unify_ms", "synthesis_ms", "total_ms"):
        assert key in breakdown
    ev_set = data["debug"]["evidence_set"]
    assert ev_set is not None
    assert ev_set["query"] == "Berapa total publikasi?"
    assert len(ev_set["evidence_objects"]) == 1
    assert ev_set["evidence_objects"][0]["metric"] == "publication_count"
    assert ev_set["evidence_objects"][0]["value"] == 5
    assert ev_set["is_empty"] is False
    assert "COUNT" in (ev_set["sql_executed"] or "")


@pytest.mark.asyncio
async def test_evidence_layer_developer_mode_off_omits_debug(monkeypatch):
    """developer_mode default (off) never exposes debug metadata."""
    sql_result = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) AS total_publications FROM publications",
        columns=["total_publications"],
        rows=[{"total_publications": 5}],
        row_count=1,
        filters_ignored=[],
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=sql_result),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(client, {"question": "Berapa total publikasi?"})

    assert data["debug"] is None
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_evidence_layer_deterministic_repeated_calls(monkeypatch):
    """Two identical requests yield byte-identical evidence ordering (deterministic ranker)."""
    vec_result = VectorRetrievalResult(
        matches=[
            VectorMatchItem(
                publication_id="PUB_X",
                title="Alpha",
                year=2020,
                doi="10.1/x",
                eid=None,
                citation_count=1,
                chunk_id="PUB_X_C1",
                chunk_text="alpha chunk",
                similarity_score=0.7,
            ),
            VectorMatchItem(
                publication_id="PUB_Y",
                title="Beta",
                year=2021,
                doi="10.2/y",
                eid=None,
                citation_count=2,
                chunk_id="PUB_Y_C1",
                chunk_text="beta chunk",
                similarity_score=0.9,
            ),
        ],
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT ...",
    )
    retrieve_mock = AsyncMock(return_value=vec_result)
    monkeypatch.setattr("backend.app.routers.ask.VectorRetriever.retrieve", retrieve_mock)
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            d1 = await _post(client, {"question": "Alpha beta papers"})
            d2 = await _post(client, {"question": "Alpha beta papers"})

    # Higher similarity (PUB_Y, 0.9) must rank first both times.
    assert [s["publication_id"] for s in d1["sources"]] == ["PUB_Y", "PUB_X"]
    assert [s["publication_id"] for s in d2["sources"]] == ["PUB_Y", "PUB_X"]
    assert [e["value"] for e in d1["evidence_objects"]] == [e["value"] for e in d2["evidence_objects"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,fallback",
    [
        ("SQLRoute", False),
        ("SQLRoute", True),
        ("VectorRoute", False),
        ("VectorRoute", True),
        ("GraphRoute", False),
        ("HybridRoute", True),
    ],
)
async def test_evidence_layer_all_branches_carry_reasoning_and_fallback(
    monkeypatch, route: str, fallback: bool
):
    """Every ask.py branch returns route_reasoning + answered_via_fallback honestly.

    Covers: entity not_found / needs_clarification short-circuits, SQLRoute ok +
    empty, VectorRoute ok + empty, and Graph/Hybrid honest stubs. developer_mode
    must always surface a non-empty route_reasoning; the top-level
    answered_via_fallback must mirror the router decision on every branch.
    """
    import backend.app.routers.ask as ask_module

    reasoning = f"test reasoning for {route} (fallback={fallback})"
    decision = RouteDecision(route=route, reasoning=reasoning, answered_via_fallback=fallback)  # type: ignore[arg-type]
    monkeypatch.setattr(
        ask_module.QuestionRouter,
        "classify_route",
        classmethod(lambda cls, q, f=None: decision),
    )

    sql_ok = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) AS total_publications FROM publications",
        columns=["total_publications"],
        rows=[{"total_publications": 3}],
        row_count=1,
    )
    sql_empty = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) FROM publications",
        columns=[],
        rows=[],
        row_count=0,
    )
    vec_ok = VectorRetrievalResult(
        matches=[
            VectorMatchItem(
                publication_id="PUB_T",
                title="Branch Coverage Study",
                year=2023,
                doi="10.1/branch",
                eid=None,
                citation_count=1,
                chunk_id="PUB_T_C1",
                chunk_text="branch coverage chunk",
                similarity_score=0.8,
            )
        ],
        threshold=0.65,
        filters_ignored=[],
        sql_executed="SELECT ...",
    )
    vec_empty = VectorRetrievalResult(
        matches=[], threshold=0.65, filters_ignored=[], sql_executed="SELECT ..."
    )
    monkeypatch.setattr(
        ask_module.SqlRetriever, "retrieve", AsyncMock(return_value=sql_ok)
    )
    monkeypatch.setattr(
        ask_module.VectorRetriever, "retrieve", AsyncMock(return_value=vec_ok)
    )

    ok_gate = EntityResolutionResult(status="ok")
    not_found_gate = EntityResolutionResult(
        status="not_found", clarification_message="Tidak ditemukan."
    )
    clarify_gate = EntityResolutionResult(
        status="needs_clarification",
        candidates=[CandidateItem(id="A1", name="A One", type="author", publication_count=1)],
        clarification_message="Pilih salah satu.",
    )

    scenarios = [
        ("entity_not_found", not_found_gate, None, None, "not_found"),
        ("entity_clarify", clarify_gate, None, None, "needs_clarification"),
        ("retrieval_ok", ok_gate, None, None, "ok"),
        ("retrieval_empty", ok_gate, "empty", None, "not_found"),
    ]
    if route in ("GraphRoute", "HybridRoute"):
        scenarios = [s for s in scenarios if s[0] in ("entity_not_found", "entity_clarify", "retrieval_ok")]
        # Stub branches always answer not_found regardless of gate-ok.
        scenarios = [
            ("entity_not_found", not_found_gate, None, None, "not_found"),
            ("entity_clarify", clarify_gate, None, None, "needs_clarification"),
            ("stub", ok_gate, None, None, "not_found"),
        ]

    for name, gate, variant, _unused, expected_status in scenarios:
        if variant == "empty":
            if route == "SQLRoute":
                monkeypatch.setattr(
                    ask_module.SqlRetriever, "retrieve", AsyncMock(return_value=sql_empty)
                )
            else:
                monkeypatch.setattr(
                    ask_module.VectorRetriever, "retrieve", AsyncMock(return_value=vec_empty)
                )
        else:
            monkeypatch.setattr(
                ask_module.SqlRetriever, "retrieve", AsyncMock(return_value=sql_ok)
            )
            monkeypatch.setattr(
                ask_module.VectorRetriever, "retrieve", AsyncMock(return_value=vec_ok)
            )
        _patch_gate(gate, monkeypatch)

        with patch(
            "backend.app.routers.ask.get_pool",
            new=AsyncMock(return_value=_fake_pool(AsyncMock())),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                data = await _post(
                    client, {"question": "branch coverage probe?", "developer_mode": True}
                )

        assert data["route"] == route, f"{route}/{name}: route mismatch"
        assert data["status"] == expected_status, f"{route}/{name}: status mismatch"
        assert data["answered_via_fallback"] is fallback, f"{route}/{name}: fallback flag"
        assert isinstance(data["answered_via_fallback"], bool)
        assert data["debug"] is not None, f"{route}/{name}: debug missing"
        assert data["debug"]["route_reasoning"] == reasoning, f"{route}/{name}: reasoning"
        lat = data["debug"]["latency_breakdown_ms"]
        for key in ("routing_ms", "entity_resolution_ms", "total_ms"):
            assert key in lat, f"{route}/{name}: latency {key} missing"
        if route in ("SQLRoute", "VectorRoute") and gate.status == "ok":
            assert "evidence_unify_ms" in lat, f"{route}/{name}: evidence_unify_ms missing"


@pytest.mark.asyncio
async def test_evidence_layer_zero_match_developer_mode_exposes_empty_set(monkeypatch):
    """Empty EvidenceSet short-circuit still exposes evidence_unify_ms + empty set in debug."""
    empty_sql = SqlRetrievalResult(
        sql_executed="SELECT COUNT(*) FROM publications",
        columns=[],
        rows=[],
        row_count=0,
        filters_ignored=[],
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.SqlRetriever.retrieve",
        AsyncMock(return_value=empty_sql),
    )
    _patch_gate(_entity_ok(), monkeypatch)

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(AsyncMock()))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = await _post(
                client,
                {"question": "Daftar publikasi pada tahun 1950", "developer_mode": True},
            )

    assert data["status"] == "not_found"
    assert data["evidence_objects"] == []
    assert data["sources"] == []
    assert data["debug"] is not None
    assert "evidence_unify_ms" in data["debug"]["latency_breakdown_ms"]
    ev_set = data["debug"]["evidence_set"]
    assert ev_set is not None
    assert ev_set["is_empty"] is True
