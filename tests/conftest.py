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
