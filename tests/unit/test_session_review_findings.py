"""Regression tests for the four Required findings from the session code review.

Docs Reference: reports/session_persistence_signoff.md §5a (findings R1-R4).

Each finding here was a claim in the code or in the docs that did not match
behaviour:

  R1  Scope labels were duplicated across two renderers and free to drift.
  R2  ``refresh_summary`` read the ENTIRE transcript on every session-aware
      request, contradicting the bounded-context design.
  R3  ``status='failed'`` existed in the schema and in comments but no code path
      ever wrote it.
  R4  ``use_session_context=false`` still injected the whole transcript into the
      LLM prompt; the flag only gated filter inheritance.

No database required. R1/R2 are service-level; R3/R4 are router-level with a
stubbed pipeline and a stub session service.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from backend.app.models.session import (
    SCOPE_INHERITABLE_KEYS,
    SCOPE_KEY_ORDER,
    SCOPE_LABELS,
    ConversationContext,
    ConversationMessage,
    ConversationScope,
    merge_applied_filters,
    render_scope_parts,
)

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "app"
ROOT = Path(__file__).resolve().parents[2]

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _msg(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, created_at=_NOW  # type: ignore[arg-type]
    )


# ======================================================================
# R1 - one scope renderer, not two
# ======================================================================
class TestScopeRenderingIsNotDuplicated:
    """R1: ``_render_scope`` and ``_render_scope_parts`` were the same function."""

    def test_exactly_one_scope_renderer_exists_in_the_package(self) -> None:
        """No second copy of the renderer may reappear.

        Structural on purpose: two byte-identical renderers pass every output
        test and still drift the moment someone renames one label. Counting the
        definitions is the only guard that catches it.
        """
        offenders: list[str] = []
        for path in BACKEND.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    if not node.name.startswith(("_render_scope", "render_scope")):
                        continue
                    canonical = (
                        node.name == "render_scope_parts"
                        and path.name == "session.py"
                        and path.parent.name == "models"
                    )
                    if not canonical:
                        offenders.append(f"{path.name}:{node.lineno} {node.name}")
        assert offenders == [], f"duplicate scope renderers found: {offenders}"

    def test_no_module_level_label_map_outside_models(self) -> None:
        """The label map must have exactly one home."""
        offenders: list[str] = []
        for path in BACKEND.rglob("*.py"):
            if path.name == "session.py" and path.parent.name == "models":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name) and node.target.id in {
                        "SCOPE_LABEL_ID",
                        "SCOPE_LABELS",
                    }:
                        offenders.append(f"{path.name}:{node.lineno} {node.target.id}")
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id in {"SCOPE_LABEL_ID", "SCOPE_LABELS"}
                        ):
                            offenders.append(f"{path.name}:{node.lineno} {target.id}")
        assert offenders == [], f"label map copied into {offenders}"

    def test_both_services_import_the_canonical_renderer(self) -> None:
        """A service that stops calling it is how the two paths diverge again."""
        from backend.app.services import session_service as svc_mod
        from backend.app.services import session_summary_service as sum_mod

        assert sum_mod.render_scope_parts is render_scope_parts, (
            "summary service does not use the shared renderer"
        )
        assert svc_mod.render_scope_parts is render_scope_parts, (
            "session service does not use the shared renderer"
        )

    def test_labels_cover_exactly_the_inheritable_keys(self) -> None:
        """A missing key renders as raw snake_case; an extra key never renders."""
        assert frozenset(SCOPE_LABELS) == SCOPE_INHERITABLE_KEYS

    def test_empty_and_none_values_are_skipped(self) -> None:
        """The one behaviour both copies had to agree on."""
        scope = {"country": "Indonesia", "author_name": "", "keyword": None}
        assert render_scope_parts(scope) == ["Negara=Indonesia"]

    def test_render_order_follows_scope_key_order(self) -> None:
        scope = {k: f"v{i}" for i, k in enumerate(SCOPE_KEY_ORDER)}
        parts = render_scope_parts(scope)
        assert len(parts) == len(SCOPE_KEY_ORDER)
        # Labels appear in SCOPE_KEY_ORDER sequence, not dict insertion order.
        labels = [p.split("=", 1)[0] for p in parts]
        assert labels == [SCOPE_LABELS[k] for k in SCOPE_KEY_ORDER], labels


# ======================================================================
# R2 - the summary refresh must read a bounded window
# ======================================================================
class _WindowConn:
    """Connection that records whether the read was bounded, and by how much."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._rows = rows or []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        upper = sql.upper()
        if "LIMIT $1" in upper or "LIMIT " in upper:
            self.calls.append(("bounded", args[-1] if args else None))
        else:
            self.calls.append(("unbounded", None))
        return self._rows

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.calls.append(("fetchrow", None))
        return {}

    async def fetchval(self, sql: str, *args: Any) -> None:
        self.calls.append(("fetchval", None))
        return None

    async def execute(self, sql: str, *args: Any) -> None:
        self.calls.append(("execute", None))
        return None


class _WindowPool:
    def __init__(self, conn: _WindowConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_inner) -> _WindowConn:
                return conn

            async def __aexit__(self_inner, *exc: object) -> bool:
                return False

        return _Ctx()


def _service_with(conn: _WindowConn) -> Any:
    from backend.app.services.session_service import SessionService

    svc = SessionService.__new__(SessionService)
    svc._pool = _WindowPool(conn)  # type: ignore[attr-defined]
    return svc


class TestSummaryRefreshIsBounded:
    """R2: ``refresh_summary`` used ``list_messages`` (no LIMIT) per request."""

    @staticmethod
    def _row() -> dict[str, Any]:
        return {
            "role": "user",
            "content": "Berapa publikasi di Indonesia?",
            "applied_filters": {"country": "Indonesia"},
        }

    async def test_refresh_never_issues_an_unbounded_read(self, monkeypatch) -> None:
        conn = _WindowConn(rows=[self._row()])
        svc = _service_with(conn)

        seen: dict[str, Any] = {}

        async def _fake_upsert(_self: Any, **kw: Any) -> None:
            seen.update(kw)

        monkeypatch.setattr(
            "backend.app.services.session_repository.SessionRepository.upsert_summary",
            _fake_upsert,
        )

        assert await svc.refresh_summary(session_id=uuid.uuid4()) is True, (
            "refresh_summary returned False, so the read/upsert path never ran"
        )

        verbs = [verb for verb, _ in conn.calls]
        assert "unbounded" not in verbs, (
            f"refresh_summary issued an unbounded transcript read: {conn.calls}"
        )
        assert "bounded" in verbs, f"expected the LIMIT-ed read, got {conn.calls}"
        assert seen, "summary was never upserted, so the read path was not exercised"

    async def test_bound_matches_the_context_window_setting(self, monkeypatch) -> None:
        """The summary window and the context window must be the same setting."""
        from backend.app.core.config import get_settings

        conn = _WindowConn(rows=[self._row()])
        svc = _service_with(conn)

        async def _fake_upsert(_self: Any, **kw: Any) -> None:
            return None

        monkeypatch.setattr(
            "backend.app.services.session_repository.SessionRepository.upsert_summary",
            _fake_upsert,
        )
        await svc.refresh_summary(session_id=uuid.uuid4())

        expected = get_settings().session_recent_messages_limit
        limits = [arg for verb, arg in conn.calls if verb == "bounded"]
        assert limits, f"no bounded read recorded: {conn.calls}"
        assert limits[-1] == expected, (
            f"summary window {limits[-1]} != context window {expected}"
        )

    async def test_messages_covered_reports_the_window_not_the_session_total(
        self, monkeypatch
    ) -> None:
        """``messages_covered`` must not imply the whole session was summarised."""
        conn = _WindowConn(rows=[self._row()])
        svc = _service_with(conn)

        seen: dict[str, Any] = {}

        async def _fake_upsert(_self: Any, **kw: Any) -> None:
            seen.update(kw)

        monkeypatch.setattr(
            "backend.app.services.session_repository.SessionRepository.upsert_summary",
            _fake_upsert,
        )
        await svc.refresh_summary(session_id=uuid.uuid4())

        assert seen["messages_covered"] == 1, seen

    async def test_caller_supplied_messages_skip_the_read_entirely(self) -> None:
        """An explicit ``messages`` argument means no transcript query at all."""
        conn = _WindowConn()
        svc = _service_with(conn)
        rows = [
            {
                "role": "user",
                "content": "Berapa publikasi di Indonesia?",
                "applied_filters": None,
            }
        ]

        await svc.refresh_summary(session_id=uuid.uuid4(), messages=rows)  # type: ignore[arg-type]

        read_kinds = {verb for verb, _ in conn.calls} & {"bounded", "unbounded"}
        assert not read_kinds, (
            f"refresh_summary queried even though rows were supplied: {conn.calls}"
        )

    def test_repository_still_exposes_the_full_read(self) -> None:
        """``list_messages`` stays; it is just off the per-request path.

        Removing it would be over-correction: ``GET /sessions/{id}`` and archive
        legitimately want the whole transcript.
        """
        from backend.app.services.session_repository import SessionRepository

        assert hasattr(SessionRepository, "list_messages")
        assert hasattr(SessionRepository, "list_recent_messages")
        assert "limit" in inspect.signature(
            SessionRepository.list_recent_messages
        ).parameters


class TestSummaryBuildHandlesRepositoryRows:
    """R5: the summary was never written, because ``build`` ignored dict rows.

    ``SessionRepository`` returns **dicts**. ``getattr(dict_row, "role")`` is
    ``None``, so every row was filtered out, ``build`` returned ``""``, and
    ``refresh_summary`` read that as "nothing to say" and skipped the upsert.
    The feature was dead in production while every unit test passed, because
    those tests handed in ``ConversationMessage`` models.
    """

    _TURN: ClassVar[dict[str, Any]] = {
        "role": "user",
        "content": "Berapa publikasi di Indonesia?",
        "applied_filters": {"country": "Indonesia"},
    }

    def test_dict_rows_render_a_summary(self) -> None:
        from backend.app.services.session_summary_service import (
            SessionSummaryService,
        )

        out = SessionSummaryService.build([self._TURN], max_chars=900)
        assert out, "a dict row produced no summary - the row was discarded"
        assert "Berapa publikasi" in out, out

    def test_dict_rows_and_models_render_identically(self) -> None:
        """Both shapes must agree, or the summary depends on the call site."""
        from backend.app.services.session_summary_service import (
            SessionSummaryService,
        )

        model = ConversationMessage(
            role="user",
            content=self._TURN["content"],
            applied_filters=self._TURN["applied_filters"],  # type: ignore[arg-type]
            created_at=_NOW,
        )
        from_dict = SessionSummaryService.build([self._TURN], max_chars=900)
        from_model = SessionSummaryService.build([model], max_chars=900)
        assert from_dict == from_model, (from_dict, from_model)

    def test_a_realistic_multi_turn_dict_transcript_renders(self) -> None:
        """Full shape as ``list_recent_messages`` actually returns it."""
        from backend.app.services.session_summary_service import (
            SessionSummaryService,
        )

        rows = [
            {
                "role": "user",
                "content": "Berapa publikasi di Indonesia?",
                "applied_filters": {"country": "Indonesia"},
            },
            {"role": "assistant", "content": "Ada 42.", "applied_filters": None},
            {
                "role": "user",
                "content": "Bagaimana kecenderungannya sejak 2020?",
                "applied_filters": {"country": "Indonesia", "year_from": 2020},
            },
        ]
        out = SessionSummaryService.build(rows, max_chars=900)
        assert out
        assert "Negara=Indonesia" in out, out
        assert "Tahun mulai=2020" in out, out
        assert "Berapa publikasi" in out, "prior question missing"

    def test_a_row_missing_role_is_ignored_not_fatal(self) -> None:
        from backend.app.services.session_summary_service import (
            SessionSummaryService,
        )

        assert SessionSummaryService.build([{"content": "?"}], max_chars=900) == ""


# ======================================================================
# R3 - status='failed' must actually be written
# ======================================================================
class _StubSessionService:
    """Records what ``ask_question`` asks it to persist."""

    def __init__(self, context: ConversationContext | None = None) -> None:
        self.context = context or ConversationContext(
            session_id=uuid.uuid4(), recent_messages=[], scope=ConversationScope()
        )
        self.user_messages: list[dict[str, Any]] = []
        self.assistant_messages: list[dict[str, Any]] = []
        self.summaries: list[uuid.UUID] = []

    @classmethod
    async def create(cls) -> _StubSessionService:
        return cls()

    async def get_session(self, session_id: uuid.UUID) -> dict[str, Any]:
        return {"session_id": session_id}

    async def load_context(self, session_id: uuid.UUID) -> ConversationContext:
        return self.context

    def effective_filters(self, explicit: Any, scope: ConversationScope) -> Any:
        return explicit, []

    async def record_user_message(self, **kw: Any) -> None:
        self.user_messages.append(kw)

    async def record_assistant_message(self, **kw: Any) -> None:
        self.assistant_messages.append(kw)

    async def adopt_title_from_first_question(self, sid: uuid.UUID, q: str) -> None:
        return None

    async def refresh_summary(self, *, session_id: uuid.UUID) -> bool:
        self.summaries.append(session_id)
        return True


class _StubRequest:
    """Just enough of ``Request`` for the ``request.state.request_id`` read."""

    def __init__(self, request_id: str) -> None:
        self.state = type("_S", (), {"request_id": request_id})()


def _install(monkeypatch: Any, svc: _StubSessionService, pipeline: Any) -> None:
    from backend.app.routers import ask as ask_mod

    async def _create() -> _StubSessionService:
        return svc

    monkeypatch.setattr(ask_mod.SessionService, "create", _create)
    monkeypatch.setattr(ask_mod, "_run_ask_pipeline", pipeline)


def _payload(**over: Any) -> Any:
    from backend.app.models.ask import AskRequest

    return AskRequest(**{"question": "Berapa publikasi Indonesia?", **over})


async def _ask(over: dict[str, Any], req_id: str) -> None:
    from backend.app.routers import ask as ask_mod

    await ask_mod.ask_question(  # type: ignore[arg-type]
        _payload(**over), _StubRequest(req_id)  # type: ignore[arg-type]
    )


def _boom(exc: BaseException) -> Any:
    async def _raise(*a: Any, **kw: Any) -> Any:
        raise exc

    return _raise


def _ok(answer: str = "42") -> Any:
    from backend.app.models.ask import AskResponse

    async def _ret(*a: Any, **kw: Any) -> AskResponse:
        return AskResponse(
            request_id="r", status="ok", route="SQLRoute", answer=answer
        )

    return _ret


class TestFailedTurnsAreRecorded:
    """R3: the 'failed' literal existed only in the schema and in comments."""

    async def test_pipeline_failure_persists_a_failed_assistant_turn(
        self, monkeypatch
    ) -> None:
        svc = _StubSessionService()
        _install(monkeypatch, svc, _boom(RuntimeError("simulated retrieval failure")))

        with pytest.raises(RuntimeError):
            await _ask({"session_id": uuid.uuid4()}, "r-1")

        assert len(svc.assistant_messages) == 1, svc.assistant_messages
        turn = svc.assistant_messages[0]
        assert turn["status"] == "failed", turn
        assert turn["request_id"] == "r-1", turn
        assert turn["content"], "a failed turn still needs non-empty content"

    async def test_the_user_turn_is_kept_alongside_the_failed_one(
        self, monkeypatch
    ) -> None:
        """Both turns must be on the record: the question happened."""
        svc = _StubSessionService()
        _install(monkeypatch, svc, _boom(TimeoutError("llm")))

        with pytest.raises(TimeoutError):
            await _ask({"session_id": uuid.uuid4()}, "r-2")

        assert len(svc.user_messages) == 1, svc.user_messages
        assert len(svc.assistant_messages) == 1, svc.assistant_messages

    async def test_exception_text_is_never_stored_in_the_transcript(
        self, monkeypatch
    ) -> None:
        """``GET /sessions/{id}`` returns content verbatim, so str(exc) would leak."""
        from backend.app.routers import ask as ask_mod

        svc = _StubSessionService()
        secret = "postgresql://app_readonly:hunter2@db-prod-01.internal:5432/x"
        _install(monkeypatch, svc, _boom(RuntimeError(secret)))

        with pytest.raises(RuntimeError):
            await _ask({"session_id": uuid.uuid4()}, "r-3")

        content = svc.assistant_messages[0]["content"]
        assert "hunter2" not in content, content
        assert "db-prod-01" not in content, content
        assert content == ask_mod.FAILED_TURN_ANSWER

    async def test_a_persistence_failure_does_not_mask_the_original(
        self, monkeypatch
    ) -> None:
        """The original exception must reach the global handler."""
        svc = _StubSessionService()
        _install(monkeypatch, svc, _boom(ValueError("original failure")))

        async def _record_also_fails(**kw: Any) -> None:
            raise RuntimeError("session store is down too")

        monkeypatch.setattr(svc, "record_assistant_message", _record_also_fails)

        with pytest.raises(ValueError, match="original failure"):
            await _ask({"session_id": uuid.uuid4()}, "r-4")

    async def test_success_path_still_records_complete(self, monkeypatch) -> None:
        """R3 must not turn the happy path into a failed turn."""
        svc = _StubSessionService()
        sid = uuid.uuid4()
        _install(monkeypatch, svc, _ok("42"))

        await _ask({"session_id": sid}, "r-5")

        assert [m["status"] for m in svc.assistant_messages] == ["complete"]
        assert svc.assistant_messages[0]["content"] == "42"
        assert svc.summaries == [sid], "summary should refresh on the happy path"

    async def test_no_session_means_no_failure_write(self, monkeypatch) -> None:
        """Without a session there is no transcript, and no extra work."""
        svc = _StubSessionService()
        _install(monkeypatch, svc, _boom(RuntimeError("boom")))

        with pytest.raises(RuntimeError):
            await _ask({}, "r-6")

        assert svc.assistant_messages == []


# ======================================================================
# R4 - use_session_context=false means the transcript stays out
# ======================================================================
def _pipeline_spy(box: dict[str, Any]) -> Any:
    from backend.app.models.ask import AskResponse

    async def _spy(*a: Any, **kw: Any) -> AskResponse:
        block = kw.get("conversation_block")
        if block is None and len(a) >= 5:
            block = a[4]
        box["block"] = block
        return AskResponse(
            request_id="r", status="ok", route="SQLRoute", answer="ok"
        )

    return _spy


def _context_with_history() -> ConversationContext:
    return ConversationContext(
        session_id=uuid.uuid4(),
        recent_messages=[_msg("user", "sebelumnya: apa itu AI?")],
        scope=ConversationScope(country="Indonesia"),
    )


class TestSessionContextOptOutIsHonoured:
    """R4: the flag gated filters but not the prompt."""

    async def test_opt_out_withholds_the_transcript_from_the_prompt(
        self, monkeypatch
    ) -> None:
        svc = _StubSessionService(context=_context_with_history())
        box: dict[str, Any] = {}
        _install(monkeypatch, svc, _pipeline_spy(box))

        await _ask({"session_id": uuid.uuid4(), "use_session_context": False}, "r-7")

        assert box["block"] is None, (
            f"use_session_context=False still injected a transcript: {box['block']!r}"
        )

    async def test_opt_in_still_supplies_the_transcript(self, monkeypatch) -> None:
        """Guards the opposite failure: gating must not disable the feature."""
        svc = _StubSessionService(context=_context_with_history())
        box: dict[str, Any] = {}
        _install(monkeypatch, svc, _pipeline_spy(box))

        await _ask({"session_id": uuid.uuid4(), "use_session_context": True}, "r-8")

        assert box["block"] is not None, "opt-in lost the conversation block"
        assert "sebelumnya" in box["block"]

    async def test_opt_out_still_records_the_turn(self, monkeypatch) -> None:
        """Opting out of the prompt must not opt out of persistence."""
        svc = _StubSessionService(context=_context_with_history())
        _install(monkeypatch, svc, _ok())

        await _ask({"session_id": uuid.uuid4(), "use_session_context": False}, "r-9")

        assert len(svc.user_messages) == 1, svc.user_messages
        assert len(svc.assistant_messages) == 1, svc.assistant_messages

    def test_the_flag_description_documents_both_effects(self) -> None:
        """The field description is what an API consumer reads. Keep it honest."""
        from backend.app.models.ask import AskRequest

        raw = AskRequest.model_fields["use_session_context"].description or ""
        desc = raw.lower()
        assert "filter" in desc, desc
        assert any(
            w in desc for w in ("llm", "prompt", "narasi", "transkrip", "transcript")
        ), f"description omits the prompt-injection effect: {desc}"

    @pytest.mark.parametrize(
        "rel", ["docs/03 System Architecture.md", "docs/06 Api Design.md"]
    )
    def test_docs_state_that_opt_out_withholds_the_transcript(self, rel: str) -> None:
        """Guard the docs that described the flag as inheritance-only."""
        text = (ROOT / rel).read_text(encoding="utf-8")
        idx = text.lower().find("use_session_context")
        assert idx != -1, f"{rel} no longer documents the flag"
        window = text[max(0, idx - 500) : idx + 900].lower()
        assert any(
            w in window for w in ("prompt", "llm", "transkrip", "narasi")
        ), f"{rel} describes the flag without stating the prompt effect"


# ======================================================================
# Trims
# ======================================================================
class TestPipelineSignatureHasNoDeadParameter:
    def test_pipeline_takes_no_request(self) -> None:
        from backend.app.routers.ask import _run_ask_pipeline

        params = list(inspect.signature(_run_ask_pipeline).parameters)
        assert "request" not in params, f"unused Request still present: {params}"
        assert params == [
            "payload",
            "start_time",
            "req_id",
            "latencies",
            "conversation_block",
        ], params

    def test_scope_key_order_is_a_tuple_not_a_set(self) -> None:
        """Regression guard for the hash-seed determinism defect (defect 2)."""
        assert isinstance(SCOPE_KEY_ORDER, tuple)
        assert isinstance(SCOPE_INHERITABLE_KEYS, frozenset)


class TestScopeMergeAndRenderAgree:
    def test_render_covers_every_key_merge_can_produce(self) -> None:
        """Every key ``merge_applied_filters`` emits must have a label."""
        merged = merge_applied_filters([dict.fromkeys(SCOPE_KEY_ORDER, "v")])
        assert len(render_scope_parts(merged)) == len(SCOPE_KEY_ORDER)

    def test_merge_and_render_round_trip(self) -> None:
        filters = {"country": "Indonesia", "year_from": 2020}
        merged = merge_applied_filters([filters])
        assert render_scope_parts(merged) == ["Negara=Indonesia", "Tahun mulai=2020"]