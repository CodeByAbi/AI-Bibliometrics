"""Retry with exponential backoff + jitter for transient outbound calls.

Docs Reference: docs/05 Retrieval Rag Design.md (Ollama budgets), docs/08 Security.md.

Contract:
- Retry ONLY retriable errors (timeouts, connection errors, HTTP 5xx).
  Never retry 4xx client errors — they fail fast.
- Every attempt failure is logged server-side; callers receive the last
  error chained via ``raise ... from exc`` so context is never lost.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from backend.app.core.logging import logger

T = TypeVar("T")


def is_retriable_http_status(status_code: int) -> bool:
    """True for transient 5xx / 429; False for 4xx client errors."""
    return status_code == 429 or 500 <= status_code <= 599


def is_retriable_exception(exc: BaseException) -> bool:
    """True for network/transient failures worth one more attempt."""
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, asyncio.TimeoutError))


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 0.5,
    max_delay_s: float = 10.0,
    operation: str = "outbound-call",
) -> T:
    """Execute ``fn`` with exponential backoff + jitter.

    Raises the last error (chained) when attempts are exhausted or the
    error is non-retriable.
    """
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_error = exc
            # Non-retriable: fail fast, loudly, with context.
            if not is_retriable_exception(exc):
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
    assert last_error is not None  # for type-checkers; loop always sets it
    raise last_error
