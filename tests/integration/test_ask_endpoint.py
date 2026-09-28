"""Integration tests for POST /api/v1/ask endpoint.

Docs Reference: docs/06 Api Design.md §5, docs/11 Roadmap.md §4 (Fase 2).
"""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.main import app


@pytest.mark.asyncio
async def test_ask_endpoint_valid_request():
    """Verify POST /api/v1/ask succeeds with valid payload."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        custom_req_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Who are the top authors in 2023?"},
            headers={"X-Request-ID": custom_req_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_req_id
        assert "X-Response-Time-MS" in resp.headers

        data = resp.json()
        assert data["request_id"] == custom_req_id
        assert data["status"] in ("ok", "not_found")
        assert data["route"] in ("SQLRoute", "VectorRoute", "GraphRoute", "HybridRoute")
        assert isinstance(data["answer"], str)
        assert isinstance(data["evidence_objects"], list)
        assert isinstance(data["sources"], list)
        assert isinstance(data["unverified_citations"], list)


@pytest.mark.asyncio
async def test_ask_endpoint_developer_mode():
    """Verify developer_mode returns debug payload with latency breakdown."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ask",
            json={"question": "Top publications on stem cells", "developer_mode": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["debug"] is not None
        assert "latency_breakdown_ms" in data["debug"]


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
        assert "error" in data
        assert data["error"]["error_type"] == "validation_error"
        assert data["error"]["status_code"] == 422
        assert "request_id" in data


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
