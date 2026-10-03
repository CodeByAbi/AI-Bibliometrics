"""Unit tests for synthesis observability: counters, health payload, /metrics.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §6.

The behaviour under test is the one that makes a silently broken LLM visible.
``refine()`` absorbs every failure into the deterministic renderer, so without
these counters a deployment whose LLM has never succeeded is indistinguishable
from a healthy one.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from backend.app.core.metrics import get_registry, render_metrics
from backend.app.core.middleware import RateLimitingMiddleware
from backend.app.routers.health import build_synthesis_health
from backend.app.services.synthesizer.llm import LlmSynthesisError
from backend.app.services.synthesizer.stats import (
    KNOWN_REASONS,
    REASON_CITATION_STRIPPED,
    REASON_EMPTY,
    REASON_HTTP,
    REASON_TIMEOUT,
    REASON_TRANSPORT,
    REASON_UNKNOWN,
    REASON_UNREACHABLE,
    SynthesisStats,
    get_synthesis_stats,
)

# ---------------------------------------------------------------------------
# SynthesisStats: counter semantics
# ---------------------------------------------------------------------------


def test_fresh_stats_report_no_calls_and_zero_rate():
    """A never-exercised pipeline must not report a misleading non-zero rate."""
    snap = SynthesisStats().snapshot()
    assert snap.total_calls == 0
    assert snap.fallback_rate == 0.0
    assert snap.degraded is False


def test_record_llm_increments_success_only():
    stats = SynthesisStats()
    stats.record_llm(123.5)
    stats.record_llm(76.5)
    snap = stats.snapshot()
    assert snap.llm_calls == 2
    assert snap.fallback_calls == 0
    assert snap.fallback_rate == 0.0
    assert snap.last_llm_ms == 76.5  # most recent, not accumulated
    assert snap.degraded is False


def test_fallback_rate_is_share_of_all_attempts():
    """The headline number: 3 of 4 attempts degraded -> 0.75, not 1.0."""
    stats = SynthesisStats()
    for _ in range(3):
        stats.record_fallback(REASON_TIMEOUT)
    stats.record_llm(10.0)
    snap = stats.snapshot()
    assert snap.total_calls == 4
    assert snap.fallback_calls == 3
    assert snap.fallback_rate == 0.75
    assert snap.degraded is True


def test_total_fallback_yields_rate_of_one():
    """The CPU-host scenario: LLM never succeeds -> rate 1.0, not silent."""
    stats = SynthesisStats()
    for _ in range(5):
        stats.record_fallback(REASON_TIMEOUT)
    assert stats.snapshot().fallback_rate == 1.0


def test_unrecognised_reason_is_bucketed_as_unknown():
    """A new failure mode must still be counted, not silently dropped."""
    stats = SynthesisStats()
    stats.record_fallback("some_future_reason")
    snap = stats.snapshot()
    assert dict(snap.fallback_by_reason) == {REASON_UNKNOWN: 1}
    assert snap.last_fallback_reason == REASON_UNKNOWN


def test_fallback_by_reason_is_sorted_and_immutable():
    stats = SynthesisStats()
    stats.record_fallback(REASON_HTTP)
    stats.record_fallback(REASON_TIMEOUT)
    stats.record_fallback(REASON_UNREACHABLE)
    snap = stats.snapshot()
    reasons = [reason for reason, _ in snap.fallback_by_reason]
    assert reasons == sorted(reasons)
    # Tuple of pairs keeps the "frozen" snapshot genuinely immutable.
    assert isinstance(snap.fallback_by_reason, tuple)


def test_reset_zeroes_everything():
    stats = SynthesisStats()
    stats.record_llm(5.0)
    stats.record_fallback(REASON_EMPTY)
    stats.reset()
    snap = stats.snapshot()
    assert snap.llm_calls == 0
    assert snap.fallback_calls == 0
    assert snap.last_llm_ms is None
    assert snap.last_fallback_reason is None


def test_singleton_is_shared():
    assert get_synthesis_stats() is get_synthesis_stats()


# ---------------------------------------------------------------------------
# LlmSynthesisError carries a machine-readable reason
# ---------------------------------------------------------------------------


def test_llm_error_reason_defaults_to_unknown():
    """A bare raise (as in older call sites and tests) must not crash counting."""
    assert LlmSynthesisError("boom").reason == REASON_UNKNOWN


def test_llm_error_carries_explicit_reason():
    assert LlmSynthesisError("boom", REASON_TIMEOUT).reason == REASON_TIMEOUT


def test_every_declared_reason_is_known():
    assert REASON_UNKNOWN in KNOWN_REASONS
    assert REASON_CITATION_STRIPPED in KNOWN_REASONS
    for reason in (
        REASON_TIMEOUT,
        REASON_UNREACHABLE,
        REASON_TRANSPORT,
        REASON_HTTP,
        REASON_EMPTY,
        REASON_CITATION_STRIPPED,
    ):
        assert reason in KNOWN_REASONS


# ---------------------------------------------------------------------------
# Health payload
# ---------------------------------------------------------------------------


def test_build_synthesis_health_reflects_counters():
    get_synthesis_stats().record_fallback(REASON_UNREACHABLE)
    get_synthesis_stats().record_llm(42.0)
    health = build_synthesis_health()
    assert health.llm_calls == 1
    assert health.fallback_calls == 1
    assert health.fallback_rate == 0.5
    assert health.fallback_by_reason == {REASON_UNREACHABLE: 1}
    assert health.last_llm_ms == 42.0
    assert health.last_fallback_reason == REASON_UNREACHABLE
    assert health.degraded is True
    assert health.scope == "process"


def test_build_synthesis_health_never_raises():
    """A counter read must never be able to fail the health endpoint."""
    assert build_synthesis_health().fallback_rate == 0.0


def test_health_response_exposes_synthesis_block():
    from backend.app.models.health import (
        DatabaseHealth,
        EmbeddingServiceHealth,
        HealthResponse,
        LLMServiceHealth,
        SynthesisHealth,
    )

    resp = HealthResponse(
        status="healthy",
        database=DatabaseHealth(
            status="connected",
            role="app_readonly",
            silver_tables_ready=True,
            gold_tables_ready=True,
            pgvector_ready=True,
        ),
        llm_service=LLMServiceHealth(
            status="connected", model="qwen2.5-coder:7b-instruct"
        ),
        embedding_service=EmbeddingServiceHealth(
            status="ready", model="BAAI/bge-m3"
        ),
    )
    assert isinstance(resp.synthesis, SynthesisHealth)
    assert resp.synthesis.fallback_rate == 0.0
    # Must stay serialisable for the API contract.
    assert "synthesis" in resp.model_dump()


# ---------------------------------------------------------------------------
# /metrics exposition
# ---------------------------------------------------------------------------


def test_metrics_exposes_synthesis_series():
    get_synthesis_stats().record_fallback(REASON_TIMEOUT)
    get_synthesis_stats().record_fallback(REASON_TIMEOUT)
    get_synthesis_stats().record_llm(11.0)
    body = render_metrics().decode("utf-8")
    assert "aibiblio_synthesis_llm_total" in body
    assert "aibiblio_synthesis_fallback_total" in body
    assert "aibiblio_synthesis_fallback_ratio" in body
    assert 'reason="timeout"' in body
    assert 'reason="unreachable"' in body  # zero-filled, series pre-created
    assert "aibiblio_synthesis_last_llm_ms" in body


def test_metrics_renders_before_any_call_has_happened():
    """Series must exist at zero so dashboards are not blank pre-traffic."""
    body = render_metrics().decode("utf-8")
    assert "aibiblio_synthesis_llm_total" in body


def test_metrics_registry_is_singleton_and_idempotent():
    """Re-registering would raise Duplicated timeseries and break startup."""
    assert get_registry() is get_registry()


# ---------------------------------------------------------------------------
# /metrics endpoint wiring on the real app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_served():
    from backend.app.main import create_app

    get_synthesis_stats().record_fallback(REASON_TRANSPORT)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "aibiblio_synthesis_fallback_total" in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint_exempt_from_rate_limiting():
    """A scraper polling /metrics must never be able to lock itself out."""
    app = FastAPI()
    app.add_middleware(RateLimitingMiddleware, requests_per_minute=1)

    @app.get("/metrics")
    async def metrics():
        return {"ok": True}

    @app.get("/api/v1/ask")
    async def ask():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(5):
            resp = await client.get("/metrics")
            assert resp.status_code == 200
        # The limited route still enforces its budget.
        assert (await client.get("/api/v1/ask")).status_code == 200
        assert (await client.get("/api/v1/ask")).status_code == 429
