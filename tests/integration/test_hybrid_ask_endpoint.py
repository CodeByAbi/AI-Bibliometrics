"""Integration tests for HybridRoute on POST /api/v1/ask endpoint.

Docs Reference: docs/05 Retrieval Rag Design.md §5.4; docs/06 Api Design.md §5;
docs/10 Implementation Plan.md (Task 8.5, Task 9-full, Task 10); docs/11 Roadmap.md (Fase 7).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.models.ask import CandidateItem
from backend.app.services.retrievers.hybrid_retriever import (
    HybridExpertItem,
    HybridPublicationMeta,
    HybridRetrievalResult,
    HybridTopicEvolutionItem,
)
from backend.app.services.router import EntityResolutionResult


class _FakeConn:
    """Async context manager wrapper returned by pool.acquire()."""

    def __init__(self, conn: AsyncMock):
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        pass


def _fake_pool(conn: AsyncMock) -> MagicMock:
    """Create a mock pool whose acquire() synchronously returns an async context manager."""
    pool = MagicMock()
    pool.acquire.side_effect = lambda: _FakeConn(conn)
    return pool


@pytest.mark.asyncio
async def test_ask_endpoint_hybrid_route_topic_trends_ok(monkeypatch):
    """Verify HybridRoute handles topic trends queries returning verified evolution metrics."""
    fake_conn = AsyncMock()

    mock_topics = [
        HybridTopicEvolutionItem(
            topic_id=1,
            topic_name="Mesenchymal Stem Cells & Inflammation",
            year=2024,
            publication_count=12,
            citation_count=45,
            growth_score=0.40,
            citation_acceleration=0.15,
            recency_weight=1.0,
            is_emerging=True,
        )
    ]
    hybrid_res = HybridRetrievalResult(
        intent_type="TOPIC_TRENDS",
        topics=mock_topics,
        experts=[],
        publications={},
        sql_executed="TEMPLATE: SQL_GOLD_ANALYTICS (intent='TOPIC_TRENDS')",
        target_topic_name="Mesenchymal Stem Cells & Inflammation",
        filters_ignored=[],
    )

    async def _mock_retrieve(*_args, **_kwargs):
        return hybrid_res

    monkeypatch.setattr(
        "backend.app.routers.ask.HybridRetriever.retrieve",
        _mock_retrieve,
    )

    async def _mock_gate(*_args, **_kwargs):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        _mock_gate,
    )

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(fake_conn))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Bagaimana tren perkembangan terapi stem cell 5 tahun terakhir?",
                    "developer_mode": True,
                },
            )

    assert resp.status_code == 200
    data = resp.json()

    assert data["route"] == "HybridRoute"
    assert data["status"] == "ok"
    assert len(data["evidence_objects"]) >= 1
    assert data["evidence_objects"][0]["metric"] == "growth_score"
    assert data["evidence_objects"][0]["value"] == 0.40
    assert "Mesenchymal Stem Cells & Inflammation" in data["answer"]
    assert data["unverified_citations"] == []

    # Check latencies breakdown
    assert data["debug"] is not None
    lat = data["debug"]["latency_breakdown_ms"]
    assert "hybrid_retrieval_ms" in lat
    assert "evidence_unify_ms" in lat
    assert "synthesis_ms" in lat
    assert "total_ms" in lat
    assert data["debug"]["evidence_set"] is not None


@pytest.mark.asyncio
async def test_ask_endpoint_hybrid_route_expert_ranking_with_citations_ok(monkeypatch):
    """Verify HybridRoute handles expert ranking queries returning citations and source refs."""
    fake_conn = AsyncMock()

    mock_experts = [
        HybridExpertItem(
            author_id="AUTH_101",
            author_name="Hardjo, Marhaen",
            topic_id=2,
            topic_name="Phytochemicals & Molecular Docking",
            expertise_score=88.5,
            relevance_score=92.0,
            productivity_score=85.0,
            impact_score=88.0,
            recency_score=90.0,
            h_index_topic=6,
            publication_count_topic=9,
            citation_count_topic=50,
            coauthor_network_size=11,
        )
    ]
    mock_pubs = {
        "PUB_PHYTO_1": HybridPublicationMeta(
            publication_id="PUB_PHYTO_1",
            title="Ethanol Extract of Cosmos Caudatus Attenuates Oxidative Stress",
            year=2024,
            doi="10.26538/tjnpr/v9i12.44",
            eid="2-s2.0-105028738314",
        )
    }
    hybrid_res = HybridRetrievalResult(
        intent_type="EXPERT_RANKING",
        topics=[],
        experts=mock_experts,
        publications=mock_pubs,
        sql_executed="TEMPLATE: SQL_GOLD_ANALYTICS (intent='EXPERT_RANKING')",
        target_topic_name="Phytochemicals & Molecular Docking",
        filters_ignored=[],
    )

    async def _mock_retrieve(*_args, **_kwargs):
        return hybrid_res

    monkeypatch.setattr(
        "backend.app.routers.ask.HybridRetriever.retrieve",
        _mock_retrieve,
    )

    async def _mock_gate(*_args, **_kwargs):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        _mock_gate,
    )

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(fake_conn))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa pakar utama pada topik Mesenchymal Stem Cell di Indonesia?",
                    "developer_mode": True,
                },
            )

    assert resp.status_code == 200
    data = resp.json()

    assert data["route"] == "HybridRoute"
    assert data["status"] == "ok"
    assert len(data["evidence_objects"]) >= 1
    assert data["evidence_objects"][0]["metric"] == "expertise_score"
    assert data["evidence_objects"][0]["value"] == 88.5
    assert len(data["sources"]) == 1
    assert data["sources"][0]["doi"] == "10.26538/tjnpr/v9i12.44"
    assert "Hardjo, Marhaen" in data["answer"]
    assert "[Ethanol Extract of Cosmos Caudatus Attenuates Oxidative Stress, 2024, 10.26538/tjnpr/v9i12.44]" in data["answer"]
    assert data["unverified_citations"] == []


@pytest.mark.asyncio
async def test_ask_endpoint_hybrid_route_zero_match_short_circuit(monkeypatch):
    """Verify zero-match HybridRoute query short-circuits to not_found without LLM call."""
    fake_conn = AsyncMock()

    empty_res = HybridRetrievalResult(
        intent_type="TOPIC_TRENDS",
        topics=[],
        experts=[],
        publications={},
        sql_executed="TEMPLATE: SQL_GOLD_ANALYTICS (intent='TOPIC_TRENDS')",
        filters_ignored=["country"],
    )

    async def _mock_retrieve(*_args, **_kwargs):
        return empty_res

    monkeypatch.setattr(
        "backend.app.routers.ask.HybridRetriever.retrieve",
        _mock_retrieve,
    )

    async def _mock_gate(*_args, **_kwargs):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        _mock_gate,
    )

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(fake_conn))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Bagaimana tren perkembangan terapi stem cell 5 tahun terakhir?",
                    "developer_mode": True,
                },
            )

    assert resp.status_code == 200
    data = resp.json()

    assert data["route"] == "HybridRoute"
    assert data["status"] == "not_found"
    assert "Data tidak ditemukan" in data["answer"]
    assert data["evidence_objects"] == []
    assert data["sources"] == []
    assert data["unverified_citations"] == []
    assert data["filters_ignored"] == ["country"]


@pytest.mark.asyncio
async def test_ask_endpoint_hybrid_route_entity_not_found(monkeypatch):
    """Verify author entity not found returns not_found early."""
    fake_conn = AsyncMock()

    async def _mock_gate(*_args, **_kwargs):
        return EntityResolutionResult(
            status="not_found",
            clarification_message="Tidak ditemukan peneliti yang cocok.",
        )

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        _mock_gate,
    )

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(fake_conn))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa pakar utama pada topik Mesenchymal Stem Cell di Indonesia?",
                    "filters": {"author_name": "Peneliti Antah Berantah"},
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_found"
    assert data["evidence_objects"] == []


@pytest.mark.asyncio
async def test_ask_endpoint_hybrid_route_entity_clarification(monkeypatch):
    """Verify ambiguous entity returns needs_clarification with candidates."""
    fake_conn = AsyncMock()

    cands = [
        CandidateItem(id="AUTH_1", name="Budi A", type="author", publication_count=3),
        CandidateItem(id="AUTH_2", name="Budi B", type="author", publication_count=2),
    ]

    async def _mock_gate(*_args, **_kwargs):
        return EntityResolutionResult(
            status="needs_clarification",
            candidates=cands,
            clarification_message="Ditemukan beberapa peneliti dengan nama tersebut.",
        )

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        _mock_gate,
    )

    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=_fake_pool(fake_conn))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa pakar utama pada topik AI?",
                    "filters": {"author_name": "Budi"},
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "needs_clarification"
    assert data["candidates"] is not None
    assert len(data["candidates"]) == 2
