"""Integration tests for VectorRoute execution in POST /api/v1/ask (Phase 4).

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/06 Api Design.md §5, docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app


@pytest.mark.asyncio
async def test_ask_endpoint_vector_route_successful_match():
    """Verify semantic query matching a prototype document returns status=ok with EvidenceObjects."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Indo-Wdsimplequad2.0 Indonesian Benchmark Dataset for Knowledge Graph Question Answering"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] == "VectorRoute"
        assert data["status"] == "ok"
        assert len(data["evidence_objects"]) >= 1
        assert len(data["sources"]) >= 1

        ev = data["evidence_objects"][0]
        assert ev["metric"] == "similarity_score"
        assert ev["value"] >= 0.65
        assert ev["confidence"] >= 0.65
        assert len(ev["sources"]) == 1

        src = data["sources"][0]
        assert src["source_type"] == "vector"
        assert src["relevance_score"] >= 0.65


@pytest.mark.asyncio
async def test_ask_endpoint_vector_route_unrelated_query_not_found():
    """Verify completely unrelated query (< 0.65) returns status=not_found deterministically."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "how to bake sourdough bread and make pizza crust at home"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] == "VectorRoute"
        assert data["status"] == "not_found"
        assert data["evidence_objects"] == []
        assert data["sources"] == []
        assert "Data tidak ditemukan" in data["answer"]


@pytest.mark.asyncio
async def test_ask_endpoint_vector_route_developer_mode_diagnostics():
    """Verify developer_mode on VectorRoute returns executed SQL and latency breakdown."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Indo-Wdsimplequad2.0 Indonesian Benchmark Dataset for Knowledge Graph Question Answering",
                "developer_mode": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["debug"] is not None
        assert "scored_chunks" in data["debug"]["sql_executed"]
        assert "vector_retrieval_ms" in data["debug"]["latency_breakdown_ms"]
        assert "synthesis_ms" in data["debug"]["latency_breakdown_ms"]
        assert "total_ms" in data["debug"]["latency_breakdown_ms"]
        scored = data["debug"]["scored_chunks"]
        assert isinstance(scored, list) and len(scored) >= 1
        first = scored[0]
        assert set(first) == {"publication_id", "title", "year", "doi", "chunk_id", "similarity_score"}
        assert first["similarity_score"] >= 0.65
        assert len(scored) == len(data["sources"])


@pytest.mark.asyncio
async def test_ask_endpoint_vector_route_with_filters():
    """Verify year and document_type filters are applied to VectorRoute query."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Indo-Wdsimplequad2.0 Indonesian Benchmark Dataset for Knowledge Graph Question Answering",
                "filters": {"year": 2024, "document_type": "article"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] == "VectorRoute"
