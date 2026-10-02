"""Integration tests for GraphRoute on POST /api/v1/ask endpoint.

Docs Reference: docs/05 Retrieval Rag Design.md §5.3; docs/06 Api Design.md §5;
docs/10 Implementation Plan.md (Task 8-retriever, Task 10); docs/11 Roadmap.md (Fase 6).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.models.ask import CandidateItem
from backend.app.services.retrievers.graph_retriever import (
    GraphEdgeResult,
    GraphPublicationMeta,
    GraphRetrievalResult,
)
from backend.app.services.router import EntityResolutionResult


class _FakeConn:
    """Async context manager wrapper returned by pool.acquire()."""

    def __init__(self, conn: AsyncMock):
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


def _fake_pool(conn: AsyncMock) -> MagicMock:
    """Create a mock pool whose acquire() synchronously returns an async context manager."""
    pool = MagicMock()
    pool.acquire.side_effect = lambda: _FakeConn(conn)
    return pool


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_institution_collaboration_ok(monkeypatch):
    """Verify GraphRoute handles institution collaboration queries with rich provenance."""
    fake_conn = AsyncMock()

    mock_edges = [
        GraphEdgeResult(
            partner_id="INST_UI",
            partner_name="Universitas Indonesia",
            publication_count=12,
            via_publication_ids=["PUB001", "PUB002"],
        )
    ]
    mock_pubs = {
        "PUB001": GraphPublicationMeta(
            publication_id="PUB001",
            title="Joint AI Study",
            year=2024,
            doi="10.1016/j.ai.2024",
            eid="2-s2.0-101",
        ),
        "PUB002": GraphPublicationMeta(
            publication_id="PUB002",
            title="Biotech Research",
            year=2023,
            doi=None,
            eid="2-s2.0-102",
        ),
    }
    graph_res = GraphRetrievalResult(
        template_type="T1",
        edges=mock_edges,
        publications=mock_pubs,
        sql_executed="SQL_TEMPLATE_T1 (inst_id='INST_ITB', limit=20)",
        target_entity_name="Institut Teknologi Bandung",
        target_entity_id="INST_ITB",
        filters_ignored=[],
        execution_time_ms=15.0,
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.GraphRetriever.retrieve",
        AsyncMock(return_value=graph_res),
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        AsyncMock(
            return_value=EntityResolutionResult(
                status="ok",
                resolved_institution_id="INST_ITB",
                resolved_institution_name="Institut Teknologi Bandung",
            )
        ),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Siapa saja yang berkolaborasi dengan ITB?",
                    "developer_mode": True,
                },
            )

    assert resp.status_code == 200
    data = resp.json()

    assert data["route"] == "GraphRoute"
    assert data["status"] == "ok"
    assert len(data["evidence_objects"]) == 1
    ev = data["evidence_objects"][0]
    assert ev["metric"] == "publication_count"
    assert ev["value"] == 12
    assert "Universitas Indonesia" in ev["claim"]
    assert len(ev["sources"]) == 2

    assert len(data["sources"]) == 2
    assert data["sources"][0]["source_type"] == "graph"
    assert data["sources"][0]["publication_id"] in ("PUB001", "PUB002")

    assert "Universitas Indonesia" in data["answer"]
    assert "12 publikasi bersama" in data["answer"]
    assert len(data["unverified_citations"]) == 0

    assert data["debug"] is not None
    assert "SQL_TEMPLATE_T1" in (data["debug"]["sql_executed"] or "")
    lat = data["debug"]["latency_breakdown_ms"]
    assert "graph_retrieval_ms" in lat
    assert "evidence_unify_ms" in lat
    assert "synthesis_ms" in lat
    assert "total_ms" in lat


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_author_coauthorship_ok(monkeypatch):
    """Verify GraphRoute handles author co-authorship queries (T2)."""
    fake_conn = AsyncMock()

    mock_edges = [
        GraphEdgeResult(
            partner_id="AUTH_TONY",
            partner_name="Tony Liwang",
            publication_count=5,
            via_publication_ids=["PUB005"],
        )
    ]
    mock_pubs = {
        "PUB005": GraphPublicationMeta(
            publication_id="PUB005",
            title="Stem Cell Therapy Advances",
            year=2024,
            doi="10.1000/stem.2024",
        )
    }
    graph_res = GraphRetrievalResult(
        template_type="T2",
        edges=mock_edges,
        publications=mock_pubs,
        sql_executed="SQL_TEMPLATE_T2",
        target_entity_name="Septi Gumiandari",
        target_entity_id="AUTH_SEPTI",
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.GraphRetriever.retrieve",
        AsyncMock(return_value=graph_res),
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        AsyncMock(
            return_value=EntityResolutionResult(
                status="ok",
                resolved_author_id="AUTH_SEPTI",
                resolved_author_name="Septi Gumiandari",
            )
        ),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Siapa co-author dari Septi Gumiandari?"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "GraphRoute"
    assert data["status"] == "ok"
    assert "Tony Liwang" in data["answer"]
    assert "[Stem Cell Therapy Advances, 2024, 10.1000/stem.2024]" in data["answer"]


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_topic_composition_ok(monkeypatch):
    """Verify GraphRoute handles topic/keyword institution composition (T3)."""
    fake_conn = AsyncMock()

    mock_edges = [
        GraphEdgeResult(
            partner_id="INST_UI",
            partner_name="Universitas Indonesia",
            publication_count=15,
            via_publication_ids=["PUB010"],
            extra_metadata={"topic_keyword": "Artificial Intelligence"},
        )
    ]
    mock_pubs = {
        "PUB010": GraphPublicationMeta(
            publication_id="PUB010",
            title="AI In Medicine",
            year=2025,
            doi="10.1000/aimed.2025",
        )
    }
    graph_res = GraphRetrievalResult(
        template_type="T3",
        edges=mock_edges,
        publications=mock_pubs,
        sql_executed="SQL_TEMPLATE_T3",
        target_entity_name="Artificial Intelligence",
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.GraphRetriever.retrieve",
        AsyncMock(return_value=graph_res),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Institusi mana yang berkolaborasi dalam riset Artificial Intelligence?"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "GraphRoute"
    assert data["status"] == "ok"
    assert "Universitas Indonesia" in data["answer"]


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_path_search_ok(monkeypatch):
    """Verify GraphRoute handles multi-hop path searches (T4)."""
    fake_conn = AsyncMock()

    mock_edges = [
        GraphEdgeResult(
            partner_id="INST_UGM",
            partner_name="Universitas Gadjah Mada",
            publication_count=6,
            via_publication_ids=["PUB020"],
            path_nodes=["INST_ITB", "INST_UI", "INST_UGM"],
            hop_count=2,
            extra_metadata={"path": ["INST_ITB", "INST_UI", "INST_UGM"], "hops": 2},
        )
    ]
    mock_pubs = {
        "PUB020": GraphPublicationMeta(
            publication_id="PUB020",
            title="Collaborative Network Analysis",
            year=2024,
            doi="10.1000/cna.2024",
        )
    }
    graph_res = GraphRetrievalResult(
        template_type="T4",
        edges=mock_edges,
        publications=mock_pubs,
        sql_executed="SQL_TEMPLATE_T4",
        target_entity_name="Institut Teknologi Bandung",
        target_entity_id="INST_ITB",
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.GraphRetriever.retrieve",
        AsyncMock(return_value=graph_res),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Bagaimana jalur kolaborasi antara ITB dan UI?"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "GraphRoute"
    assert data["status"] == "ok"
    assert "Universitas Gadjah Mada" in data["answer"]
    assert "2 hop" in data["answer"]


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_zero_evidence_short_circuits_not_found(monkeypatch):
    """Zero graph edges short-circuit to status: not_found deterministically (<200ms)."""
    fake_conn = AsyncMock()

    empty_graph_res = GraphRetrievalResult(
        template_type="T1",
        edges=[],
        publications={},
        sql_executed="SQL_TEMPLATE_T1",
        target_entity_name="Universitas Tanpa Kolaborasi",
    )

    monkeypatch.setattr(
        "backend.app.routers.ask.GraphRetriever.retrieve",
        AsyncMock(return_value=empty_graph_res),
    )
    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        AsyncMock(
            return_value=EntityResolutionResult(
                status="ok",
                resolved_institution_id="INST_EMPTY",
                resolved_institution_name="Universitas Tanpa Kolaborasi",
            )
        ),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Siapa mitra kolaborasi Universitas Tanpa Kolaborasi?"},
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "GraphRoute"
    assert data["status"] == "not_found"
    assert "Data tidak ditemukan" in data["answer"]
    assert data["evidence_objects"] == []
    assert data["sources"] == []


@pytest.mark.asyncio
async def test_ask_endpoint_graph_route_ambiguous_entity_needs_clarification(monkeypatch):
    """Ambiguous entity resolution surfaces needs_clarification before graph retrieval."""
    fake_conn = AsyncMock()

    candidates = [
        CandidateItem(id="INST_01", name="Universitas A", type="institution", publication_count=10),
        CandidateItem(id="INST_02", name="Universitas B", type="institution", publication_count=5),
    ]

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities",
        AsyncMock(
            return_value=EntityResolutionResult(
                status="needs_clarification",
                candidates=candidates,
                clarification_message="Ditemukan 2 institusi.",
            )
        ),
    )

    with patch(
        "backend.app.routers.ask.get_pool",
        new=AsyncMock(return_value=_fake_pool(fake_conn)),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/ask",
                json={"question": "Siapa mitra kolaborasi institusi Universitas?"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "GraphRoute"
    assert data["status"] == "needs_clarification"
    assert len(data["candidates"]) == 2
