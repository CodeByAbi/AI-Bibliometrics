"""HTTP Middleware for request tracing and rate limiting.

Docs Reference: docs/06 Api Design.md §4, docs/08 Security.md §3-§4.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, List
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.app.core.config import get_settings
from backend.app.core.logging import current_request_id, logger
from backend.app.models.errors import ErrorDetail, ErrorResponse


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """Middleware for UUIDv4 request_id generation, context propagation, and latency timing."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 1. Extract or generate UUIDv4 request_id (sanitized to prevent log/header injection)
        req_id = request.headers.get("X-Request-ID")
        if not req_id or len(req_id.strip()) == 0:
            req_id = str(uuid.uuid4())
        else:
            # Strip CR/LF to block header/log injection, truncate to 128 chars
            req_id = req_id.strip().replace("\r", "").replace("\n", "")[:128]
            if not req_id:
                req_id = str(uuid.uuid4())

        # Set in state and contextvar
        request.state.request_id = req_id
        token = current_request_id.set(req_id)

        start_time = time.perf_counter()
        endpoint = request.url.path
        method = request.method

        logger.info(
            "Inbound request: %s %s",
            method,
            endpoint,
            extra={"request_id": req_id, "endpoint": endpoint, "status": "started"},
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "Request exception after %.2fms: %s",
                elapsed_ms,
                str(exc),
                exc_info=True,
                extra={"request_id": req_id, "endpoint": endpoint, "latency_ms": elapsed_ms, "status": 500},
            )
            current_request_id.reset(token)
            raise exc

        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-MS"] = str(elapsed_ms)

        logger.info(
            "Completed request: %s %s -> %d in %.2fms",
            method,
            endpoint,
            response.status_code,
            elapsed_ms,
            extra={
                "request_id": req_id,
                "endpoint": endpoint,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
            },
        )

        current_request_id.reset(token)
        return response


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding window IP rate limiter."""

    _instances: List["RateLimitingMiddleware"] = []

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.window_s = 60.0
        self.history: Dict[str, List[float]] = defaultdict(list)
        self._last_sweep = time.time()
        RateLimitingMiddleware._instances.append(self)

    @classmethod
    def reset_all(cls) -> None:
        """Reset rate limiter history across all instances (used in tests)."""
        for inst in cls._instances:
            inst.history.clear()
    def _sweep(self, now: float) -> None:
        """Evict stale IPs to bound memory on long-lived servers (1-min cadence)."""
        if now - self._last_sweep < 60.0:
            return
        self._last_sweep = now
        cutoff = now - self.window_s
        stale = [ip for ip, ts in self.history.items() if not ts or max(ts) <= cutoff]
        for ip in stale:
            del self.history[ip]

    def _clean_and_check(self, ip: str, now: float) -> bool:
        timestamps = self.history[ip]
        cutoff = now - self.window_s
        # Retain only timestamps within the current window
        valid_timestamps = [t for t in timestamps if t > cutoff]
        self.history[ip] = valid_timestamps
        if len(valid_timestamps) >= self.rpm:
            return False
        self.history[ip].append(now)
        return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Exclude internal health/docs/metrics checks from rate limiting — a
        # scraper polling /metrics must never be able to lock itself out.
        if request.url.path in (
            "/api/v1/health",
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/",
        ):
            return await call_next(request)

        # Keyed on the resolved peer address. Behind a reverse proxy this is only
        # correct because uvicorn runs with --proxy-headers, which overwrites
        # scope["client"] from X-Forwarded-For / X-Real-Ip for trusted proxies
        # (see TRUSTED_PROXY_IPS in docker-compose.yml). Without that flag every
        # request shares the proxy's IP and the entire site collapses into one
        # bucket — a site-wide 429. Do not read X-Forwarded-For directly here:
        # that would trust a client-supplied header and let anyone bypass the
        # limiter by inventing a fresh IP per request.
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        self._sweep(now)

        if not self._clean_and_check(client_ip, now):
            req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
            logger.warning(
                "Rate limit exceeded for IP %s on %s",
                client_ip,
                request.url.path,
                extra={"request_id": req_id, "error_code": "rate_limit_exceeded", "status": 429},
            )
            payload = ErrorResponse(
                request_id=req_id,
                error=ErrorDetail(
                    error_type="rate_limit_exceeded",
                    message=f"Batas laju permintaan terlampaui (maksimal {self.rpm} request/menit). Silakan coba kembali beberapa saat lagi.",
                    status_code=429,
                ),
            )
            return JSONResponse(
                status_code=429,
                content=payload.model_dump(),
                headers={"X-Request-ID": req_id},
            )

        return await call_next(request)
