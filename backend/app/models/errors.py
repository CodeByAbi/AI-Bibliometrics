"""Standardized error models for the API boundary.

Docs Reference: docs/06 Api Design.md §7, docs/08 Security.md §3.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Detailed error object returned within standard error envelopes."""

    model_config = ConfigDict(frozen=True)

    error_type: str = Field(
        ...,
        description="Categorized error identifier (e.g. validation_error, db_timeout, not_found, internal_error)",
    )
    message: str = Field(
        ...,
        description="Sanitized, human-readable error description safe for client display",
    )
    status_code: int = Field(..., description="HTTP status code")
    details: Optional[Any] = Field(None, description="Optional structured validation details")


class ErrorResponse(BaseModel):
    """Top-level standardized error envelope."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(..., description="UUIDv4 tracking ID for correlating with backend logs")
    error: ErrorDetail = Field(..., description="Structured error payload")
