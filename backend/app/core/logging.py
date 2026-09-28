"""Structured JSON logging for backend observibility.

Docs Reference: docs/08 Security.md §4, docs/11 Roadmap.md §4 (Fase 2).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any, Optional

# Context variable for current request ID
current_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_request_id", default=None
)


class StructuredJSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject request_id from contextvar or record attribute
        req_id = getattr(record, "request_id", None) or current_request_id.get()
        if req_id:
            payload["request_id"] = req_id

        # Inject structured audit fields if present in record.__dict__
        for attr in ("route", "status", "latency_ms", "error_code", "db_query_time_ms", "endpoint"):
            val = getattr(record, attr, None)
            if val is not None:
                payload[attr] = val

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "ai_bibliometrics") -> logging.Logger:
    """Get or configure a structured logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = get_logger("ai_bibliometrics.api")
