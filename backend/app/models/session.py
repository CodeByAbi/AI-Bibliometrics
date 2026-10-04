"""Session persistence schemas — the application domain DTOs.

Docs Reference: docs/04 Database Schema.md §13, docs/06 Api Design.md §7.

Everything here is conversation state. None of it is evidence, and none of it
may be read as a bibliometric fact (docs/03 §0.3 invariant 5 — Session
Isolation Invariant).

Column-name vs field-name mapping, for anyone reading the migration alongside
these models:

    database column        API field
    ---------------------  ---------
    research_sessions.session_id   id
    research_messages.message_id   id
    research_messages.session_id   (path/query param only; not echoed per-message)

The database keeps the repo's ``<entity>_id`` convention (publication_id,
author_id, topic_id); the wire format keeps the shorter ``id`` the API spec
publishes. Both are deliberate — see migration 005 for the same reasoning.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: FilterParams keys that may be carried in ``applied_filters`` and replayed as
#: conversation scope. Deliberately an explicit allow-list rather than "whatever
#: FilterParams happens to have": a new FilterParams field must be opted into
#: scope replay by a conscious decision, not picked up implicitly. ``year`` is
#: excluded because a bare equality year is a poor inherited default — the range
#: fields (year_from / year_to) carry a window and inherit meaningfully.
SCOPE_INHERITABLE_KEYS: frozenset[str] = frozenset(
    {
        "country",
        "author_name",
        "institution_name",
        "topic_name",
        "keyword",
        "document_type",
        "year_from",
        "year_to",
    }
)

#: Canonical ITERATION order for the keys above.
#:
#: Separate from ``SCOPE_INHERITABLE_KEYS`` because that is a ``frozenset``, and
#: frozenset iteration order for strings depends on PYTHONHASHSEED. Iterating it
#: directly made the rendered summary — which is documented as deterministic —
#: come out in a different order in different processes, so two workers
#: regenerating the same summary produced different strings and the upsert churned
#: for no reason. Membership tests use the frozenset; anything that renders or
#: serialises uses this tuple.
SCOPE_KEY_ORDER: tuple[str, ...] = (
    "country",
    "author_name",
    "institution_name",
    "topic_name",
    "keyword",
    "document_type",
    "year_from",
    "year_to",
)

#: Indonesian display labels for the scope keys, rendered as "label=value".
#:
#: Lives here, next to ``SCOPE_KEY_ORDER``, because two independent renderers
#: needed this map: the summary text shown in ``GET /sessions/{id}`` and the
#: ``[Cakupan sesi]`` block injected into the LLM narration prompt. They had
#: drifted into two separate copies of the same dict, and a label edited in one
#: place would silently not appear in the other.
SCOPE_LABELS: dict[str, str] = {
    "country": "Negara",
    "author_name": "Penulis",
    "institution_name": "Institusi",
    "topic_name": "Topik",
    "keyword": "Kata kunci",
    "document_type": "Tipe dokumen",
    "year_from": "Tahun mulai",
    "year_to": "Tahun akhir",
}

assert frozenset(SCOPE_KEY_ORDER) == SCOPE_INHERITABLE_KEYS, (
    "SCOPE_KEY_ORDER and SCOPE_INHERITABLE_KEYS must describe the same key set; "
    "an ordered tuple plus a frozenset that disagree means one was updated "
    "without the other."
)

assert frozenset(SCOPE_LABELS) == SCOPE_INHERITABLE_KEYS, (
    "SCOPE_LABELS must cover exactly the inherit-able keys; a missing key would "
    "render as a raw snake_case field name, and an extra key would never render."
)


def render_scope_parts(scope: dict[str, Any]) -> list[str]:
    """Render a scope dict as ordered ``label=value`` fragments.

    Single renderer for both the persisted summary text and the prompt block, so
    the two cannot drift. Keys with a ``None`` or empty value are omitted, and
    iteration follows ``SCOPE_KEY_ORDER`` so output is byte-stable across
    processes.

    Pure data function with no I/O, like ``merge_applied_filters`` above.
    """
    parts: list[str] = []
    for key in SCOPE_KEY_ORDER:
        value = scope.get(key)
        if value is None or value == "":
            continue
        parts.append(f"{SCOPE_LABELS[key]}={value}")
    return parts


def merge_applied_filters(filter_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge scope dicts across turns, most-recent-value-wins.

    Inputs are in chronological order, so later turns override earlier ones —
    "the scope moved from Indonesia to Malaysia" is honoured. Only
    ``SCOPE_INHERITABLE_KEYS`` survive, which is what stops an unexpected field
    in stored provenance from ever reaching a FilterParams.

    Lives here rather than in either service so the repository-facing service and
    the summary renderer can share one definition without importing each other.
    It is a pure data function with no I/O, matching the precedent of
    ``format_citation`` in ``models/ask.py``.
    """
    merged: dict[str, Any] = {}
    for filters in filter_list:
        if not filters:
            continue
        for key in SCOPE_KEY_ORDER:
            value = filters.get(key)
            if value is not None and value != "":
                merged[key] = value
    return merged


class SessionStatus(BaseModel):
    """Lifecycle literals for a research session."""

    model_config = ConfigDict(frozen=True)

    ACTIVE: Literal["active"] = "active"
    ARCHIVED: Literal["archived"] = "archived"


#: The two-state lifecycle as a plain literal alias, for use in signatures.
SessionStatusLiteral = Literal["active", "archived"]
MessageRoleLiteral = Literal["user", "assistant"]
MessageStatusLiteral = Literal["complete", "failed"]


class SessionCreateRequest(BaseModel):
    """Body of POST /api/v1/sessions."""

    model_config = ConfigDict(frozen=True)

    title: str | None = Field(
        default=None,
        max_length=200,
        description="Judul riset. Opsional: bila kosong, judul diturunkan dari "
        "pertanyaan pertama.",
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, v: str | None) -> str | None:
        """Collapse whitespace and reject a blank-but-present title.

        A title of "   " must not become an empty-titled session: the CHECK
        constraint would reject the INSERT at the database level, which is a
        worse error message than a 422 and loses the field path.
        """
        if v is None:
            return None
        t = " ".join(v.split())
        if not t:
            raise ValueError("title must not be blank when provided")
        return t


class SessionCreatedResponse(BaseModel):
    """Response of POST /api/v1/sessions (201)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    status: SessionStatusLiteral = "active"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class SessionListItem(BaseModel):
    """One row of GET /api/v1/sessions.

    Metadata only, by design: the list endpoint must not drag the transcript
    along, so a user with 200 sessions does not pay for 200 message histories.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    status: SessionStatusLiteral = "active"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class SessionMessageResponse(BaseModel):
    """One stored turn."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    role: MessageRoleLiteral
    content: str
    status: MessageStatusLiteral = "complete"
    created_at: datetime
    #: Provenance only — lets an operator tie a stored turn to its log line and
    #: its AskResponse. Never evidence.
    request_id: str | None = None
    route: str | None = None


class SessionDetailResponse(BaseModel):
    """Response of GET /api/v1/sessions/{session_id}.

    Metadata plus the full transcript, chronologically. NOT a bibliometric
    retrieval endpoint: no metric, count, or aggregate appears here, because
    anything the UI wants to show numerically must be re-asked through
    /api/v1/ask against the canonical corpus.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    status: SessionStatusLiteral = "active"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    messages: list[SessionMessageResponse] = Field(default_factory=list)
    summary: str | None = Field(
        default=None,
        description="Conversation summary. Untrusted, user-influenced text — "
        "context, never evidence.",
    )


class ConversationScope(BaseModel):
    """Resolved conversational scope — what the conversation is *about*.

    These are the user's own stated constraints replayed forward, not facts
    discovered from the corpus. Merging a scope into an AskRequest changes which
    rows retrieval looks at; it never supplies an answer.
    """

    model_config = ConfigDict(frozen=True)

    country: str | None = None
    author_name: str | None = None
    institution_name: str | None = None
    topic_name: str | None = None
    keyword: str | None = None
    document_type: str | None = None
    year_from: int | None = None
    year_to: int | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            getattr(self, k) is not None for k in SCOPE_KEY_ORDER
        )

    def as_filter_kwargs(self) -> dict[str, Any]:
        """Non-None scope fields, shaped to splat into FilterParams.

        Iterates ``SCOPE_KEY_ORDER`` so the dict is insertion-ordered
        deterministically, which keeps the JSONB provenance column stable.
        """
        return {
            k: getattr(self, k)
            for k in SCOPE_KEY_ORDER
            if getattr(self, k) is not None
        }


class ConversationMessage(BaseModel):
    """A stored turn as the context builder sees it."""

    model_config = ConfigDict(frozen=True)

    role: MessageRoleLiteral
    content: str
    created_at: datetime
    applied_filters: dict[str, Any] = Field(default_factory=dict)


class ConversationContext(BaseModel):
    """Bounded context handed to the question pipeline.

    Composed of ``summary`` + at most ``recent_messages_limit`` recent turns +
    the current question. Explicitly bounded: the prompt must not grow with
    session length, so a 500-turn session and a 3-turn session cost the same.

    Security: every field here is user-influenced. It is untrusted data with
    the same standing as indexed publication text — it may inform
    interpretation but can never override system instructions or stand in for
    evidence.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    summary: str | None = None
    recent_messages: list[ConversationMessage] = Field(default_factory=list)
    scope: ConversationScope = Field(default_factory=ConversationScope)

    @property
    def is_empty(self) -> bool:
        return not self.summary and not self.recent_messages