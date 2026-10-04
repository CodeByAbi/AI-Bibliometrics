"""P0-B: the outbound LLM budget must be bounded and explicitly configured.

Docs Reference: docs/06 Api Design.md §7 (error envelope), docs/05 §5.1.

The defect this file exists to prevent: ``with_retry`` classified
``httpx.TimeoutException`` as retriable and every Ollama caller passed
``max_attempts=2``. On the synchronous ``POST /api/v1/ask`` path an
unsupported SQL question therefore cost
``OLLAMA_TIMEOUT_S (8s) + backoff + OLLAMA_TIMEOUT_S (8s)`` ≈ 17 s, and the
identical retry could not succeed because the same model on the same box needs
the same wall clock.

Two invariants are asserted throughout:

1. A timeout costs exactly ONE outbound attempt.
2. The number of attempts is a configured value (``OLLAMA_MAX_RETRIES``),
   never a literal buried in a call site.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.core.retry import (
    is_retriable_exception,
    with_retry,
)


class _Attempts:
    """Callable that counts attempts and can be told to time out."""

    def __init__(self, *, exc: BaseException | None = None, fail_times: int = 99):
        self.calls = 0
        self._exc = exc
        self._fail_times = fail_times

    async def __call__(self):
        self.calls += 1
        if self._exc is not None and self.calls <= self._fail_times:
            raise self._exc
        return f"ok-after-{self.calls}"


class TestTimeoutsAreNotRetried:
    """The core P0-B invariant: a timeout is terminal, never retried."""

    def test_timeout_is_not_retriable_by_default(self):
        assert is_retriable_exception(httpx.ReadTimeout("stalled")) is False

    def test_asyncio_timeout_is_not_retriable_by_default(self):
        assert is_retriable_exception(TimeoutError()) is False

    def test_connect_error_is_still_retriable(self):
        # Connection errors ARE worth a retry: a pooled keep-alive socket going
        # stale is transient, and the retry genuinely recovers.
        assert is_retriable_exception(httpx.ConnectError("refused")) is True

    def test_timeout_can_be_made_retriable_explicitly(self):
        # Opt-in remains possible for callers that know their dependency is
        # faster on a second try; the default is what protects /ask.
        assert (
            is_retriable_exception(
                httpx.ReadTimeout("stalled"), retry_on_timeout=True
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_single_attempt_on_timeout(self):
        op = _Attempts(exc=httpx.ReadTimeout("stalled"))
        with pytest.raises(httpx.ReadTimeout):
            await with_retry(op, max_attempts=5, operation="test-timeout")
        # 5 attempts were ALLOWED. Exactly 1 was USED.
        assert op.calls == 1

    @pytest.mark.asyncio
    async def test_single_attempt_on_asyncio_timeout(self):
        op = _Attempts(exc=TimeoutError())
        with pytest.raises(asyncio.TimeoutError):
            await with_retry(op, max_attempts=5)
        assert op.calls == 1


class TestBoundedRetryCeiling:
    """Connect errors retry, but never past the configured ceiling."""

    @pytest.mark.asyncio
    async def test_connect_error_retries_up_to_ceiling(self):
        op = _Attempts(exc=httpx.ConnectError("refused"))
        with pytest.raises(httpx.ConnectError):
            await with_retry(
                op, max_attempts=3, base_delay_s=0, operation="test-connect"
            )
        assert op.calls == 3

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        op = _Attempts(exc=httpx.ConnectError("refused"), fail_times=1)
        result = await with_retry(op, max_attempts=2, base_delay_s=0)
        assert result == "ok-after-2"
        assert op.calls == 2

    @pytest.mark.asyncio
    async def test_max_attempts_zero_is_rejected(self):
        # A ceiling of zero would otherwise mean "never call at all" or an
        # off-by-one loop; fail loudly at the boundary instead.
        with pytest.raises(ValueError):
            await with_retry(_Attempts(), max_attempts=0)

    @pytest.mark.asyncio
    async def test_non_transient_error_fails_on_first_attempt(self):
        op = _Attempts(exc=RuntimeError("deterministic bug"))
        with pytest.raises(RuntimeError):
            await with_retry(op, max_attempts=4, base_delay_s=0)
        assert op.calls == 1


class TestRetryTelemetry:
    """``retry_count`` must be observable, not reconstructed from log order."""

    @pytest.mark.asyncio
    async def test_telemetry_reports_no_retry_on_timeout(self):
        telemetry: dict = {}
        with pytest.raises(httpx.ReadTimeout):
            await with_retry(
                _Attempts(exc=httpx.ReadTimeout("stalled")),
                max_attempts=4,
                operation="test",
                telemetry=telemetry,
            )
        assert telemetry["attempts"] == 1
        assert telemetry["retried"] is False
        assert telemetry["outcome"] == "error"
        assert telemetry["total_ms"] >= 0

    @pytest.mark.asyncio
    async def test_telemetry_reports_success_without_retry(self):
        telemetry: dict = {}
        await with_retry(
            _Attempts(), max_attempts=3, base_delay_s=0, telemetry=telemetry
        )
        assert telemetry["attempts"] == 1
        assert telemetry["retried"] is False
        assert telemetry["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_telemetry_reports_the_retry(self):
        telemetry: dict = {}
        op = _Attempts(exc=httpx.ConnectError("refused"), fail_times=1)
        await with_retry(op, max_attempts=3, base_delay_s=0, telemetry=telemetry)
        assert telemetry["attempts"] == 2
        assert telemetry["retried"] is True
        assert telemetry["outcome"] == "ok"


class TestSettingsAreExplicitlyConfigurable:
    """The budgets must be env-driven, not literals in a call site."""

    def test_ollama_max_retries_default_is_one(self):
        # 1 extra attempt => 2 total, preserving the documented
        # "one transient retry" behaviour for connect errors.
        assert get_settings().ollama_max_retries == 1

    def test_text2sql_timeout_is_shorter_than_ollama_timeout(self):
        settings = get_settings()
        # Text-to-SQL is the only LLM call /ask can be blocked on without the
        # caller opting in, so it must not inherit the wider synthesis budget.
        assert settings.text2sql_timeout_s <= settings.ollama_timeout_s

    def test_retry_ceiling_cannot_be_unbounded(self):
        assert get_settings().ollama_max_retries <= 3

    @pytest.mark.asyncio
    async def test_configured_retry_count_drives_total_attempts(self):
        settings = get_settings()
        telemetry: dict = {}
        with pytest.raises(httpx.ReadTimeout):
            await with_retry(
                _Attempts(exc=httpx.ReadTimeout("stalled")),
                max_attempts=1 + settings.ollama_max_retries,
                retry_on_timeout=False,
                telemetry=telemetry,
            )
        # Whatever the operator configured, a timeout still costs one attempt.
        assert telemetry["attempts"] == 1
        assert telemetry["max_attempts"] == 1 + settings.ollama_max_retries