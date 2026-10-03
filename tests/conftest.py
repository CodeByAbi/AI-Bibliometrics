"""Pytest root configuration ensuring project root is in sys.path."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from backend.app.core.middleware import RateLimitingMiddleware


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset rate limiter sliding windows between tests to avoid 429 in fast test suites."""
    RateLimitingMiddleware.reset_all()
    yield
    RateLimitingMiddleware.reset_all()


@pytest.fixture(autouse=True)
def reset_synthesis_stats():
    """Zero the process-wide synthesis counters around every test.

    ``get_synthesis_stats()`` returns a module-level singleton, so without this
    a test that records a fallback would leak its counts into the next test's
    ``fallback_rate`` / ``fallback_by_reason`` assertions.
    """
    from backend.app.services.synthesizer.stats import get_synthesis_stats

    get_synthesis_stats().reset()
    yield
    get_synthesis_stats().reset()


@pytest.fixture(autouse=True)
def isolate_embedding_model_cache():
    """Save/restore the global SentenceTransformer cache around each test.

    Phase 4 audit (M2): ``backend.app.services.embedding._st_model`` is a
    module-level global. Unit tests that patch the loader (e.g. a mock whose
    ``encode`` returns non-finite vectors) would otherwise leak their mock
    into later tests in the same process — e.g. vector endpoint integration
    tests that exercise the real ``generate_query_embedding`` path and then
    fail with 503.

    Save/restore (instead of unconditional clearing) is deliberate: it both
    seals the contamination leak *and* preserves the warm production cache
    across integration tests, so the heavyweight bge-m3 model is loaded once
    per suite rather than re-downloaded/re-loaded per test (slow + flaky).
    """
    import backend.app.services.embedding as embedding_module

    saved_model = embedding_module._st_model
    yield
    embedding_module._st_model = saved_model
