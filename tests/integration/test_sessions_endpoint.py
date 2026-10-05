"""Integration tests: session CRUD and session-aware /api/v1/ask (spec Tests 1-4).

Docs Reference: docs/06 Api Design.md §7, docs/04 Database Schema.md §13.

Entire module is marked ``session_integration``. Without a configured
``DB_URL_SESSION`` every test here SKIPS with an actionable reason rather than
passing — see tests/integration/conftest.py for why that distinction matters.

Run the group explicitly to prove the feature is actually exercised:

    pytest -m session_integration -v
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.session_pool import get_session_pool

pytestmark = [pytest.mark.session_integration]


async def _create_session(client, title: str | None = None) -> str:
    body: dict = {} if title is None else {"title": title}
    resp = await client.post("/api/v1/sessions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ----------------------------------------------------------------------
# Test 1 — create session
# ----------------------------------------------------------------------
class TestCreateSession:
    async def test_returns_201_with_generated_id(
        self, session_client, session_store_ready
    ) -> None:
        resp = await session_client.post(
            "/api/v1/sessions", json={"title": "AI Research Indonesia"}
        )
        assert resp.status_code == 201
        data = resp.json()
        # A real v4 UUID, generated server-side.
        assert uuid.UUID(data["id"]).version == 4
        assert data["title"] == "AI Research Indonesia"
        assert data["status"] == "active"
        assert data["last_message_at"] is None
        assert data["created_at"] and data["updated_at"]

    async def test_title_is_optional(self, session_client, session_store_ready) -> None:
        """An untitled session is named after its first question later."""
        resp = await session_client.post("/api/v1/sessions", json={})
        assert resp.status_code == 201
        assert resp.json()["title"]

    async def test_blank_title_is_422(
        self, session_client, session_store_ready
    ) -> None:
        """Rejected by validation with a field path, not by a CHECK constraint."""
        resp = await session_client.post("/api/v1/sessions", json={"title": "   "})
        assert resp.status_code == 422
        assert resp.json()["error"]["error_type"] == "validation_error"


# ----------------------------------------------------------------------
# Test 2 — send a question, both turns persisted
# ----------------------------------------------------------------------
class TestAskPersistsTurns:
    async def test_user_and_assistant_messages_both_persisted(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        sid = await _create_session(session_client, "Persist Test")

        resp = await session_client.post(
            "/api/v1/ask",
            json={
                "session_id": sid,
                "question": "Berapa total publikasi pada tahun 2025?",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["session_id"] == sid

        detail = await session_client.get(f"/api/v1/sessions/{sid}")
        assert detail.status_code == 200
        body = detail.json()

        assert len(body["messages"]) == 2, body["messages"]
        roles = [m["role"] for m in body["messages"]]
        assert roles == ["user", "assistant"]

        assert body["messages"][0]["content"] == (
            "Berapa total publikasi pada tahun 2025?"
        )
        assistant = body["messages"][1]
        assert assistant["content"] == resp.json()["answer"]
        assert assistant["route"] == resp.json()["route"]
        # Provenance: the stored turn is correlatable to the request that made it.
        assert assistant["request_id"] == resp.json()["request_id"]

        # last_message_at advanced from NULL.
        assert body["last_message_at"] is not None

    async def test_session_id_echoed_on_response_without_session_id_absent(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """Backwards compatibility: no session_id in, none out, still 200."""
        resp = await session_client.post(
            "/api/v1/ask", json={"question": "Berapa total publikasi?"}
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] is None

    async def test_not_found_answer_is_still_persisted(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """A zero-evidence turn is a real turn.

        The user asked and the system answered deterministically; dropping it
        would leave a dangling question in the transcript with nothing after it.
        """
        sid = await _create_session(session_client, "NotFound Test")
        resp = await session_client.post(
            "/api/v1/ask",
            json={"session_id": sid, "question": "Daftar publikasi pada tahun 1950"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

        detail = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][1]["content"] == resp.json()["answer"]

    async def test_ask_without_session_does_not_create_a_session(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """The stateless path must not leave residue anywhere."""
        before = len((await session_client.get("/api/v1/sessions")).json())
        await session_client.post(
            "/api/v1/ask", json={"question": "Berapa total publikasi?"}
        )
        after = len((await session_client.get("/api/v1/sessions")).json())
        assert after == before


# ----------------------------------------------------------------------
# Test 3 — session history, chronological
# ----------------------------------------------------------------------
class TestSessionHistory:
    async def test_messages_returned_in_chronological_order(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        sid = await _create_session(session_client, "Chronology Test")
        for q in (
            "Berapa total publikasi?",
            "Siapa penulis paling produktif?",
            "Top 5 publikasi dengan sitasi terbanyak",
        ):
            r = await session_client.post(
                "/api/v1/ask", json={"session_id": sid, "question": q}
            )
            assert r.status_code == 200, r.text

        body = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        messages = body["messages"]
        assert len(messages) == 6  # 3 exchanges
        assert [m["role"] for m in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        timestamps = [m["created_at"] for m in messages]
        assert timestamps == sorted(timestamps), timestamps
        # The order matches what was actually asked.
        assert messages[0]["content"] == "Berapa total publikasi?"
        assert messages[2]["content"] == "Siapa penulis paling produktif?"
        assert messages[4]["content"] == (
            "Top 5 publikasi dengan sitasi terbanyak"
        )

    async def test_detail_includes_summary_and_no_metrics(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """AC-SESSION-12: the summary is context, and the payload carries no
        bibliometric metric field."""
        sid = await _create_session(session_client, "Summary Shape")
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": sid, "question": "Berapa total publikasi?"},
        )
        body = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        assert body["summary"]
        for banned in ("publication_count", "citation_count", "evidence_objects"):
            assert banned not in body


# ----------------------------------------------------------------------
# Test 4 — recent sessions ordering
# ----------------------------------------------------------------------
class TestRecentSessions:
    async def test_sorted_by_last_message_at_desc(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        first = await _create_session(session_client, "Older")
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": first, "question": "Berapa total publikasi?"},
        )
        second = await _create_session(session_client, "Newer")
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": second, "question": "Berapa total publikasi?"},
        )
        empty = await _create_session(session_client, "Never Asked")

        rows = (await session_client.get("/api/v1/sessions")).json()
        ids = [r["id"] for r in rows]
        assert second in ids and first in ids and empty in ids

        # Most recent activity first; the never-asked session sorts last because
        # last_message_at is NULL and the ordering is NULLS LAST.
        assert ids.index(second) < ids.index(first) < ids.index(empty)

        # And the ordering is monotonic over the non-null entries.
        stamps = [r["last_message_at"] for r in rows if r["last_message_at"]]
        assert stamps == sorted(stamps, reverse=True)

    async def test_list_returns_metadata_only_not_the_transcript(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """Spec: do not return full message history from the list endpoint."""
        sid = await _create_session(session_client, "List Shape")
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": sid, "question": "Berapa total publikasi?"},
        )
        rows = (await session_client.get("/api/v1/sessions")).json()
        row = next(r for r in rows if r["id"] == sid)
        assert "messages" not in row
        assert "summary" not in row
        assert set(row) == {
            "id",
            "title",
            "status",
            "created_at",
            "updated_at",
            "last_message_at",
        }

    async def test_limit_is_respected(
        self, session_client, session_store_ready
    ) -> None:
        for i in range(3):
            await _create_session(session_client, f"Bulk {i}")
        rows = (await session_client.get("/api/v1/sessions?limit=2")).json()
        assert len(rows) == 2

    async def test_limit_out_of_range_is_422(
        self, session_client, session_store_ready
    ) -> None:
        assert (
            await session_client.get("/api/v1/sessions?limit=0")
        ).status_code == 422
        assert (
            await session_client.get("/api/v1/sessions?limit=99999")
        ).status_code == 422


# ----------------------------------------------------------------------
# Test 11 (part 1) — invalid session id
# ----------------------------------------------------------------------
class TestInvalidSession:
    async def test_get_unknown_session_returns_404(
        self, session_client, session_store_ready
    ) -> None:
        resp = await session_client.get(f"/api/v1/sessions/{uuid.uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["error_type"] == "session_not_found"

    async def test_malformed_session_id_is_422(
        self, session_client, session_store_ready
    ) -> None:
        """A non-UUID is a validation error, not a database lookup."""
        resp = await session_client.get("/api/v1/sessions/not-a-uuid")
        assert resp.status_code == 422

    async def test_delete_unknown_session_is_204(
        self, session_client, session_store_ready
    ) -> None:
        """DELETE is idempotent; a 404 would invite a retry loop."""
        resp = await session_client.delete(f"/api/v1/sessions/{uuid.uuid4()}")
        assert resp.status_code == 204


# ----------------------------------------------------------------------
# Session store lifecycle
# ----------------------------------------------------------------------
class TestSessionLifecycle:
    async def test_untitled_session_is_named_after_first_question(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        """A derived title is a truncation of the question, never a metric."""
        sid = await _create_session(session_client, None)
        detail = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        assert detail["title"] == "Riset Baru"
        assert detail["title"] != "Berapa total publikasi?"
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": sid, "question": "Berapa total publikasi?"},
        )
        detail = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        assert detail["title"] == "Berapa total publikasi?"

    async def test_user_chosen_title_is_never_overwritten(
        self, session_client, session_store_ready, requires_live_biblio
    ) -> None:
        sid = await _create_session(session_client, "My Own Title")
        await session_client.post(
            "/api/v1/ask",
            json={"session_id": sid, "question": "Berapa total publikasi?"},
        )
        detail = (await session_client.get(f"/api/v1/sessions/{sid}")).json()
        assert detail["title"] == "My Own Title"

    async def test_status_filter_defaults_to_active(
        self, session_client, session_store_ready
    ) -> None:
        sid = await _create_session(session_client, "Archived Soon")
        pool = await get_session_pool()
        assert pool is not None
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE app.research_sessions SET status = 'archived' "
                "WHERE session_id = $1::uuid",
                sid,
            )

        listed = (await session_client.get("/api/v1/sessions")).json()
        active_ids = [r["id"] for r in listed]
        archived = (
            await session_client.get("/api/v1/sessions?status=archived")
        ).json()
        assert sid not in active_ids
        assert sid in [r["id"] for r in archived]

        # Restore so the row does not leak into other tests' ordering assertions.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE app.research_sessions SET status = 'active' "
                "WHERE session_id = $1::uuid",
                sid,
            )