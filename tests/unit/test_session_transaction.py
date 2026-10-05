"""Regression tests: session writes really execute inside their transaction.

Docs Reference: docs/08 §1.4 (transaction boundary),
reports/session_persistence_signoff.md §5.

WHY THIS FILE EXISTS
====================
This is a regression test for a defect that shipped and was invisible to every
other test in the suite:

``SessionRepository`` originally held the ``Pool`` and every method called
``pool.fetchrow(...)``, which **acquires its own connection**. Meanwhile
``SessionService.record_user_message`` opened ``conn.transaction()`` on a
*different* connection. The transaction therefore committed an empty scope while
appearing to cover the writes:

    acquire -> conn#1
    txn:BEGIN on conn#1
    fetchrow -> pool.fetchrow-internal (NO ACTIVE TXN)
    fetchval  -> pool.fetchval-internal (NO ACTIVE TXN)
    txn:COMMIT on conn#1

Every behavioural test still passed, because from the caller's point of view a
transaction *was* opened. Only dispatch inspection revealed that nothing ran
inside it.

So the assertion here is deliberately about DISPATCH, not about outcomes: these
fakes have no database and make no claim about SQL correctness. They record which
connection object each statement went to, and require that it is the one holding
the transaction. That is the property that was broken, and it is checkable
without a live session store.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest

from backend.app.services.session_repository import SessionRepository
from backend.app.services.session_service import SessionService


class RecordingConn:
    """A connection that records whether a transaction is open when it is used."""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log
        self.in_txn = False
        self.commits = 0
        self.rollbacks = 0

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self_inner) -> RecordingConn:
                outer.log.append(f"BEGIN on {outer.name}")
                outer.in_txn = True
                return outer

            async def __aexit__(self_inner, *exc: object) -> bool:
                if exc and exc[0] is not None:
                    outer.log.append(f"ROLLBACK on {outer.name}")
                    outer.rollbacks += 1
                else:
                    outer.log.append(f"COMMIT on {outer.name}")
                    outer.commits += 1
                outer.in_txn = False
                return False

        return _Tx()

    def _use(self, verb: str) -> None:
        self.log.append(
            f"{verb} on {self.name} "
            f"({'INSIDE TXN' if self.in_txn else 'OUTSIDE TXN'})"
        )

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self._use("fetchrow")
        return {"message_id": uuid.uuid4(), "applied_filters": None}

    async def fetchval(self, sql: str, *args: Any) -> None:
        self._use("fetchval")
        return None

    async def execute(self, sql: str, *args: Any) -> None:
        self._use("execute")
        return None


class RecordingPool:
    """Hands out a distinct connection object per acquire, like ``asyncpg.Pool``.

    ``conn_factory`` is a seam rather than a subclass hook: the failure test needs
    a connection whose second statement raises, and overriding ``acquire`` in a
    subclass would have to re-declare the context-manager type.
    """

    def __init__(self, conn_factory: type[RecordingConn] | None = None) -> None:
        self.log: list[str] = []
        self._n = 0
        self.conns: list[RecordingConn] = []
        self._factory = conn_factory or RecordingConn

    def acquire(self):
        self._n += 1
        conn = self._factory(f"conn#{self._n}", self.log)
        self.conns.append(conn)
        conn.log.append(f"acquire -> {conn.name}")

        class _Ctx:
            async def __aenter__(self_inner) -> RecordingConn:
                return conn

            async def __aexit__(self_inner, *exc: object) -> bool:
                return False

        return _Ctx()

    @property
    def statements_outside_txn(self) -> list[str]:
        return [ln for ln in self.log if "OUTSIDE TXN" in ln]

    @property
    def statements_total(self) -> int:
        return sum(
            1 for ln in self.log if ln.startswith(("fetchrow", "fetchval", "execute"))
        )


class TestRepositoryIsConnectionBound:
    """Structural guard: the signature is what makes the fix possible."""

    def test_repository_takes_a_connection_not_a_pool(self) -> None:
        params = list(inspect.signature(SessionRepository.__init__).parameters)
        assert params == ["self", "conn"], params

    def test_repository_exposes_no_pool_accessor(self) -> None:
        """A pool escape hatch is how the service reaches around its transaction.

        With one, ``self._repo.pool.acquire()`` is reachable again and the next
        revision can reopen the same hole.
        """
        assert not hasattr(SessionRepository, "pool")
        assert not [a for a in dir(SessionRepository) if "pool" in a.lower()]

    def test_service_owns_the_pool(self) -> None:
        params = list(inspect.signature(SessionService.__init__).parameters)
        assert params == ["self", "pool"], params


class TestTurnsArePersistedInsideATransaction:
    async def test_user_turn_runs_inside_the_transaction(self) -> None:
        pool = RecordingPool()
        service = SessionService(pool)  # type: ignore[arg-type]

        await service.record_user_message(
            session_id=uuid.uuid4(), content="q", request_id="r1"
        )

        # BOTH directions asserted. "None outside" alone passes vacuously if
        # nothing ran at all — which is exactly what happens when the service
        # swallows an error before dispatch, and it is how the original defect
        # survived a first pass at this test.
        assert pool.statements_total == 2, pool.log
        outside = pool.statements_outside_txn
        assert not outside, (
            "these statements executed outside the transaction: "
            f"{outside}\nfull log: {pool.log}"
        )

    async def test_assistant_turn_runs_inside_the_transaction(self) -> None:
        pool = RecordingPool()
        service = SessionService(pool)  # type: ignore[arg-type]

        await service.record_assistant_message(
            session_id=uuid.uuid4(),
            content="a",
            route="SQLRoute",
            request_id="r1",
        )

        assert pool.statements_total == 2, pool.log
        outside = pool.statements_outside_txn
        assert not outside, (
            "these statements executed outside the transaction: "
            f"{outside}\nfull log: {pool.log}"
        )

    async def test_both_statements_of_a_turn_share_one_connection(self) -> None:
        """INSERT and the session UPDATE must be on the SAME connection.

        Two connections would mean two implicit transactions, which is the bug
        in a different costume.
        """
        pool = RecordingPool()
        service = SessionService(pool)  # type: ignore[arg-type]

        await service.record_user_message(
            session_id=uuid.uuid4(), content="q", request_id="r1"
        )

        verbs = [
            ln.split(" on ")[0]
            for ln in pool.log
            if ln.startswith(("fetchrow", "fetchval", "execute"))
        ]
        assert verbs == ["fetchrow", "fetchval"], verbs

        targets = {
            ln.split(" on ")[1].split(" ")[0]
            for ln in pool.log
            if ln.startswith(("fetchrow", "fetchval", "execute"))
        }
        assert len(targets) == 1, f"statements split across connections: {targets}"

    async def test_transaction_is_opened_and_committed(self) -> None:
        pool = RecordingPool()
        service = SessionService(pool)  # type: ignore[arg-type]

        await service.record_user_message(
            session_id=uuid.uuid4(), content="q", request_id="r1"
        )

        assert any(ln.startswith("BEGIN on") for ln in pool.log), pool.log
        assert sum(1 for ln in pool.log if ln.startswith("COMMIT on")) == 1, pool.log
        assert sum(1 for ln in pool.log if ln.startswith("ROLLBACK on")) == 0, pool.log


class TestTransactionRollsBackOnFailure:
    async def test_a_failing_statement_rolls_the_turn_back(self) -> None:
        """If the session UPDATE fails, the INSERT must not survive.

        This is the whole point of the boundary: a half-written turn with a stale
        ``last_message_at`` is the failure the transaction exists to prevent.
        """

        class FailingConn(RecordingConn):
            async def fetchval(self, sql: str, *args: Any) -> None:
                self._use("fetchval")
                raise RuntimeError("simulated touch_session failure")

        pool = RecordingPool(conn_factory=FailingConn)
        service = SessionService(pool)  # type: ignore[arg-type]

        result = await service.record_user_message(
            session_id=uuid.uuid4(), content="q", request_id="r1"
        )

        # Best-effort contract preserved: the request is not failed by storage.
        assert result is None
        assert sum(1 for ln in pool.log if ln.startswith("ROLLBACK on")) == 1, pool.log
        assert sum(1 for ln in pool.log if ln.startswith("COMMIT on")) == 0, pool.log


class TestSingleStatementOperationsNeedNoTransaction:
    """Not every method needs one; asserting that keeps the boundary honest."""

    @pytest.mark.parametrize(
        "method_name",
        [
            "create_session",
            "get_session",
            "list_sessions",
            "get_session_detail",
            "delete_session",
            "load_context",
            "archive_session",
            "adopt_title_from_first_question",
            "refresh_summary",
        ],
    )
    def test_read_and_single_write_paths_open_no_transaction(
        self, method_name: str
    ) -> None:
        """These are one statement or pure reads — already atomic on their own.

        Wrapping each in a transaction would buy nothing and would make the one
        place that *needs* a transaction (`_persist_turn`) harder to find.
        """
        src = inspect.getsource(getattr(SessionService, method_name))
        assert "transaction()" not in src, (
            f"{method_name} opens a transaction but does not need one"
        )

    def test_exactly_one_method_opens_a_transaction(self) -> None:
        """Single choke point for the transaction boundary."""
        openers = [
            name
            for name, member in inspect.getmembers(SessionService, inspect.isfunction)
            if "transaction()" in inspect.getsource(member)
        ]
        assert openers == ["_persist_turn"], openers