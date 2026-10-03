"""Fase 7 close-out tests for documented MEDIUM gaps.

Documents intentional behaviors as executable contracts so Fase 8
evaluation cannot misread them as bugs:
1. Hybrid endpoint maps retriever statement timeouts to a 503
   ``db_timeout`` envelope carrying ``request_id``.
2. Pure TOPIC_TRENDS answers carry metric evidence with zero publication
   sources by design (D1-c) — trends are Gold-aggregate claims.
3. Multi-intent queries resolve by documented priority Graph > Hybrid >
   SQL > Vector (Graph wins over aggregate wording).
4. Hybrid surfaces honestly-ignored structured filters.

Docs Reference: docs/05 Retrieval Rag Design.md §3-§5;
    docs/11 Roadmap.md §Fase 7 (close-out G).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.errors import DBTimeoutError
from backend.app.main import app
from backend.app.services.retrievers.hybrid_retriever import (
    HybridRetrievalResult,
    HybridTopicEvolutionItem,
)
from backend.app.services.router import EntityResolutionResult, QuestionRouter


class _FakeConn:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        pass


def _trends_only_result() -> HybridRetrievalResult:
    return HybridRetrievalResult(
        intent_type="TOPIC_TRENDS",
        topics=[
            HybridTopicEvolutionItem(
                topic_id=2,
                topic_name="Mesenchymal Stem Cells & Inflammation",
                year=2024,
                publication_count=12,
                citation_count=45,
                growth_score=0.40,
                citation_acceleration=0.15,
                recency_weight=1.0,
                is_emerging=True,
            )
        ],
        experts=[],
        publications={},
        sql_executed="TEMPLATE: SQL_GOLD_ANALYTICS (intent='TOPIC_TRENDS')",
        target_topic_name="Mesenchymal Stem Cells & Inflammation",
        filters_ignored=["country"],
    )


def test_router_multi_intent_graph_wins_over_sql_counting():
    """'Berapa jumlah kolaborasi ...' routes GraphRoute by documented priority."""
    decision = QuestionRouter.classify_route("Berapa jumlah kolaborasi institusi ITB?")
    assert decision.route == "GraphRoute"
    assert decision.answered_via_fallback is False


def test_router_topic_filter_without_trend_words_falls_back_hybrid():
    """topic_name-only queries fall back to HybridRoute (documented fallback)."""
    from backend.app.models.ask import FilterParams

    decision = QuestionRouter.classify_route(
        "Publikasi tentang topik yang tidak dikenal?",
        filters=FilterParams(topic_name="xyzzynonexistent"),
    )
    assert decision.route == "HybridRoute"
    assert decision.answered_via_fallback is True


@pytest.mark.asyncio
async def test_hybrid_endpoint_db_timeout_returns_503_envelope(monkeypatch):
    """Retriever statement timeout → 503 db_timeout envelope with request_id."""

    async def _mock_retrieve(*_a, **_k):
        raise DBTimeoutError("HybridRetriever statement timed out (10s)")

    monkeypatch.setattr("backend.app.routers.ask.HybridRetriever.retrieve", _mock_retrieve)

    async def _mock_gate(*_a, **_k):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities", _mock_gate
    )

    fake_pool = MagicMock()
    fake_pool.acquire.side_effect = lambda: _FakeConn(AsyncMock())
    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=fake_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Bagaimana tren topik stem cell?",
                    "filters": {"topic_name": "Stem Cell"},
                },
            )
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"]["error_type"] == "db_timeout"
    assert data["request_id"]
    # Sanitized envelope: no SQL, no traceback, no DSN.
    assert "SELECT" not in resp.text
    assert "Traceback" not in resp.text


@pytest.mark.asyncio
async def test_hybrid_trends_pure_carries_no_publication_sources(monkeypatch):
    """Pure trend answers are metric-grounded with empty sources (intentional D1-c)."""
    hybrid_res = _trends_only_result()

    async def _mock_retrieve(*_a, **_k):
        return hybrid_res

    monkeypatch.setattr("backend.app.routers.ask.HybridRetriever.retrieve", _mock_retrieve)

    async def _mock_gate(*_a, **_k):
        return EntityResolutionResult(status="ok")

    monkeypatch.setattr(
        "backend.app.routers.ask.EntityResolutionGate.resolve_entities", _mock_gate
    )

    fake_pool = MagicMock()
    fake_pool.acquire.side_effect = lambda: _FakeConn(AsyncMock())
    with patch("backend.app.routers.ask.get_pool", new=AsyncMock(return_value=fake_pool)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/ask",
                json={
                    "question": "Bagaimana tren topik stem cell?",
                    "filters": {"topic_name": "Stem Cell", "country": "indonesia"},
                    "developer_mode": True,
                },
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["route"] == "HybridRoute"
    assert len(data["evidence_objects"]) >= 1
    assert data["sources"] == []
    assert data["unverified_citations"] == []
    # Metric values stay exact (no LLM distortion surface).
    assert data["evidence_objects"][0]["value"] == 0.40
    # Honestly-ignored structured filters are surfaced.
    assert "country" in data["filters_ignored"]
    assert data["debug"]["synthesis_backend"] == "deterministic"
