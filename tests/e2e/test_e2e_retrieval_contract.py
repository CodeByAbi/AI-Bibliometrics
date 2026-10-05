"""Generic end-to-end retrieval contract.

PR #15 closed with two retrieval defects that the suite could not see: a pipeline
trail that reported four completed stages during a backend outage, and a stale
container image answering ``status: ok`` with ``evidence_objects[0].value == 0``
for a year holding no rows. Both hid behind tests that assert one hardcoded
scenario each. This module asserts the *contract* instead, and derives every
expectation from the database so it cannot drift into checking a number someone
remembered rather than one the corpus contains.

The contract, stated once:

    supported corpus representation -> status ok + DB-backed evidence
    unsupported representation     -> deterministic not_found, zero evidence
    backend/retrieval failure      -> explicit error, never a success shape

Two rules govern everything below.

1. **No hardcoded facts.** Counts, years, institution spellings and partner names
   are read back from ``public`` through the same read-only pool the app uses. If
   the corpus is replaced or extended, these tests follow it. A test asserting
   ``== 20`` proves only that someone typed 20.

2. **No entity special-casing.** The institution used below is *discovered* from
   the corpus (whichever name has the most collaboration edges), never named.
   Alias/fuzzy entity resolution is an explicitly unimplemented future capability
   (docs/01 PRD), so the unsupported-representation case is asserted generically:
   an entity string that provably matches zero rows must fail closed rather than
   be guessed at.

Route selection runs everywhere (no database needed). The grounding checks need a
real corpus and run under ``E2E_LIVE=1``, following ``test_e2e_12_queries.py``.
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app

pytestmark = [pytest.mark.e2e_live]

USE_LIVE = os.environ.get("E2E_LIVE", "").lower() in ("1", "true", "yes")

#: A name guaranteed to match no institution row, used to prove the gate fails
#: closed. Constructed at runtime rather than written literally, so it can never
#: collide with real corpus data if the corpus is ever replaced.
UNMATCHED_ENTITY = f"Zzqx Institution That Cannot Exist {uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Corpus reader. Every expectation below comes from here.
# ---------------------------------------------------------------------------


class Corpus:
    """Reads expectations out of the live corpus, inside the caller's loop.

    Each method resolves ``get_pool()`` fresh instead of holding a pool. That is
    load-bearing: ``get_pool()`` is loop-aware (it rebuilds when the cached pool
    belongs to another loop), so resolving per call keeps these queries in the
    SAME event loop as the ASGI request they check. An async fixture that
    acquired the pool instead would bind it to the fixture's loop while
    pytest-asyncio hands each test a new one, which surfaces as
    "Event loop is closed" - a failure that looks like a retrieval bug and is not.
    """

    @staticmethod
    async def _pool():
        from backend.app.db.pool import get_pool

        return await get_pool()

    async def reachable(self) -> bool:
        try:
            pool = await self._pool()
            async with pool.acquire() as conn:
                return bool(
                    await conn.fetchval(
                        "SELECT to_regclass('public.publications') IS NOT NULL"
                    )
                )
        except (ValueError, OSError, RuntimeError):
            return False

    async def year_with_rows(self) -> int | None:
        """The year holding the most publications, read from the corpus."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT year FROM publications WHERE year IS NOT NULL"
                " GROUP BY year"
                " ORDER BY COUNT(DISTINCT publication_id) DESC, year DESC LIMIT 1"
            )
        return int(value) if value is not None else None

    async def year_without_rows(self) -> int | None:
        """A publication-free year, or None if the corpus has no such gap."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT MIN(y)::int FROM generate_series(1900, 2100) AS y"
                " WHERE NOT EXISTS (SELECT 1 FROM publications WHERE year = y)"
            )
        return int(value) if value is not None else None

    async def publication_count(self, year: int) -> int:
        """The same COUNT(DISTINCT publication_id) the retriever is specified to use."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(DISTINCT publication_id) FROM publications"
                    " WHERE year = $1",
                    year,
                )
            )

    async def busiest_institution(self) -> tuple[str, int] | None:
        """The corpus-supported spelling of an institution plus its edge count.

        ``institution_collaboration`` stores Scopus institution IDs
        (``INS...``), not names, so the name is joined through
        ``institutions.institution_id``. Nothing here re-types a name: if the
        Scopus export changes the affiliation string, this follows it.
        """
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT i.institution_name, COUNT(*) AS edges"
                " FROM institution_collaboration c"
                " JOIN institutions i ON i.institution_id = c.institution_a"
                " WHERE i.institution_name IS NOT NULL AND i.institution_name <> ''"
                " GROUP BY i.institution_name"
                " ORDER BY edges DESC, i.institution_name LIMIT 1"
            )
        if row is None:
            return None
        return str(row["institution_name"]), int(row["edges"])

    async def partners(self, institution_name: str) -> set[str]:
        """Partner institution names recorded for this institution.

        Edges are stored directionally and keyed by ID, so both directions and
        both joins are needed. Collecting them keeps this from being stricter
        than the data actually is.
        """
        pool = await self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT out.institution_name AS other"
                " FROM institution_collaboration c"
                " JOIN institutions i ON i.institution_id = c.institution_a"
                " JOIN institutions out ON out.institution_id = c.institution_b"
                " WHERE i.institution_name = $1"
                " UNION"
                " SELECT out.institution_name"
                " FROM institution_collaboration c"
                " JOIN institutions i ON i.institution_id = c.institution_b"
                " JOIN institutions out ON out.institution_id = c.institution_a"
                " WHERE i.institution_name = $1",
                institution_name,
            )
        return {str(r["other"]) for r in rows}

    async def publication_exists(self, publication_id: str) -> bool:
        """True when this publication id is really in the corpus."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT EXISTS"
                    " (SELECT 1 FROM publications WHERE publication_id = $1)",
                    publication_id,
                )
            )

    async def ambiguous_institution(self) -> list[str] | None:
        """Distinct names sharing one normalized key, or None if none collide."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            key = await conn.fetchval(
                "SELECT institution_name_normalized FROM institutions"
                " WHERE institution_name_normalized IS NOT NULL"
                " GROUP BY institution_name_normalized HAVING COUNT(*) > 1 LIMIT 1"
            )
            if key is None:
                return None
            rows = await conn.fetch(
                "SELECT DISTINCT institution_name FROM institutions"
                " WHERE institution_name_normalized = $1",
                str(key),
            )
        names = sorted({str(r["institution_name"]) for r in rows})
        return names if len(names) > 1 else None


@pytest.fixture
def corpus() -> Corpus:
    return Corpus()


async def require_live(corpus: Corpus) -> None:
    """Skip unless a live corpus is both requested and actually reachable."""
    if not USE_LIVE:
        pytest.skip("set E2E_LIVE=1 to exercise the real corpus")
    if not await corpus.reachable():
        pytest.skip("no reachable bibliometric database")


async def _ask(client, question: str, **extra):
    resp = await client.post("/api/v1/ask", json={"question": question, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


#: Retriever collaboration claims read
#: ``"Kolaborasi dengan <PARTNER> tercatat sebanyak N publikasi bersama ..."``.
#: Pulling the partner out of the claim keeps the grounding check on structured
#: retriever output instead of on LLM prose.
_CLAIM_PREFIX = "Kolaborasi dengan "
_CLAIM_SUFFIX = " tercatat"


def _partner_from_claim(claim: str) -> str | None:
    """Extract the partner institution name from a graph evidence claim."""
    start = claim.find(_CLAIM_PREFIX)
    if start == -1:
        return None
    rest = claim[start + len(_CLAIM_PREFIX) :]
    end = rest.find(_CLAIM_SUFFIX)
    return (rest[:end] if end != -1 else rest).strip() or None


# ---------------------------------------------------------------------------
# Contract 1: supported representation -> ok + DB-backed evidence
# ---------------------------------------------------------------------------


class TestSupportedRepresentationIsGrounded:
    async def test_aggregation_equals_a_direct_database_count(self, corpus):
        """The number in the answer must equal what the database actually holds.

        The anti-hallucination core. Expected value comes from
        ``COUNT(DISTINCT publication_id)`` over ``publications``, so an invented
        or wrong figure fails here.
        """
        await require_live(corpus)
        year = await corpus.year_with_rows()
        if year is None:
            pytest.skip("corpus has no publication years")
        expected = await corpus.publication_count(year)
        assert expected > 0

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = await _ask(client, f"Berapa publikasi pada tahun {year}?")

        assert data["route"] == "SQLRoute"
        assert data["status"] == "ok"
        assert data["evidence_objects"], "an ok aggregate must carry evidence"
        ev = data["evidence_objects"][0]
        assert ev["metric"] == "publication_count"
        assert ev["value"] == expected
        assert str(expected) in data["answer"], (
            f"answer must state the real count {expected}, got {data['answer']!r}"
        )

    async def test_collaboration_answer_cites_only_real_partners(self, corpus):
        """Every partner a graph answer names must exist in the edge table.

        The institution is whatever the corpus stores and the expected partners
        are read back from ``institution_collaboration``.

        The assertion reads the *evidence claims*, not the synthesised prose.
        Claims come from the retriever and are deterministic, while the prose is
        written by the LLM and may abbreviate or rephrase a name - asserting on
        it would make this a test of Ollama's wording.
        """
        await require_live(corpus)
        institution = await corpus.busiest_institution()
        if institution is None:
            pytest.skip("corpus has no institution_collaboration edges")

        name, edge_count = institution
        partners = await corpus.partners(name)
        assert partners, "fixture requires an institution that has partners"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = await _ask(
                client,
                "Institusi mana yang collaborate?",
                filters={"institution_name": name},
            )

        assert data["route"] == "GraphRoute"
        assert data["status"] == "ok"
        assert data["sources"], "a graph answer must be citable"

        claimed = {
            partner
            for ev in data["evidence_objects"]
            if (partner := _partner_from_claim(ev.get("claim", "")))
        }
        assert claimed, "graph evidence must state who collaborated"
        # Nothing invented: every named partner is one the edge table records.
        assert claimed <= partners, (
            f"answer names partners absent from the edge table: "
            f"{sorted(claimed - partners)}"
        )
        # Nothing hidden either: every recorded partner is reported.
        assert claimed == partners, (
            f"edge table records {len(partners)} partners,"
            f" answer reported {len(claimed)}"
        )
        assert len(data["evidence_objects"]) <= edge_count

        # Every cited publication must really exist in the corpus.
        for ev in data["evidence_objects"]:
            for src in ev.get("sources") or []:
                pub_id = src.get("publication_id")
                assert pub_id, "evidence must cite a publication"
                assert await corpus.publication_exists(pub_id), (
                    f"evidence cites publication {pub_id}, which is not in the corpus"
                )


# ---------------------------------------------------------------------------
# Contract 2: unsupported representation -> deterministic not_found
# ---------------------------------------------------------------------------


class TestUnsupportedRepresentationFailsClosed:
    async def test_entity_matching_no_rows_returns_not_found_and_no_evidence(
        self, corpus
    ):
        """An unresolvable entity fails closed rather than being guessed at.

        Alias resolution is a documented future capability. Until it exists, an
        unknown entity must yield ``not_found`` with zero evidence and no
        synthesised claim, not a confident answer about a nearby name.
        """
        await require_live(corpus)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = await _ask(
                client,
                "Institusi mana yang collaborate?",
                filters={"institution_name": UNMATCHED_ENTITY},
                developer_mode=True,
            )

        assert data["status"] == "not_found"
        assert data.get("evidence_objects", []) == []
        assert data.get("sources", []) == []
        # The zero-evidence class keeps "nothing matched" auditable and distinct
        # from "something broke".
        assert data["debug"]["zero_evidence_class"] == "entity_not_found"

    async def test_the_failure_is_deterministic_across_repeats(self, corpus):
        """The same unsupported entity must fail identically every time.

        A probabilistic gate here would let an unsupported spelling intermittently
        produce an answer, which is the precise hallucination risk the contract
        exists to prevent.
        """
        await require_live(corpus)

        observed = set()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for _ in range(3):
                data = await _ask(
                    client,
                    "Institusi mana yang collaborate?",
                    filters={"institution_name": UNMATCHED_ENTITY},
                )
                observed.add((data["status"], len(data.get("evidence_objects") or [])))

        assert observed == {("not_found", 0)}

    async def test_a_year_with_no_rows_is_not_reported_as_a_zero_count(self, corpus):
        """Zero matches must not be dressed up as an answer containing the number 0.

        This is the exact shape the stale image served: ``status: ok`` with
        ``evidence_objects[0].value == 0`` for a year holding no rows. A reader
        cannot tell that apart from a real finding, so the contract requires the
        honest ``not_found``.
        """
        await require_live(corpus)
        empty_year = await corpus.year_without_rows()
        if empty_year is None:
            pytest.skip("corpus has no publication-free year to test against")
        # Guard the fixture itself: this year must genuinely hold nothing.
        assert await corpus.publication_count(empty_year) == 0

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = await _ask(
                client,
                f"Berapa publikasi pada tahun {empty_year}?",
                developer_mode=True,
            )

        assert data["status"] == "not_found"
        assert data.get("evidence_objects", []) == []
        assert data["debug"]["zero_evidence_class"] == "zero_aggregate"
        # The forbidden shape, stated positively.
        assert not any(
            ev.get("metric") == "publication_count" and ev.get("value") == 0
            for ev in data.get("evidence_objects") or []
        )


# ---------------------------------------------------------------------------
# Contract 3: a failure is never shaped like a success
# ---------------------------------------------------------------------------


class TestFailureIsNotSuccess:
    async def test_a_stalled_generator_yields_an_error_envelope(self, corpus):
        """A retriever that raises must surface an error, not an empty answer.

        ``ok`` with zero evidence and ``not_found`` are both legitimate shapes; a
        raised exception is neither. Collapsing them would hide the outage behind
        a plausible-looking answer.
        """
        await require_live(corpus)

        from backend.app.core.errors import LLMTimeoutError
        from backend.app.services.retrievers.sql_retriever import SqlRetriever

        async def stall(cls, *a, **kw):
            raise LLMTimeoutError()

        original = SqlRetriever.generate_llm_sql
        SqlRetriever.generate_llm_sql = classmethod(stall)  # type: ignore[method-assign]
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/v1/ask",
                    json={"question": "Berapa rata-rata sitasi per tahun?"},
                )
        finally:
            SqlRetriever.generate_llm_sql = original  # type: ignore[method-assign]

        assert resp.status_code != 200
        body = resp.json()
        for forbidden in (
            "evidence_objects",
            "sources",
            "answer",
            "unverified_citations",
        ):
            assert forbidden not in body, (
                f"a failed request must not carry {forbidden!r};"
                " that is a success shape"
            )
        assert body["error"]["status_code"] == resp.status_code

    async def test_a_failure_after_a_success_returns_no_stale_evidence(self, corpus):
        """A later failure must not return the previous answer's evidence."""
        await require_live(corpus)
        year = await corpus.year_with_rows()
        if year is None:
            pytest.skip("corpus has no publication years")

        from backend.app.services.retrievers.sql_retriever import SqlRetriever

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            good = await _ask(client, f"Berapa publikasi pada tahun {year}?")
            assert good["status"] == "ok"
            assert good["evidence_objects"]

            async def stall(cls, *a, **kw):
                raise RuntimeError("retriever exploded")

            # `raise_app_exceptions=False` is required here: this is an UNMAPPED
            # error, so the app turns it into a 500. With the default transport
            # setting httpx re-raises the exception and the test sees a crash
            # instead of the envelope a real HTTP client would receive.
            original = SqlRetriever.generate_llm_sql
            SqlRetriever.generate_llm_sql = classmethod(stall)  # type: ignore[method-assign]
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as failing:
                    resp = await failing.post(
                        "/api/v1/ask",
                        json={"question": "Berapa rata-rata sitasi per tahun?"},
                    )
            finally:
                SqlRetriever.generate_llm_sql = original  # type: ignore[method-assign]

        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["error_type"] == "internal_error"
        # The previous answer's evidence must not ride along on the error.
        for key in ("evidence_objects", "sources", "answer"):
            assert key not in body, f"a 500 must not carry {key!r}"


# ---------------------------------------------------------------------------
# Route selection: no database needed, so this runs everywhere
# ---------------------------------------------------------------------------


class TestRouteSelection:
    """Each intent must land on the route that can answer it.

    A question silently drifting to VectorRoute would return plausible prose with
    no aggregate behind it.
    """

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Berapa total publikasi pada tahun 2025?", "SQLRoute"),
            ("Siapa 5 penulis paling produktif tahun 2025?", "SQLRoute"),
            ("Berapa rata-rata sitasi per tahun?", "SQLRoute"),
            ("Who collaborates with Lambung Mangkurat University?", "GraphRoute"),
            ("Institusi mana yang collaborate dengan Universitas X?", "GraphRoute"),
            ("Papers about mesenchymal stem cell differentiation?", "VectorRoute"),
            ("Who are the experts in mesenchymal stem cell research?", "HybridRoute"),
            ("What are the emerging topics in this field?", "HybridRoute"),
        ],
    )
    def test_intent_reaches_the_expected_route(self, question: str, expected: str):
        from backend.app.services.router import QuestionRouter

        decision = QuestionRouter.classify_route(question)
        assert decision.route == expected, (
            f"{question!r} routed to {decision.route}, expected {expected} "
            f"(reasoning: {decision.reasoning})"
        )

    def test_every_route_is_reachable(self):
        """Guard against a router that only ever emits one or two routes."""
        from backend.app.services.router import QuestionRouter

        routes = {
            QuestionRouter.classify_route(q).route
            for q in (
                "Berapa total publikasi pada tahun 2025?",
                "Who collaborates with Lambung Mangkurat University?",
                "Papers about mesenchymal stem cell differentiation?",
                "Who are the experts in mesenchymal stem cell research?",
            )
        }
        assert routes == {"SQLRoute", "GraphRoute", "VectorRoute", "HybridRoute"}


# ---------------------------------------------------------------------------
# Documented limitation, encoded so it cannot be forgotten
# ---------------------------------------------------------------------------


class TestKnownLimitation:
    def test_alias_resolution_is_absent_by_design(self):
        """Entity alias/fuzzy matching is NOT implemented. Lock that in.

        If a future change adds it, this test fails and forces the false-positive
        risk ("Universitas X" -> "X University") to be decided deliberately,
        rather than arriving as a silent behaviour change that starts answering
        questions nobody verified.
        """
        from backend.app.services.router import EntityResolutionGate

        assert not hasattr(EntityResolutionGate, "resolve_alias")
        assert not hasattr(EntityResolutionGate, "fuzzy_match")

    async def test_an_ambiguous_entity_is_asked_about_or_narrowed_visibly(self, corpus):
        """Ambiguity must never resolve by invisible guesswork.

        Two acceptable outcomes and nothing else:
          * ``needs_clarification`` - the user is asked; or
          * ``ok`` *with* a recorded ``entity_narrowing`` - the gate picked the
            highest-publication variant and said so in the debug envelope.

        An ``ok`` that silently substituted one of several candidates is the
        failure mode: the answer is about a department while the question asked
        about a university. The ambiguous entity is discovered from the corpus,
        so this depends on no particular institution name.
        """
        await require_live(corpus)
        names = await corpus.ambiguous_institution()
        if names is None:
            pytest.skip("corpus has no ambiguous institution to test")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            data = await _ask(
                client,
                "Who collaborates with this institution?",
                filters={"institution_name": names[0]},
                developer_mode=True,
            )

        if data["status"] == "needs_clarification":
            assert data.get("candidates"), "a clarification must offer candidates"
            assert data.get("clarification_message")
        else:
            assert data["status"] == "ok"
            assert (data.get("debug") or {}).get("entity_narrowing") is not None, (
                "an answer that silently picked one of several matching "
                "institutions is a grounding defect"
            )
