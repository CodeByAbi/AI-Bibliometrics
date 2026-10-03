"""Prometheus exposition for the synthesis counters.

Docs Reference: docs/06 Api Design.md §6.

``/metrics`` exposes the same numbers that ``GET /api/v1/health`` reports as
``synthesis``, in Prometheus text format, so they can be scraped and alerted on
rather than eyeballed. The bridge reads from
:mod:`backend.app.services.synthesizer.stats` rather than keeping its own copy of
the counters — ``SynthesisStats`` is the single source of truth, so the two views
can never disagree.

Deliberately isolated from ``stats.py``: that module stays stdlib-only so the
counter logic is importable and testable without this dependency.

SECURITY: ``/metrics`` is unauthenticated and reveals model identifiers plus
failure-mode ratios. That is acceptable for a local/dev stack, but once the
backend sits behind Caddy or a tunnel, restrict this path at the edge.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from backend.app.core.config import get_settings
from backend.app.services.synthesizer.stats import KNOWN_REASONS, get_synthesis_stats

_PREFIX = "aibiblio_synthesis"


class SynthesisCollector:
    """Adapter exposing :class:`SynthesisStats` as Prometheus metric families."""

    def collect(self):
        snap = get_synthesis_stats().snapshot()
        model = get_settings().llm_model
        # SynthesisSnapshot keeps reasons as an immutable tuple of pairs, so
        # index it into a dict for the per-reason lookup below.
        by_reason = dict(snap.fallback_by_reason)

        llm_total = CounterMetricFamily(
            f"{_PREFIX}_llm_total",
            "Successful LLM answer syntheses since process start.",
            labels=["model"],
        )
        llm_total.add_metric([model], snap.llm_calls)
        yield llm_total

        # Emit every canonical reason, defaulting to 0, so the series exists
        # before the first failure. Without this, a dashboard shows "no data"
        # precisely when you have not yet proven the pipeline works.
        fallback_total = CounterMetricFamily(
            f"{_PREFIX}_fallback_total",
            "Deterministic-fallback syntheses since process start, by reason.",
            labels=["model", "reason"],
        )
        for reason in sorted(KNOWN_REASONS):
            fallback_total.add_metric(
                [model, reason], by_reason.get(reason, 0)
            )
        yield fallback_total

        ratio = GaugeMetricFamily(
            f"{_PREFIX}_fallback_ratio",
            "Share of synthesis attempts served by the deterministic renderer "
            "(0.0-1.0). Near 1.0 means the LLM path is effectively dead. "
            "Resets on restart.",
        )
        ratio.add_metric([], snap.fallback_rate)
        yield ratio

        # Counters are process-local, so expose uptime-normalised demand too —
        # it survives restarts in aggregate and is the rate to alert on.
        calls = GaugeMetricFamily(
            f"{_PREFIX}_calls_current",
            "Synthesis attempts recorded by this process since start.",
        )
        calls.add_metric([], snap.total_calls)
        yield calls

        last_ms = GaugeMetricFamily(
            f"{_PREFIX}_last_llm_ms",
            "Wall time of the most recent successful LLM call in ms, or 0 if none.",
        )
        last_ms.add_metric([], snap.last_llm_ms or 0)
        yield last_ms


_registry: CollectorRegistry | None = None


def get_registry() -> CollectorRegistry:
    """Return the process registry with the synthesis collector registered once.

    Uses a private registry rather than the global ``REGISTRY`` default so that
    importing this module more than once (test collection, ``importlib.reload``)
    cannot raise ``Duplicated timeseries`` and break app startup.
    """
    global _registry
    if _registry is None:
        registry = CollectorRegistry(auto_describe=True)
        registry.register(SynthesisCollector())
        _registry = registry
    return _registry


def render_metrics() -> bytes:
    """Render the current registry in Prometheus text exposition format."""
    return generate_latest(get_registry())


__all__ = [
    "CONTENT_TYPE_LATEST",
    "SynthesisCollector",
    "get_registry",
    "render_metrics",
]
