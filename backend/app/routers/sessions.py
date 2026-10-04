"""Sessions router — /api/v1/sessions.

Docs Reference: docs/06 Api Design.md §7, docs/04 Database Schema.md §13.

WHAT THIS IS
============
Conversation-state CRUD. Four endpoints:

    POST   /api/v1/sessions            201  create
    GET    /api/v1/sessions            200  recent sessions (metadata only)
    GET    /api/v1/sessions/{id}       200  metadata + full transcript
    DELETE /api/v1/sessions/{id}       204  delete, cascading to the transcript

WHAT THIS IS NOT
================
Not a retrieval endpoint. Nothing here returns a bibliometric metric, and
nothing here can be used to obtain one: the router imports only SessionService,
never a retriever or a synthesizer. A UI that wants a number must ask
``POST /api/v1/ask``, which goes through the retrieval and evidence pipeline.
That is the whole point of the split — a count rendered from a session row
would be a number with no query behind it and no way to be revalidated.

DELETE is confirmed by HTTP 204 with no body, and it is scoped by the schema:
``app.research_sessions`` has ON DELETE CASCADE to the two child tables and no
foreign key to anything in ``public``. Deleting a session therefore cannot
delete, or even touch, a publication.

All four endpoints share the 503 ``session_store_unavailable`` behaviour when
session persistence is not configured. The alternative — silently succeeding and
dropping the session — would look like the feature working.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from backend.app.core.logging import logger
from backend.app.models.session import (
    SessionCreatedResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionListItem,
    SessionMessageResponse,
    SessionUpdateRequest,
)
from backend.app.services.session_repository import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from backend.app.services.session_service import SessionService

router = APIRouter(tags=["Sessions"])


def _to_created(row: dict) -> SessionCreatedResponse:
    return SessionCreatedResponse(
        id=row["session_id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
    )


def _to_list_item(row: dict) -> SessionListItem:
    return SessionListItem(
        id=row["session_id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
        message_count=row.get("message_count") or 0,
        source_count=row.get("source_count") or 0,
        last_route=row.get("last_route"),
    )


def _to_message(row: dict) -> SessionMessageResponse:
    return SessionMessageResponse(
        id=row["message_id"],
        role=row["role"],
        content=row["content"],
        status=row["status"],
        created_at=row["created_at"],
        request_id=row.get("request_id"),
        route=row.get("route"),
        evidence_objects=row.get("evidence_objects") or [],
        sources=row.get("sources") or [],
    )


@router.post(
    "/sessions",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a research session",
)
async def create_session(payload: SessionCreateRequest) -> SessionCreatedResponse:
    """Create a session workspace.

    ``title`` is optional: an untitled session is named after its first question
    by ``POST /api/v1/ask``. A title supplied here is never overwritten.

    503 ``session_store_unavailable`` when session persistence is unconfigured.
    """
    service = await SessionService.create()
    row = await service.create_session(title=payload.title)
    logger.info(
        "Session created: id=%s has_title=%s",
        row["session_id"],
        payload.title is not None,
        extra={"endpoint": "/api/v1/sessions", "session_id": str(row["session_id"])},
    )
    return _to_created(row)


@router.get(
    "/sessions",
    response_model=list[SessionListItem],
    summary="List recent sessions",
)
async def list_sessions(
    limit: int = Query(
        default=DEFAULT_LIST_LIMIT,
        ge=1,
        le=MAX_LIST_LIMIT,
        description="Maximum sessions to return. Metadata only — the "
        "transcript is fetched per session via GET /api/v1/sessions/{id}.",
    ),
    session_status: str | None = Query(
        default=None,
        alias="status",
        pattern="^(active|archived)$",
        description="Lifecycle filter. Defaults to active sessions only.",
    ),
) -> list[SessionListItem]:
    """Recent sessions, newest activity first.

    Ordered by ``last_message_at DESC NULLS LAST``, so a session with no
    messages yet sorts last rather than masquerading as the most recent
    research.

    Operational note: this endpoint shares the per-IP rate-limit budget with
    ``/api/v1/ask`` (docs/08 §3). A client should fetch on mount and after each
    mutation, not poll.
    """
    service = await SessionService.create()
    rows = await service.list_sessions(
        limit=limit, status=session_status  # type: ignore[arg-type]
    )
    return [_to_list_item(row) for row in rows]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    summary="Get a session and its conversation history",
)
async def get_session(session_id: uuid.UUID) -> SessionDetailResponse:
    """Session metadata plus the full transcript, oldest first.

    404 ``session_not_found`` when the session does not exist. The 404 costs one
    indexed lookup on schema ``app`` and never reaches the bibliometric corpus.

    The returned ``summary`` is conversation memory: topic, scope and prior
    questions. It is untrusted, user-influenced text and is never evidence.
    """
    service = await SessionService.create()
    row = await service.get_session_detail(session_id)
    return SessionDetailResponse(
        id=row["session_id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
        messages=[_to_message(m) for m in row["messages"]],
        summary=row.get("summary"),
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionCreatedResponse,
    summary="Rename a research session",
)
async def update_session(
    session_id: uuid.UUID, payload: SessionUpdateRequest
) -> SessionCreatedResponse:
    """Rename a session. Touches nothing else.

    The one mutation a user owns: a title. It is NOT a generic update — the
    request model sets ``extra="forbid"``, so there is no field here that could
    reach the transcript, the lifecycle, or anything bibliometric.

    A supplied title always wins, including over the placeholder and over a
    previously auto-derived title. That is the point of an explicit rename: the
    user is asserting the name, so ``adopt_title_from_first_question`` must not be
    able to overwrite it afterwards. Its SQL predicate still only fires while the
    title equals the placeholder, so the two paths cannot fight.

    404 ``session_not_found`` when the session does not exist; 422 for a blank or
    oversized title; 503 ``session_store_unavailable`` when unconfigured.
    """
    service = await SessionService.create()
    row = await service.rename_session(session_id, payload.title)
    logger.info(
        "Session renamed: id=%s",
        session_id,
        extra={
            "endpoint": "/api/v1/sessions",
            "session_id": str(session_id),
            "operation": "rename",
        },
    )
    return _to_created(row)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session and its conversation history",
)
async def delete_session(session_id: uuid.UUID) -> Response:
    """Delete a session, cascading to its messages and summary.

    204 whether or not the session existed: DELETE is idempotent, and a 404
    would make a client retry loop against a session it has already removed.

    SCOPE: ``research_messages`` and ``research_session_summaries`` only. No
    bibliometric table is referenced by any foreign key in schema ``app``, so
    this operation is structurally incapable of affecting the corpus
    (AC-SESSION-9).
    """
    service = await SessionService.create()
    existed = await service.delete_session(session_id)
    logger.info(
        "Session delete: id=%s existed=%s",
        session_id,
        existed,
        extra={"endpoint": "/api/v1/sessions", "session_id": str(session_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)