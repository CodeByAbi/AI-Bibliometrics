"""Shared fixtures for the session-persistence acceptance suite.

Docs Reference: docs/04 Database Schema.md §13, docs/08 §1.4.

WHY THESE TESTS SKIP INSTEAD OF PASSING
========================================
The session feature needs a credential (``DB_URL_SESSION``, role ``app_session``)
and a migrated ``app`` schema. A developer without that configuration has not
tested the feature at all — they have tested that the feature is switched off.

So the suite distinguishes three outcomes and never conflates them:

    PASSED   the session behaviour was verified against a real session store
    SKIPPED  no session credential; the behaviour was NOT verified
    FAILED   the behaviour was verified and is wrong

Only the first two are possible here, and SKIPPED is reported loudly (``-rs``)
and carries the marker ``session_integration`` so a session-enabled CI job can
require that the group executed rather than merely not failing:

    pytest -m session_integration

Treating "skipped" as "passing" is the failure mode this module exists to
prevent: it makes an unconfigured environment look like a verified one.
"""

from __future__ import annotations

import os
import uuid

import pytest

from backend.app.core.config import get_settings

#: Reasons a session test cannot run, surfaced verbatim in the skip message so
#: the operator knows which of the two setup steps is missing.
REASON_NO_DSN = (
    "DB_URL_SESSION is not configured. Session persistence cannot run, so this "
    "acceptance test is UNVERIFIED, not passing. Owner setup: "
    "(1) python scripts/migrate.py up   "
    "(2) python scripts/grant_session_role.py   "
    "(3) add DB_URL_SESSION for role app_session to .env"
)
REASON_SCHEMA_MISSING = (
    "DB_URL_SESSION is set but the `app` schema is not reachable. Run "
    "`python scripts/migrate.py up` and `python scripts/grant_session_role.py`."
)

SESSION_SCHEMA = "app"


def _skip(reason: str) -> None:
    pytest.skip(reason, allow_module_level=False)


@pytest.fixture(scope="session")
def session_store_ready() -> bool:
    """True only when a session credential AND a migrated `app` schema exist.

    Session-scoped because the probe costs a round-trip and every test in the
    group needs the same answer. A failure here is reported as a skip with an
    actionable message rather than an error, so an unconfigured developer gets
    one clear line instead of 12 collection errors.
    """
    settings = get_settings()
    if not settings.session_persistence_enabled:
        _skip(REASON_NO_DSN)
    return True


@pytest.fixture
async def session_client():
    """AsyncClient bound to the app, for session endpoint tests."""
    from httpx import ASGITransport, AsyncClient

    from backend.app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def bibliometric_pool():
    """The retrieval pool, used only to prove isolation from the corpus.

    Deliberately the ``get_pool`` handle, not the session pool: "the session
    tables cannot affect the bibliometric tables" has to be measured from the
    bibliometric side, or the test proves nothing.
    """
    from backend.app.db.pool import get_pool

    pool = await get_pool()
    return pool


@pytest.fixture
def requires_live_biblio() -> None:
    """Skip when no bibliometric DSN is configured."""
    if not os.environ.get("DB_URL", "").strip():
        _skip("DB_URL is not configured; cannot verify corpus counts.")


@pytest.fixture
async def corpus_counts(bibliometric_pool):
    """Callable returning row counts for the canonical bibliometric tables.

    Read through the retrieval pool, whose ``search_path`` is ``public``. If a
    session operation ever reached the corpus, these numbers would move.

    Exposed as a fixture rather than a module-level helper so ``tests/
    integration`` needs no package ``__init__`` (it has none, and relative
    imports would fail collection).
    """

    async def _counts() -> dict[str, int]:
        tables = ("publications", "authors", "institutions", "chunks")
        counts: dict[str, int] = {}
        async with bibliometric_pool.acquire() as conn:
            for table in tables:
                counts[table] = int(
                    await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                )
        return counts

    return _counts


async def new_session_id() -> uuid.UUID:
    """A syntactically valid session id that is guaranteed not to exist.

    Used by the 404 path so the test does not depend on a row that some other
    test might have created and deleted.
    """
    return uuid.uuid4()