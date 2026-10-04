"""Unit tests: session DTOs are frozen, validated, and evidence-free.

Docs Reference: docs/04 Database Schema.md §13.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.app.models.session import (
    SCOPE_INHERITABLE_KEYS,
    ConversationContext,
    ConversationMessage,
    ConversationScope,
    SessionCreatedResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListItem,
    SessionMessageResponse,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class TestSessionCreateRequest:
    def test_title_is_optional(self) -> None:
        assert SessionCreateRequest().title is None

    def test_title_whitespace_is_collapsed(self) -> None:
        req = SessionCreateRequest(title="  AI   Research\tIndonesia ")
        assert req.title == "AI Research Indonesia"

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n  "])
    def test_blank_title_rejected(self, blank: str) -> None:
        """A blank title must fail as a 422 with a field path, not as a CHECK
        constraint violation at INSERT time with a worse message."""
        with pytest.raises(ValidationError):
            SessionCreateRequest(title=blank)

    def test_title_length_capped(self) -> None:
        with pytest.raises(ValidationError):
            SessionCreateRequest(title="x" * 201)

    def test_frozen(self) -> None:
        req = SessionCreateRequest(title="X")
        with pytest.raises(ValidationError):
            req.title = "Y"  # type: ignore[misc]


class TestResponseModels:
    def test_created_response_defaults_to_active(self) -> None:
        r = SessionCreatedResponse(
            id=uuid.uuid4(), title="T", created_at=NOW, updated_at=NOW
        )
        assert r.status == "active"
        assert r.last_message_at is None

    def test_list_item_requires_no_messages_field(self) -> None:
        """The list endpoint must not carry the transcript. Assert the field
        does not exist so it cannot be added by accident."""
        assert "messages" not in SessionListItem.model_fields

    def test_detail_response_messages_default_empty(self) -> None:
        d = SessionDetailResponse(
            id=uuid.uuid4(), title="T", created_at=NOW, updated_at=NOW
        )
        assert d.messages == []
        assert d.summary is None

    def test_message_response_rejects_unknown_role(self) -> None:
        with pytest.raises(ValidationError):
            SessionMessageResponse(
                id=uuid.uuid4(),
                role="system",  # type: ignore[arg-type]
                content="x",
                created_at=NOW,
            )

    def test_message_response_rejects_failed_status_typo(self) -> None:
        with pytest.raises(ValidationError):
            SessionMessageResponse(
                id=uuid.uuid4(),
                role="user",
                content="x",
                status="errored",  # type: ignore[arg-type]
                created_at=NOW,
            )

    def test_no_bibliometric_metric_field_on_any_session_dto(self) -> None:
        """AC-SESSION-2 / AC-SESSION-8: no session DTO may expose a metric.

        A `publication_count` or `citation_count` here would be a stale copy of
        `public` with no way for a reader to know it is the stale one.

        Migration 006 permits a per-turn provenance snapshot, which does contain
        metric values, so this list deliberately EXCLUDES `evidence_objects` —
        that exception is fenced separately and narrowly by
        ``test_provenance_snapshot_is_confined_to_the_message_dto`` below, which
        fails if the field ever appears anywhere but on the per-turn DTO.

        The aggregate names below remain banned everywhere, including on
        ``SessionMessageResponse``. An evidence snapshot is a receipt for one
        answered turn; a `publication_count` column would be a session-level
        rollup, which is exactly what the invariant forbids and exactly what
        migration 006 refuses to create.
        """
        banned = {
            "publication_count",
            "citation_count",
            "author_count",
            "institution_count",
            "total_publications",
            "top_authors",
            "top_institutions",
            "expertise_score",
            "growth_score",
            "citation_acceleration",
            "evidence",
        }
        for model in (
            SessionCreateRequest,
            SessionCreatedResponse,
            SessionListItem,
            SessionDetailResponse,
            SessionMessageResponse,
        ):
            leaked = banned & set(model.model_fields)
            assert not leaked, f"{model.__name__} exposes metric field(s): {leaked}"

    def test_provenance_snapshot_is_confined_to_the_message_dto(self) -> None:
        """The migration 006 exception is per-turn and must stay that way.

        ``evidence_objects`` / ``sources`` are the only session fields permitted to
        carry bibliometric values, and only as an immutable snapshot of ONE already
        verified response. This test pins that scope so the exception cannot widen
        by accident:

        * permitted on ``SessionMessageResponse`` — the per-turn DTO;
        * forbidden on ``SessionListItem`` — which would make it a session-level
          aggregate, the thing migration 005 refused and 006 explicitly declines;
        * forbidden on ``SessionDetailResponse`` — the session envelope, so a
          client cannot read a provenance snapshot as "the session's evidence";
        * forbidden on the create/created DTOs, where there is no turn to describe.
        """
        allowed = {"SessionMessageResponse"}
        snapshot_fields = {"evidence_objects", "sources"}

        for model in (
            SessionCreateRequest,
            SessionCreatedResponse,
            SessionListItem,
            SessionDetailResponse,
            SessionMessageResponse,
        ):
            leaked = snapshot_fields & set(model.model_fields)
            if model.__name__ in allowed:
                assert leaked == snapshot_fields, (
                    f"{model.__name__} must expose {sorted(snapshot_fields)} to "
                    f"restore a past turn's evidence rail; got {sorted(leaked)}"
                )
            else:
                assert not leaked, (
                    f"{model.__name__} exposes per-turn provenance field(s) "
                    f"{sorted(leaked)}; a snapshot is per-turn and must never "
                    f"become a session-level aggregate"
                )


class TestConversationScope:
    def test_empty_scope_reports_empty(self) -> None:
        assert ConversationScope().is_empty

    def test_populated_scope_reports_non_empty(self) -> None:
        assert not ConversationScope(country="indonesia").is_empty

    def test_filter_kwargs_only_include_set_keys(self) -> None:
        scope = ConversationScope(country="indonesia", year_from=2020)
        assert scope.as_filter_kwargs() == {"country": "indonesia", "year_from": 2020}

    def test_filter_kwargs_keys_are_all_inheritable(self) -> None:
        """Every key the scope can emit must be on the inheritance allow-list,
        or a scope could set a filter the contract says is not inherited."""
        scope = ConversationScope(
            country="a",
            author_name="b",
            institution_name="c",
            topic_name="d",
            keyword="e",
            document_type="f",
            year_from=2020,
            year_to=2025,
        )
        assert set(scope.as_filter_kwargs()) <= SCOPE_INHERITABLE_KEYS

    def test_year_excluded_from_inheritable_keys(self) -> None:
        """A bare equality year is a poor inherited default; the range fields
        carry a window and inherit meaningfully. Documented in models/session.py."""
        assert "year" not in SCOPE_INHERITABLE_KEYS
        assert {"year_from", "year_to"} <= SCOPE_INHERITABLE_KEYS


class TestConversationContext:
    def _msg(
        self,
        role: str,
        content: str,
        filters: dict | None = None,
    ) -> ConversationMessage:
        return ConversationMessage(
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=NOW,
            applied_filters=filters or {},
        )

    def test_empty_context(self) -> None:
        assert ConversationContext(session_id=uuid.uuid4()).is_empty

    def test_summary_only_context_is_not_empty(self) -> None:
        ctx = ConversationContext(session_id=uuid.uuid4(), summary="AI di Indonesia")
        assert not ctx.is_empty

    def test_frozen(self) -> None:
        ctx = ConversationContext(session_id=uuid.uuid4())
        with pytest.raises(ValidationError):
            ctx.summary = "x"  # type: ignore[misc]