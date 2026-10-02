"""Integration tests for GET /api/v1/health endpoint.

Docs Reference: docs/06 Api Design.md §6, docs/11 Roadmap.md §4 (Fase 2).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.main import app


@pytest.mark.asyncio
async def test_health_endpoint_success():
    """Verify /api/v1/health returns HTTP 200 with full dependency readiness."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

        data = resp.json()
        assert data["version"] == "1.0.0"
        assert data["status"] in ("healthy", "degraded")

        # Database assertions
        db = data["database"]
        assert db["status"] == "connected"
        assert db["silver_tables_ready"] is True
        assert db["pgvector_ready"] is True
        assert db["public_tables_count"] >= 11
        # Invariant: No password or connection secret leaked
        assert "password" not in str(data).lower()
        assert "db_url" not in str(data).lower()
        assert "secret" not in str(data).lower()

        # LLM service assertions
        llm = data["llm_service"]
        assert "status" in llm
        assert llm["provider"] == "Ollama"
        assert llm["model"] == "qwen2.5-coder:7b-instruct"

        # Embedding service assertions
        embed = data["embedding_service"]
        assert embed["status"] == "ready"
        assert embed["model"] == "BAAI/bge-m3"
        assert embed["dimension"] == 1024

        # Phase 5 Evidence layer assertions
        assert data["evidence_layer_ready"] is True


def test_evidence_layer_health_probe_ready():
    """Phase 5 probe returns True when the canonical Evidence API is intact."""
    from backend.app.routers.health import check_evidence_layer_health

    assert check_evidence_layer_health() is True


def test_evidence_layer_health_probe_never_raises(monkeypatch):
    """Phase 5 probe returns False (never raises) when the Evidence API breaks."""
    import builtins

    import backend.app.routers.health as health_module

    real_import = builtins.__import__

    def _broken_import(name, *args, **kwargs):
        if name.startswith("backend.app.services.evidence"):
            raise ImportError("simulated evidence breakage")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_import)
    assert health_module.check_evidence_layer_health() is False


@pytest.mark.asyncio
async def test_root_endpoint():
    """Verify root / endpoint returns operational status."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "operational"
        assert data["version"] == "1.0.0"
