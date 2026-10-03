"""Process-local counters for the LLM answer-synthesis path.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §6.

WHY THIS EXISTS
---------------
``LlmAnswerSynthesizer.refine`` degrades to the deterministic renderer on ANY
LLM failure and only emits a ``logger.warning`` (docs/05 §7: a request must
never fail because of synthesis). That resilience is correct for availability
but it makes total LLM failure indistinguishable from success at the HTTP
layer: a deployment whose LLM has never once succeeded still returns
``status: "ok"`` with plausible-looking answers.

These counters make that failure mode observable. ``synthesis.fallback_rate``
approaching ``1.0`` means the narrative synthesis — the load-bearing feature for
the STI policy use cases — is not actually running.

SCOPE AND LIMITS (read before relying on these numbers)
-------------------------------------------------------
- Counters are **process-local and reset on restart**. They describe the current
  process lifetime, not history. Persisting history is out of scope here.
- They are **not** shared across uvicorn workers. The current container runs a
  single worker (no ``--workers`` in the compose command), so one process owns
  all traffic. If you ever scale to N workers in one container, each worker
  reports its own partial counters and ``fallback_rate`` must be aggregated
  across processes rather than read from any single one.

Stdlib only: no third-party import here, so this module stays importable and
testable independently of the Prometheus bridge in ``core/metrics.py``.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass

# Canonical fallback reasons. ``llm.py`` classifies every failure into exactly
# one of these; ``unknown`` is the defensive bucket for anything unclassified
# so a new failure mode is still counted (and visible) rather than dropped.
REASON_TIMEOUT = "timeout"
REASON_UNREACHABLE = "unreachable"
REASON_TRANSPORT = "transport"
REASON_HTTP = "http"
REASON_EMPTY = "empty"
REASON_CITATION_STRIPPED = "citation_stripped"
REASON_UNKNOWN = "unknown"

KNOWN_REASONS: frozenset[str] = frozenset(
    {
        REASON_TIMEOUT,
        REASON_UNREACHABLE,
        REASON_TRANSPORT,
        REASON_HTTP,
        REASON_EMPTY,
        REASON_CITATION_STRIPPED,
        REASON_UNKNOWN,
    }
)


@dataclass(frozen=True)
class SynthesisSnapshot:
    """Immutable point-in-time view of synthesis counters."""

    llm_calls: int = 0
    fallback_calls: int = 0
    #: Sorted ``(reason, count)`` pairs — a tuple so the snapshot stays hashable
    #: and genuinely immutable (a dict field would be mutable in a frozen model).
    fallback_by_reason: tuple[tuple[str, int], ...] = ()
    last_llm_ms: float | None = None
    last_fallback_reason: str | None = None

    @property
    def total_calls(self) -> int:
        """Every synthesis attempt, successful or not."""
        return self.llm_calls + self.fallback_calls

    @property
    def fallback_rate(self) -> float:
        """Share of attempts that fell back, in ``[0.0, 1.0]``.

        ``0.0`` when nothing has been attempted yet, so a freshly booted process
        does not report a misleading non-zero rate.
        """
        total = self.total_calls
        if total <= 0:
            return 0.0
        return self.fallback_calls / total

    @property
    def degraded(self) -> bool:
        """True once any attempt has fallen back — a latching hint for health."""
        return self.fallback_calls > 0


class SynthesisStats:
    """Thread-safe counter registry for the synthesis path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._llm_calls = 0
        self._fallback_calls = 0
        self._by_reason: Counter[str] = Counter()
        self._last_llm_ms: float | None = None
        self._last_fallback_reason: str | None = None

    def record_llm(self, llm_ms: float | None = None) -> None:
        """Record one successful LLM synthesis."""
        with self._lock:
            self._llm_calls += 1
            if llm_ms is not None:
                self._last_llm_ms = float(llm_ms)

    def record_fallback(self, reason: str = REASON_UNKNOWN) -> None:
        """Record one degraded synthesis, bucketed by ``reason``."""
        bucket = reason if reason in KNOWN_REASONS else REASON_UNKNOWN
        with self._lock:
            self._fallback_calls += 1
            self._by_reason[bucket] += 1
            self._last_fallback_reason = bucket

    def snapshot(self) -> SynthesisSnapshot:
        """Read a consistent immutable view of all counters."""
        with self._lock:
            pairs = tuple(sorted(self._by_reason.items()))
            return SynthesisSnapshot(
                llm_calls=self._llm_calls,
                fallback_calls=self._fallback_calls,
                fallback_by_reason=pairs,
                last_llm_ms=self._last_llm_ms,
                last_fallback_reason=self._last_fallback_reason,
            )

    def reset(self) -> None:
        """Zero every counter (test isolation)."""
        with self._lock:
            self._llm_calls = 0
            self._fallback_calls = 0
            self._by_reason.clear()
            self._last_llm_ms = None
            self._last_fallback_reason = None


_STATS = SynthesisStats()


def get_synthesis_stats() -> SynthesisStats:
    """Return the process-wide synthesis counter registry."""
    return _STATS
