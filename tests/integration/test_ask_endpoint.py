"""Integration tests for POST /api/v1/ask endpoint across Phase 3 vertical slice.

Docs Reference: docs/06 Api Design.md §5, docs/11 Roadmap.md §4 (Fase 3).
"""

from __future__ import annotations

import time
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.db.pool import get_pool
from backend.app.main import app


@pytest.mark.asyncio
async def test_ask_endpoint_sql_route_total_publications():
    """Verify POST /api/v1/ask executes SQL query and returns grounded EvidenceObject."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        custom_req_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Berapa total publikasi pada tahun 2025?"},
            headers={"X-Request-ID": custom_req_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_req_id

        data = resp.json()
        assert data["request_id"] == custom_req_id
        assert data["status"] == "ok"
        assert data["route"] == "SQLRoute"
        assert "20" in data["answer"]
        assert len(data["evidence_objects"]) >= 1
        ev = data["evidence_objects"][0]
        assert ev["metric"] == "publication_count"
        assert ev["value"] == 20
        assert ev["confidence"] == 1.0


@pytest.mark.asyncio
async def test_ask_endpoint_sql_route_top_authors():
    """Verify top authors ranking query produces grounded list and evidence objects."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Siapa 5 penulis paling produktif tahun 2025?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["route"] == "SQLRoute"
        assert len(data["evidence_objects"]) > 0
        for ev in data["evidence_objects"]:
            assert ev["metric"] == "publication_count"
            assert ev["confidence"] == 1.0


@pytest.mark.asyncio
async def test_ask_endpoint_zero_match_short_circuit():
    """Verify zero-match query returns status: not_found deterministically."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Daftar publikasi pada tahun 1950"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_found"
        assert "tidak ditemukan" in data["answer"].lower()
        assert data["evidence_objects"] == []
        assert data["sources"] == []


@pytest.mark.asyncio
async def test_ask_endpoint_ambiguous_entity_needs_clarification():
    """Verify ambiguous entity returns status: needs_clarification with candidates."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Berapa publikasi dari institusi Universitas?",
                "filters": {"institution_name": "Universitas"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "needs_clarification"
        assert data["candidates"] is not None
        assert len(data["candidates"]) > 1
        for cand in data["candidates"]:
            assert "id" in cand
            assert "name" in cand
            assert cand["type"] in ("author", "institution", "topic")


@pytest.mark.asyncio
async def test_ask_endpoint_developer_mode_diagnostics():
    """Verify developer_mode returns executed SQL and full latency breakdown."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Tampilkan top 5 publikasi dengan sitasi terbanyak", "developer_mode": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["debug"] is not None
        assert data["debug"]["sql_executed"] is not None
        assert "SELECT" in data["debug"]["sql_executed"]
        breakdown = data["debug"]["latency_breakdown_ms"]
        assert "routing_ms" in breakdown
        assert "entity_resolution_ms" in breakdown
        assert "sql_retrieval_ms" in breakdown
        assert "synthesis_ms" in breakdown
        assert "total_ms" in breakdown


@pytest.mark.asyncio
async def test_ask_endpoint_validation_short_question():
    """Verify question with length < 3 returns HTTP 422 with structured ErrorResponse."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "a"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["error_type"] == "validation_error"
        assert data["error"]["status_code"] == 422
        assert "request_id" in data


@pytest.mark.asyncio
async def test_ask_endpoint_destructive_input_neutralized():
    """DROP-table input never reaches the database; tables stay intact (AC Fase 3)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "DROP TABLE publications"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("not_found", "needs_clarification", "error")
        assert data["evidence_objects"] == []

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM publications;")
        assert total == 20


@pytest.mark.asyncio
async def test_ask_endpoint_injection_filter_safe_envelope():
    """Tautology payload in filters yields a safe envelope, never a 500 or leak."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Berapa total publikasi pada tahun 2025?",
                "filters": {"author_name": "' OR '1'='1"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("not_found", "needs_clarification")
        assert data["evidence_objects"] == []
        assert "error" not in data


@pytest.mark.asyncio
async def test_ask_endpoint_top_author_matches_direct_db():
    """Endpoint ranking values match a direct COUNT(DISTINCT) query."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        top = await conn.fetchrow(
            "SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS publication_count "
            "FROM authors a JOIN pub_author pa ON pa.author_id = a.author_id "
            "JOIN publications p ON p.publication_id = pa.publication_id "
            "WHERE p.year = 2025 GROUP BY a.author_id, a.author_name_normalized "
            "ORDER BY publication_count DESC, a.author_name ASC LIMIT 1;"
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Siapa 5 penulis paling produktif tahun 2025?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert top["author_name"] in data["answer"]
        assert data["evidence_objects"][0]["value"] == top["publication_count"]


@pytest.mark.asyncio
async def test_ask_endpoint_unknown_entity_not_found():
    """Fictitious filtered author short-circuits to not_found without SQL."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Berapa total publikasi pada tahun 2025?",
                "filters": {"author_name": "Xyzzq Qwerty Tidakada"},
                "developer_mode": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_found"
        assert data["evidence_objects"] == []
        assert data["sources"] == []
        assert data["debug"]["sql_executed"] is None


@pytest.mark.asyncio
async def test_ask_endpoint_non_sql_route_not_found():
    """Phase 3 serves SQL only: semantic queries answer honest not_found."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Paper yang membahas mekanisme inhibisi xanthine oxidase"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] == "VectorRoute"
        assert data["status"] == "not_found"
        assert data["evidence_objects"] == []


@pytest.mark.asyncio
async def test_ask_endpoint_filters_ignored_surfaced():
    """Unconsumed structured filters are reported, not silently dropped."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Berapa total publikasi pada tahun 2025?",
                "filters": {"year": 2025, "topic_name": "Stem Cell"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "topic_name" in data["filters_ignored"]


@pytest.mark.asyncio
async def test_ask_endpoint_zero_match_latency_guard():
    """Zero-match slice must stay far below the 8s LLM timeout (no synthesis call)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        start = time.perf_counter()
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Daftar publikasi pada tahun 1950"},
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"
        assert elapsed_ms < 5000


@pytest.mark.asyncio
async def test_ask_endpoint_validation_whitespace_question():
    """Verify question with only whitespace returns HTTP 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "      "},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["error_type"] == "validation_error"


@pytest.mark.asyncio
async def test_ask_endpoint_validation_invalid_year_range():
    """Verify invalid filter (year_to < year_from) returns HTTP 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Research output in biology",
                "filters": {"year_from": 2024, "year_to": 2020},
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["error_type"] == "validation_error"


@pytest.mark.asyncio
async def test_ask_endpoint_sql_llm_failure_maps_to_422(monkeypatch):
    """P0-3/P1-2: SQLRoute forcing the LLM path surfaces 422, never ok-hallucination."""
    from backend.app.services.retrievers.sql_retriever import SqlRetriever
    from backend.app.services.retrievers.sql_security import SqlSecurityError

    async def boom(cls, question, filters=None, validation_error=None):
        raise SqlSecurityError("LLM Text-to-SQL unavailable")

    monkeypatch.setattr(SqlRetriever, "generate_llm_sql", classmethod(boom))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # "rata-rata" hits SQLRoute intent but no deterministic template → LLM path.
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Berapa rata-rata sitasi per tahun?"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["error_type"] == "sql_generation_failed"


@pytest.mark.asyncio
async def test_ask_endpoint_keyword_filter_surfaced_not_dropped():
    """P1-4: keyword filter is reported in filters_ignored, not silently dropped."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={
                "question": "Berapa total publikasi pada tahun 2025?",
                "filters": {"year": 2025, "keyword": "stem cell"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "keyword" in data["filters_ignored"]
