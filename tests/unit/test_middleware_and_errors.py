"""Unit tests for middleware, error formatting, and structured logging.

Docs Reference: docs/06 Api Design.md §7, docs/08 Security.md §4.
"""

from __future__ import annotations

import json
import logging
import httpx
import pytest
from backend.app.core.errors import AppException, DBTimeoutError
from backend.app.core.logging import StructuredJSONFormatter
from backend.app.core.middleware import RateLimitingMiddleware
from backend.app.models.ask import FilterParams


def test_structured_json_formatter():
    """Verify log records are formatted as valid JSON with standard fields."""
    formatter = StructuredJSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.request_id = "test-uuid-1234"
    record.status = 200
    record.latency_ms = 42.5

    output = formatter.format(record)
    data = json.loads(output)
    assert data["message"] == "Test message"
    assert data["level"] == "INFO"
    assert data["request_id"] == "test-uuid-1234"
    assert data["status"] == 200
    assert data["latency_ms"] == 42.5


def test_filter_params_validation():
    """Verify FilterParams validation rules."""
    # Valid params
    fp = FilterParams(year=2023, country="indonesia")
    assert fp.year == 2023
    assert fp.country == "indonesia"

    # Invalid year range
    with pytest.raises(ValueError, match="year_to must be greater than or equal to year_from"):
        FilterParams(year_from=2025, year_to=2020)


def test_app_exceptions():
    """Verify custom AppException attributes."""
    exc = DBTimeoutError()
    assert exc.error_type == "db_timeout"
    assert exc.status_code == 503
    assert "terlalu lama" in exc.message


# ---------------------------------------------------------------------------
# CORS allow-list (docs/08). Was hardcoded; now driven by CORS_ORIGINS.
# ---------------------------------------------------------------------------


def test_cors_origins_default_to_local_only():
    """Default must stay localhost-only so a deploy cannot silently trust all."""
    from backend.app.core.config import DEFAULT_CORS_ORIGINS, Settings

    assert Settings().cors_origins == DEFAULT_CORS_ORIGINS
    assert all(
        origin.startswith(("http://localhost", "http://127.0.0.1"))
        for origin in DEFAULT_CORS_ORIGINS
    )


def test_parse_csv_env_trims_dedupes_and_strips_quotes(monkeypatch):
    from backend.app.core.config import _parse_csv_env

    monkeypatch.setenv(
        "TEST_CSV", " https://a.vercel.app , https://b.id ,'https://a.vercel.app' ,, "
    )
    assert _parse_csv_env("TEST_CSV", ["fallback"]) == [
        "https://a.vercel.app",
        "https://b.id",
    ]


def test_parse_csv_env_falls_back_when_unset(monkeypatch):
    from backend.app.core.config import _parse_csv_env

    monkeypatch.delenv("TEST_CSV", raising=False)
    assert _parse_csv_env("TEST_CSV", ["http://localhost:3000"]) == [
        "http://localhost:3000"
    ]


def test_parse_csv_env_fails_fast_on_empty(monkeypatch):
    """Silently serving a localhost-only list would look healthy while blocking
    every real browser request, so an unusable value must fail at startup."""
    from backend.app.core.config import _parse_csv_env

    monkeypatch.setenv("TEST_CSV", " , , ")
    with pytest.raises(ValueError, match="no usable entries"):
        _parse_csv_env("TEST_CSV", ["http://localhost:3000"])


def _cors_app(origins):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_cors_allows_configured_origin():
    app = _cors_app(["https://app.vercel.app"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/ping", headers={"Origin": "https://app.vercel.app"}
        )
    assert resp.headers.get("access-control-allow-origin") == "https://app.vercel.app"


@pytest.mark.asyncio
async def test_cors_blocks_unlisted_origin():
    """The regression this guards: a Vercel deploy must not be silently blocked."""
    app = _cors_app(["http://localhost:3000"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/ping", headers={"Origin": "https://app.vercel.app"}
        )
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_create_app_honours_cors_origins_env(monkeypatch):
    """End-to-end wiring: CORS_ORIGINS must reach CORSMiddleware."""
    from backend.app.core.config import get_settings
    from backend.app.main import create_app

    monkeypatch.setenv("CORS_ORIGINS", "https://from-env.vercel.app")
    get_settings.cache_clear()
    try:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ok = await client.get(
                "/api/v1/ping", headers={"Origin": "https://from-env.vercel.app"}
            )
            blocked = await client.get(
                "/api/v1/ping", headers={"Origin": "https://other.vercel.app"}
            )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert (
        ok.headers.get("access-control-allow-origin")
        == "https://from-env.vercel.app"
    )
    assert "access-control-allow-origin" not in blocked.headers


# ---------------------------------------------------------------------------
# Rate limiter: per-IP isolation (docs/08 §3)
# ---------------------------------------------------------------------------


def _limited_app(rpm: int):
    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(RateLimitingMiddleware, requests_per_minute=rpm)

    @app.get("/api/v1/ask")
    async def ask():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_rate_limit_isolates_distinct_client_ips():
    """Regression: behind a proxy every request must not share one bucket.

    httpx's ASGITransport lets us pin the peer address, standing in for what
    uvicorn's --proxy-headers derives from X-Forwarded-For.
    """
    app = _limited_app(rpm=2)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("1.1.1.1", 9)),
        base_url="http://test",
    ) as first:
        assert (await first.get("/api/v1/ask")).status_code == 200
        assert (await first.get("/api/v1/ask")).status_code == 200
        assert (await first.get("/api/v1/ask")).status_code == 429

    # A different IP must still have its full budget.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("2.2.2.2", 9)),
        base_url="http://test",
    ) as second:
        assert (await second.get("/api/v1/ask")).status_code == 200
        assert (await second.get("/api/v1/ask")).status_code == 200
        assert (await second.get("/api/v1/ask")).status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_429_carries_request_id():
    app = _limited_app(rpm=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.get("/api/v1/ask")
        resp = await client.get("/api/v1/ask")
    assert resp.status_code == 429
    assert resp.headers.get("X-Request-ID")
    assert resp.json()["error"]["error_type"] == "rate_limit_exceeded"


def test_rate_limit_rpm_is_configurable_and_defaults_to_60():
    """docs/08 §3 budget raised 20 -> 60 so interactive use stops self-429ing."""
    from backend.app.core.config import Settings

    assert Settings().rate_limit_rpm == 60


@pytest.mark.asyncio
async def test_rate_limit_rpm_read_from_env(monkeypatch):
    from backend.app.core.config import get_settings
    from backend.app.main import create_app

    monkeypatch.setenv("RATE_LIMIT_RPM", "5")
    get_settings.cache_clear()
    try:
        app = create_app()
        middleware = next(
            m for m in app.user_middleware if m.cls is RateLimitingMiddleware
        )
        assert middleware.kwargs["requests_per_minute"] == 5
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
