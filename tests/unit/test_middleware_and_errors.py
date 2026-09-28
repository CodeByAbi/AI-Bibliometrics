"""Unit tests for middleware, error formatting, and structured logging.

Docs Reference: docs/06 Api Design.md §7, docs/08 Security.md §4.
"""

from __future__ import annotations

import json
import logging
import pytest
from backend.app.core.errors import AppException, DBTimeoutError
from backend.app.core.logging import StructuredJSONFormatter
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
