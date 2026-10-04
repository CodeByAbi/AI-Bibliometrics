"""Retry with exponential backoff + jitter for transient outbound calls.

Docs Reference: docs/05 Retrieval Rag Design.md (Ollama budgets), docs/08 Security.md.

Contract:
- Retry ONLY retriable errors (connection errors, HTTP 5xx). A **timeout is not
  retried by default**: see ``retry_on_timeout`` below for why.
- Never retry 4xx client errors — they fail fast.
- Every attempt failure is logged server-side; callers receive the last
  error chained via ``raise ... from exc`` so context is never lost.
- ``telemetry`` reports what actually happened so an operator can see the
  retry count without reconstructing it from log ordering.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from backend.app.core.logging import logger

T = TypeVar("T")


def is_retriable_http_status(status_code: int) -> bool:
    """True for transient 5xx / 429; False for 4xx client errors."""
    return status_code == 429 or 500 <= status_code <= 599


def is_retriable_exception(
    exc: BaseException, *, retry_on_timeout: bool = False
) -> bool:
    """True for network/transient failures worth one more attempt.

    ``retry_on_timeout`` is deliberately **off** by default. A timeout is the
    one failure mode where a retry is guaranteed not to help on the
    synchronous request path: the same model on the same box needs the same
    amount of wall clock, so the second attempt burns an identical timeout
    before failing. That is what turned a single unsupported-SQL question into
    ~17 s (8 s timeout + backoff + 8 s timeout) on ``POST /api/v1/ask``.

    Connection errors are a different animal: they are frequently a transient
    keep-alive race against a pooled socket, and a retry genuinely recovers.
    """
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
        return retry_on_timeout
    return isinstance(exc, httpx.ConnectError)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.5,
    max_delay_s: float = 10.0,
    operation: str = "outbound-call",
    retry_on_timeout: bool = False,
    telemetry: dict[str, Any] | None = None,
) -> T:
    """Execute ``fn`` with exponential backoff + jitter.

    ``max_attempts`` is TOTAL attempts, not additional retries — a caller
    passing ``ollama_max_retries=1`` passes ``max_attempts=2``.

    ``retry_on_timeout`` (default False) makes ``httpx.TimeoutException`` and
    ``asyncio.TimeoutError`` terminal, so an expensive generation is attempted
    exactly once.

    ``telemetry``, when a dict is supplied, is populated in place on both the
    success and failure paths with:

    - ``attempts``    — outbound calls actually made
    - ``max_attempts``— the ceiling that was configured
    - ``retried``     — True iff more than one attempt was made
    - ``total_ms``    — wall clock across all attempts, backoff included
    - ``outcome``     — ``"ok"`` | ``"error"``

    Raises the last error (chained) when attempts are exhausted or the
    error is non-retriable.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    started = time.perf_counter()
    attempts = 0
    succeeded = False
    last_error: BaseException | None = None
    try:
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                result = await fn()
            except Exception as exc:
                last_error = exc
                # Non-retriable: fail fast, loudly, with context.
                if not is_retriable_exception(exc, retry_on_timeout=retry_on_timeout):
                    raise
                if attempt == max_attempts:
                    break
                jitter = random.uniform(0, base_delay_s)
                delay = min(base_delay_s * (2 ** (attempt - 1)) + jitter, max_delay_s)
                logger.warning(
                    "%s attempt %d/%d failed (%s); retrying in %.2fs",
                    operation,
                    attempt,
                    max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                succeeded = True
                return result
        assert last_error is not None  # for type-checkers; loop always sets it
        raise last_error
    finally:
        # `succeeded`, not `last_error is None`: a retried-then-succeeded call
        # still holds the first attempt's exception, and reporting that as
        # outcome="error" would tell an operator a successful request failed.
        if telemetry is not None:
            telemetry.update(
                attempts=attempts,
                max_attempts=max_attempts,
                retried=attempts > 1,
                total_ms=round((time.perf_counter() - started) * 1000, 2),
                outcome="ok" if succeeded else "error",
            )