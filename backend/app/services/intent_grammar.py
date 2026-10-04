"""Single source of truth for SQLRoute intent: routing triggers and templates.

Docs Reference: docs/05 Retrieval Rag Design.md §3, §5.1.

Why this module exists
----------------------
``QuestionRouter.SQL_PATTERNS`` (router.py) and ``SqlRetriever.INTENT_PATTERNS``
(sql_retriever.py) used to be two independent copies of the same grammar, and
they diverged in **both** directions. Both failure modes were measured on the
live deployment, not inferred:

* **Template without a route.** ``Top 5 institutions`` is in the top-institutions
  template list but matched no router pattern, so it routed to VectorRoute. The
  neighbouring ``Top 5 institution`` (no plural ``s``) *did* match, so the two
  spellings of the same question took different routes.
* **Route without a template.** ``Berapa publikasi?`` routed to SQLRoute (the
  literal ``berapa`` pattern matched) while no template covered it, so it fell
  through to Ollama Text-to-SQL. A ``COUNT(*)`` that the deterministic path
  answers in 84-90 ms cost 6.0 s warm and 21.9-41.4 s cold — 10.1 s of which is
  reloading the 4.7 GB qwen2.5-coder weights.

A route with no template is the expensive direction: it converts a bounded
deterministic query into an unbounded LLM call on the synchronous request path.
So coverage is now structural — every intent declares both its router triggers
and its template matcher in one place, and
:func:`coverage_report` exists so a divergence is impossible to reintroduce
without a test noticing.

``YearFilter`` also lives here rather than in router.py, because
``SqlRetriever`` needs it and ``router.py`` must not import a retriever. router.py
re-exports both names so existing imports keep working.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.models.ask import FilterParams


# ---------------------------------------------------------------------------
# Temporal constraints (FR2.3, docs/08 §2.2)
# ---------------------------------------------------------------------------

YearOp = Literal["eq", "gt", "gte", "lt", "lte", "between"]


class YearFilter(BaseModel):
    """Typed year constraint with allowlisted operator (FR2.3, docs/08 §2.2).

    Operators are enumerated as Literal so raw strings can never be
    concatenated into SQL — :func:`year_predicate` maps each op to a bound-param
    predicate (=, >, >=, <, <=, BETWEEN).

    Every operator is enforced. Previously only ``eq`` had a SQL implementation:
    ``between 2021 and 2023`` was parsed correctly by the router and then
    re-parsed by a second, independent regex inside SqlRetriever that collapsed
    any year mention to ``p.year = <first year>`` — a silently wrong answer
    (21 publications for 2021 instead of 2 for the whole window).
    """

    model_config = ConfigDict(frozen=True)

    op: YearOp
    year: int | None = Field(None, ge=1900, le=2026)
    year_from: int | None = Field(None, ge=1900, le=2026)
    year_to: int | None = Field(None, ge=1900, le=2026)

    @model_validator(mode="after")
    def _check_op_fields(self):
        if self.op == "eq" and self.year is None:
            raise ValueError("YearFilter op='eq' requires year")
        if self.op in ("gt", "gte", "lt", "lte") and self.year is None:
            raise ValueError(f"YearFilter op={self.op!r} requires year")
        if self.op == "between" and (self.year_from is None or self.year_to is None):
            raise ValueError("YearFilter op='between' requires year_from and year_to")
        return self


def build_year_filter(
    question: str,
    filters: FilterParams | None = None,
) -> YearFilter | None:
    """Derive a typed year constraint: explicit filters win, free text second.

    Free-text coverage (ID/EN, deterministic):
    - between: "antara 2020 dan 2023", "2020-2023", "2020 sampai 2023",
      "between 2020 and 2023", "dari 2020 hingga 2023"
    - gte: "setelah 2020", "sejak 2020", "after/since 2020", "> 2020"
    - lte: "sebelum 2020", "before 2020", "< 2020"
    - eq: bare year "tahun 2023", "in 2023"
    """
    if filters is not None:
        if filters.year is not None:
            return YearFilter(op="eq", year=filters.year)
        if filters.year_from is not None and filters.year_to is not None:
            return YearFilter(
                op="between",
                year_from=filters.year_from,
                year_to=filters.year_to,
            )
        if filters.year_from is not None:
            return YearFilter(
                op="gte", year=filters.year_from, year_from=filters.year_from
            )
        if filters.year_to is not None:
            return YearFilter(
                op="lte", year=filters.year_to, year_to=filters.year_to
            )

    ql = question.strip().lower()

    between_match = re.search(
        r"\b(?:antara\s+)?(19\d\d|20\d\d)\s*(?:-|–|—|sampai|hingga|to|dan|s/d)\s*(19\d\d|20\d\d)\b"
        r"|\b(?:between|dari)\s+(19\d\d|20\d\d)\s+(?:and|dan|hingga|sampai)\s+(19\d\d|20\d\d)\b",
        ql,
    )
    if between_match:
        years = [int(g) for g in between_match.groups() if g is not None]
        if len(years) >= 2 and 1900 <= years[0] <= 2026 and 1900 <= years[1] <= 2026:
            lo, hi = (years[0], years[1]) if years[0] <= years[1] else (years[1], years[0])
            return YearFilter(op="between", year_from=lo, year_to=hi)

    after_match = re.search(
        r"\b(?:setelah(?:\s+tahun)?|sejak|after|since)\s+(?:tahun\s+)?(19\d\d|20\d\d)\b"
        r"|\b>\s*(19\d\d|20\d\d)\b",
        ql,
    )
    if after_match:
        year = next(int(g) for g in after_match.groups() if g is not None)
        if 1900 <= year <= 2026:
            return YearFilter(op="gte", year=year, year_from=year)

    before_match = re.search(
        r"\b(?:sebelum(?:\s+tahun)?|before)\s+(?:tahun\s+)?(19\d\d|20\d\d)\b"
        r"|\b<\s*(19\d\d|20\d\d)\b",
        ql,
    )
    if before_match:
        year = next(int(g) for g in before_match.groups() if g is not None)
        if 1900 <= year <= 2026:
            return YearFilter(op="lte", year=year, year_to=year)

    year_match = re.search(r"\b(19\d\d|20\d\d)\b", ql)
    if year_match:
        year = int(year_match.group(1))
        if 1900 <= year <= 2026:
            return YearFilter(op="eq", year=year)

    return None


def year_predicate(
    alias: str,
    year_filter: YearFilter | None,
    placeholder: Callable[[object], str],
) -> str | None:
    """Render ``year_filter`` as a bound-parameter SQL predicate on ``alias``.

    ``placeholder`` appends a value to the bound-parameter list and returns its
    ``$n`` marker, so a year can never be string-concatenated into the query.

    Returns ``None`` when there is no constraint, so callers can ``extend`` a
    where-clause list without a branch.
    """
    if year_filter is None:
        return None

    if year_filter.op == "eq":
        return f"{alias}.year = {placeholder(year_filter.year)}"
    if year_filter.op == "gt":
        return f"{alias}.year > {placeholder(year_filter.year)}"
    if year_filter.op == "gte":
        return f"{alias}.year >= {placeholder(year_filter.year)}"
    if year_filter.op == "lt":
        return f"{alias}.year < {placeholder(year_filter.year)}"
    if year_filter.op == "lte":
        return f"{alias}.year <= {placeholder(year_filter.year)}"
    if year_filter.op == "between":
        lo = placeholder(year_filter.year_from)
        hi = placeholder(year_filter.year_to)
        return f"{alias}.year BETWEEN {lo} AND {hi}"

    # Unreachable: YearOp is a closed Literal enforced by the model validator.
    raise ValueError(f"unreachable year operator {year_filter.op!r}")


# ---------------------------------------------------------------------------
# Intent registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentSpec:
    """One deterministic SQL intent, with its router triggers and template match.

    Attributes:
        name: canonical intent id, also the SqlRetrieval debug label.
        template_pattern: regex the deterministic generator uses to recognise
            the intent in free text (ID + EN).
        template_literals: verbatim substrings retained as a second, independent
            check so already-covered wording can never regress when the regex is
            extended. Kept from the original INTENT_PATTERNS table verbatim.
        route_patterns: regexes that make :class:`QuestionRouter` choose
            SQLRoute for this intent. An intent with a template but no route
            pattern is a bug — it means the template is unreachable.
    """

    name: str
    template_pattern: re.Pattern[str]
    template_literals: tuple[str, ...]
    route_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)

    def matches(self, question: str) -> bool:
        """True when ``question`` expresses this intent."""
        q = question.strip().lower()
        return bool(self.template_pattern.search(q)) or any(
            term in q for term in self.template_literals
        )


_I = re.IGNORECASE

#: Shared "most cited" fragment. Used as BOTH the most_cited template and its
#: route trigger, so the two can never disagree about plural forms again.
_MOST_CITED = re.compile(
    r"\b(?:most\s+cit(?:ed|ation)s?|highest\s+cit(?:ed|ation)s?|"
    r"paling\s+banyak\s+disitasi|sitasi\s+terbanyak|"
    r"top\s+cit(?:ed|ation)s?)\b",
    _I,
)

# Evaluation order is load-bearing: the first match wins, so a more specific
# intent must precede a more general one. This preserves the original
# SqlRetriever.generate_deterministic_sql chain order while adding the two
# intents that previously had no template at all.
INTENT_SPECS: tuple[IntentSpec, ...] = (
    IntentSpec(
        name="top_authors",
        template_pattern=re.compile(
            r"\b(?:"
            # bare ranking noun, with or without a numeric rank prefix:
            # "top 5 researchers", "top authors", "5 teratas"
            r"top\s+\d*\s*(?:authors?|researchers?|penulis|peneliti)\b|"
            r"most\s+pro(?:ductive|lific)\s+authors?|"
            r"authors?\s+(?:most\s+)?pro(?:ductive|lific)|"
            r"top\s+authors?|"
            r"(?:authors?|researchers?|penulis|peneliti)\s+"
            r"(?:paling\s+produktif|ter(?:atas|produktif))|"
            r"penulis\s+paling\s+produktif|"
            r"most\s+active\s+authors?|"
            # "Which author has the highest publication count?" — the noun is
            # mandatory so this cannot swallow "which author has the most
            # citations?" (that is most_cited, checked after this spec).
            r"which\s+authors?\s+(?:has|have|had)\s+the\s+"
            r"(?:highest|most|greatest|top)\s+(?:publications?|papers?|articles?)\b|"
            r"(?:highest|most|greatest)\s+publication\s+count"
            r")",
            _I,
        ),
        template_literals=(
            "penulis paling produktif",
            "most productive author",
            "top author",
            "penulis teratas",
            "author paling produktif",
            "most prolific author",
        ),
        route_patterns=(
            re.compile(r"\b(top\s*\d+|\d+\s*teratas|peringkat|ranking)\b", _I),
            re.compile(r"\b(most\s*productive|prolific\s*authors?|most\s*active)\b", _I),
            re.compile(
                r"\b(penulis|author)\s+paling\s+produktif\b"
                r"|\bpenulis\s+teratas\b"
                r"|\b(?:authors?|researchers?|penulis|peneliti)\s+"
                r"(?:teratas|terproduktif)\b"
                r"|\btop\s+\d*\s*(?:authors?|researchers?|penulis|peneliti)\b",
                _I,
            ),
            re.compile(
                r"\bwhich\s+authors?\s+(?:has|have|had)\s+the\s+"
                r"(?:highest|most|greatest|top)\s+(?:publications?|papers?|articles?)\b",
                _I,
            ),
            re.compile(
                r"\b(?:highest|most|greatest)\s+publication\s+count\b", _I
            ),
        ),
    ),
    IntentSpec(
        name="most_cited",
        # "citation(s)" and "cit(ation)s" both need the plural tolerated:
        # without it "most citations" failed the trailing \b.
        template_pattern=_MOST_CITED,
        template_literals=(
            "sitasi terbanyak",
            "most cited",
            "highest citation",
            "paling banyak disitasi",
        ),
        route_patterns=(
            # NOTE: these must stay plural-tolerant in lockstep with
            # template_pattern above. They were not: the template matched
            # "most citations" while the route only matched "most cited", so
            # "Which author has the most citations?" got a correct intent and
            # still fell through to VectorRoute. That is the same
            # route/template divergence this module exists to prevent, so the
            # shared `_MOST_CITED` fragment below is used for both.
            _MOST_CITED,
            re.compile(
                r"\b(siapa|who)\b.*\b(paling\s+banyak\s+disitasi|"
                r"most\s+cit(?:ed|ation)s?|highest\s+cit(?:ed|ation)s?)\b",
                _I,
            ),
        ),
    ),
    IntentSpec(
        name="avg_citation_per_publication",
        # Deliberately narrow: only the "average per publication" sense.
        #
        # An earlier version of this pattern accepted a bare "rata-rata sitasi",
        # which also captured "Berapa rata-rata sitasi per tahun?" — a request
        # for a PER-YEAR breakdown. The template returned a single overall AVG,
        # so it would have answered a different question than the one asked while
        # looking like a clean deterministic hit. Grouped aggregates
        # ("per tahun" / "per year" / "grouped") must keep going to Text-to-SQL,
        # which can emit GROUP BY year; note AGGREGATE_INTENT_RE already treats
        # those phrasings as aggregates for the AST gate.
        template_pattern=re.compile(
            r"\b(?:rata-?\s?rata|rerata|average|mean)\s+"
            r"(?:sitasi|citation|citations)\s+"
            r"(?:per\s+|rata-?\s?rata\s+)?"
            r"(?:publikasi|paper|papers|artikel|publication|publications)\b"
            r"|\b(?:average|mean)\s+citation(?:s)?\s+"
            r"(?:count\s+)?per\s+publication\b",
            _I,
        ),
        template_literals=(
            "rata-rata sitasi per publikasi",
            "rata rata sitasi per publikasi",
            "rerata sitasi per publikasi",
            "average citation per publication",
            "average citations per publication",
            "average citation count per publication",
        ),
        route_patterns=(
            # Routing stays broader than templating: any average-of-citations
            # phrasing is relational and belongs on SQLRoute, whether or not a
            # bounded template can serve it.
            re.compile(
                r"\b(rata-?\s?rata|rerata|average|mean)\b.*"
                r"\b(sitasi|citation|citations)\b",
                _I,
            ),
        ),
    ),
    IntentSpec(
        name="count_publications",
        template_pattern=re.compile(
            # FIX: was `berapakah?`, where the `?` binds only to `h`, so the
            # pattern spelled "berapah" or "berapakah" and rejected the plain
            # "Berapa publikasi?" that the router's literal `berapa` pattern
            # happily routed to SQLRoute. `(?:kah)?` is the intended grouping.
            r"\bberapa(?:kah)?\s+(?:jumlah\s+|total\s+|banyak\s+|seluruh\s+)?"
            r"(?:publikasi|paper|papers|artikel|artikelnya|jurnal|karya|publication|publications)\b"
            r"|\b(?:jumlah|total)\s+(?:publikasi|paper|papers|artikel|jurnal|karya)\b"
            r"|\b(?:total|jumlah)\s+(?:number\s+of\s+)?"
            r"(?:publikasi|paper|papers|artikel|publication|publications)\b"
            r"|\b(?:how\s+many|count\s+of|number\s+of)\s+"
            r"(?:publications?|papers?|articles?|studies)\b"
            r"|\btotal\s+(?:number\s+of\s+)?(?:publications?|papers?|articles?)\b"
            r"|\bseberapa\s+banyak\s+(?:publikasi|paper|artikel)\b"
            r"|\bcount\s+(?:of\s+)?(?:publications?|papers?|articles?)\b",
            _I,
        ),
        template_literals=(
            "berapa jumlah publikasi",
            "total publikasi",
            "how many publications",
            "count of publications",
            "total paper",
            "jumlah paper",
        ),
        route_patterns=(
            re.compile(
                r"\b(berapa|jumlah|total|hitung)\b.*"
                r"\b(publikasi|paper|artikel|sitasi|author|penulis|institusi|"
                r"dana|grant)\b",
                _I,
            ),
            re.compile(
                r"\b(how\s*many|count\s*of|total\s*(number\s*of)?\s*"
                r"(publications|papers|articles|citations|authors|grants?))\b",
                _I,
            ),
        ),
    ),
    IntentSpec(
        name="top_institutions",
        template_pattern=re.compile(
            # FIX: allow a numeric rank between "top" and the noun. The original
            # had no `\d*` here, so "Top 5 institutions" matched no template and
            # reached Ollama while "Top institutions" answered in 88 ms.
            r"\btop\s*\d*\s*(?:institutions?|universit(?:y|ies)|institusi|universitas)\b"
            r"|\b(?:institutions?|universit(?:y|ies)|institusi|universitas)\s+"
            r"(?:teratas|paling\s+produktif|most\s+productive|top)\b"
            r"|\b(?:most\s+productive|top)\s+(?:institutions?|universities)\b"
            r"|\b(?:top\s+institutions?|top\s+universit(?:y|ies))\b",
            _I,
        ),
        template_literals=(
            "top institusi",
            "institusi teratas",
            "top institutions",
            "most productive institution",
            "institusi paling produktif",
        ),
        route_patterns=(
            re.compile(
                r"\b(institusi|institutions?|universitas|universities)\s+"
                r"(?:teratas|paling\s+produktif|top)\b",
                _I,
            ),
            re.compile(
                r"\btop\s*\d*\s*(?:institutions?|universities|institusi|universitas)\b",
                _I,
            ),
            re.compile(
                r"\b(most\s+productive|top)\s+(?:institutions?|universities)\b", _I
            ),
        ),
    ),
    IntentSpec(
        name="funding_aggregation",
        template_pattern=re.compile(
            r"\b(?:berapa|jumlah|total|hitung)\b.*\b(grant|dana|hibah|funding)\b"
            r"|\b(grant|dana|hibah)\b.*\b(berapa|jumlah|total|hitung)\b"
            r"|\bhow\s*many\s+(?:grants?|funding)\b"
            r"|\btotal\s+(?:grant|funding)\b",
            _I,
        ),
        template_literals=(
            "berapa grant",
            "jumlah grant",
            "total dana",
            "how many grants",
        ),
        route_patterns=(
            re.compile(
                r"\b(berapa|jumlah|total|hitung)\b.*\b(grant|dana|hibah|funding)\b",
                _I,
            ),
            re.compile(
                r"\b(grant|dana|hibah|funding)\b.*\b(berapa|jumlah|total|hitung)\b",
                _I,
            ),
            re.compile(r"\bhow\s*many\s+(?:grants?|funding)\b", _I),
        ),
    ),
    IntentSpec(
        name="list_publications",
        template_pattern=re.compile(
            r"\b(?:daftar|list|show|tampilkan|sebutkan)\s+"
            r"(?:semua\s+|all\s+)?(?:publikasi|paper|papers|artikel|publication|publications)\b"
            r"|\b(?:publikasi|papers?|articles?)\s+(?:pada\s+tahun|in\s*year|published\s*in)\s*\d{4}\b"
            r"|\b(?:paper|artikel)\s+(?:in|on)\s+(?:year\s+)?\d{4}\b"
            # FIX: "List publications in 2025" — the year-qualified alternative
            # above only accepted "in year 2025", so the bare "in 2025" spelling
            # fell through to VectorRoute.
            r"|\b(?:publikasi|papers?|articles?)\s+(?:in|on)\s+(?:year\s+)?\d{4}\b",
            _I,
        ),
        template_literals=(
            "daftar publikasi",
            "list publications",
            "show publications",
            "tampilkan publikasi",
            "artikel pada tahun",
            "paper in year",
        ),
        route_patterns=(
            re.compile(
                r"\b(daftar|tampilkan|sebutkan)\b.*"
                r"\b(publikasi|paper|artikel|publication|publications)\b",
                _I,
            ),
            re.compile(
                # Year-qualified form. Two fixes here, both found by
                # parity_violations() rather than by inspection:
                #  * the year is OPTIONAL — the template accepts "artikel pada
                #    tahun" / "paper in year" with no digits, so a mandatory
                #    \d{4} here made those templates unreachable.
                #  * `artikel` must be listed literally. `articles?` matches
                #    "article", not the Indonesian "artikel", so the template
                #    matched a phrasing no route could reach.
                r"\b(publikasi|papers?|articles?|artikel)\s*"
                r"(tahun|pada\s*tahun|in\s*year|in\s*\d{4}|published\s*in|on\s*\d{4})\b"
                r"|\b(?:publikasi|papers?|articles?|artikel)\s+(?:in|on)\s+\d{4}\b",
                _I,
            ),
            re.compile(
                r"\b(list|show)\s+(?:all\s+)?(publications?|papers?|articles?)\b", _I
            ),
        ),
    ),
)

INTENT_BY_NAME: dict[str, IntentSpec] = {spec.name: spec for spec in INTENT_SPECS}

#: SQLRoute triggers that are *not* tied to a deterministic template. Metadata
#: filter questions (funding agency, document type, open access) are genuinely
#: relational but have no bounded template, so they route to SQLRoute and are
#: answered by the AST-guarded Text-to-SQL path. Kept here so the router has a
#: single import instead of a second local pattern table.
EXTRA_SQL_ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(funding\s*agency|sumber\s*dana|hibah|grant\s*number)\b", _I
    ),
    re.compile(
        r"\b(open\s*access|document\s*type|tipe\s*dokumen|bahasa\s*dokumen)\b", _I
    ),
)

#: Every SQLRoute trigger, in intent-evaluation order then extras. QuestionRouter
#: iterates this single list, so adding an intent automatically wires its routing.
SQL_ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    p for spec in INTENT_SPECS for p in spec.route_patterns
) + EXTRA_SQL_ROUTE_PATTERNS


#: Keywords signalling a computed-number question (FR3.5). Pure ranking
#: phrasing ("top N ... terbanyak") is excluded: ranked lists over stored
#: columns need no aggregate function.
AGGREGATE_INTENT_RE = re.compile(
    r"\b(berapa|jumlah|total|hitung|count|how\s+many|rata|rerata|average|mean|"
    r"distribusi|distribution|per\s*tahun|grouped)\b",
    _I,
)


def match_intent(question: str) -> str | None:
    """Return the canonical name of the first intent ``question`` expresses.

    Order follows :data:`INTENT_SPECS`, which is ordered most-specific first.
    Returns ``None`` when no template covers the question — the signal that
    SqlRetriever must fall back to Text-to-SQL.
    """
    for spec in INTENT_SPECS:
        if spec.matches(question):
            return spec.name
    return None


def is_sql_route_intent(question: str) -> bool:
    """True when at least one SQLRoute trigger fires for ``question``."""
    return any(p.search(question.strip()) for p in SQL_ROUTE_PATTERNS)


def coverage_report() -> dict[str, object]:
    """Audit that every intent is reachable and every trigger maps to an intent.

    Raises:
        AssertionError: an intent declares no route pattern (its template would
            be unreachable), or two intents share a name. Callers turn this into
            a test so the two grammars cannot drift apart again.
    """
    names = [spec.name for spec in INTENT_SPECS]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise AssertionError(f"duplicate intent names: {sorted(dupes)}")

    unreachable = [spec.name for spec in INTENT_SPECS if not spec.route_patterns]
    if unreachable:
        raise AssertionError(
            "intents without route_patterns have unreachable templates: "
            f"{unreachable}"
        )

    return {
        "intents": names,
        "route_pattern_count": len(SQL_ROUTE_PATTERNS),
        "extra_route_pattern_count": len(EXTRA_SQL_ROUTE_PATTERNS),
        "aggregate_intent": AGGREGATE_INTENT_RE.pattern,
    }


#: Canonical phrasings per intent, ID + EN. A phrase that resolves to an intent
#: MUST route to SQLRoute; that implication is the invariant the whole module
#: exists to hold, and it is what regressed repeatedly before the grammars were
#: merged. Keyed by intent so adding a phrase cannot be silently forgotten.
#:
#: Each entry is (question, expected_intent). Phrasings with no bounded template
#: belong in INTENT_PARITY_PHRASES with an intent, never as a bare question.
INTENT_PARITY_PHRASES: tuple[tuple[str, str], ...] = (
    ("Berapa publikasi?", "count_publications"),
    ("Berapa jumlah publikasi tahun 2025?", "count_publications"),
    ("total publikasi", "count_publications"),
    ("total paper", "count_publications"),
    ("jumlah paper", "count_publications"),
    ("how many publications", "count_publications"),
    ("count of publications", "count_publications"),
    ("Berapa grant yang diberikan?", "funding_aggregation"),
    ("jumlah grant", "funding_aggregation"),
    ("how many grants", "funding_aggregation"),
    ("Top 5 institutions", "top_institutions"),
    ("Top 5 institution", "top_institutions"),
    ("top institutions", "top_institutions"),
    ("institutions teratas", "top_institutions"),
    ("top institusi", "top_institutions"),
    ("most productive institution", "top_institutions"),
    ("penulis paling produktif", "top_authors"),
    ("most productive author", "top_authors"),
    ("top author", "top_authors"),
    ("penulis teratas", "top_authors"),
    ("author paling produktif", "top_authors"),
    ("most prolific author", "top_authors"),
    ("top 5 researchers by publication count", "top_authors"),
    ("Which author has the highest publication count?", "top_authors"),
    ("sitasi terbanyak", "most_cited"),
    ("most cited", "most_cited"),
    ("most citations", "most_cited"),
    ("highest citation", "most_cited"),
    ("paling banyak disitasi", "most_cited"),
    ("Which author has the most citations?", "most_cited"),
    ("average citation count per publication", "avg_citation_per_publication"),
    ("Berapa rata-rata sitasi per publikasi?", "avg_citation_per_publication"),
    ("daftar publikasi", "list_publications"),
    ("list publications", "list_publications"),
    ("show publications", "list_publications"),
    ("tampilkan publikasi", "list_publications"),
    ("artikel pada tahun", "list_publications"),
    ("paper in year", "list_publications"),
    ("List publications in 2025", "list_publications"),
)


def parity_violations() -> list[str]:
    """Phrasings whose template coverage and route selection disagree.

    A violation is either direction of the original defect:
      * ``intent None`` + SQLRoute  → the route exists with no bounded template,
        so a fast query is converted into a 6-21 s Ollama Text-to-SQL call.
      * ``intent X`` + not SQLRoute → a template exists but is unreachable.

    Returns a human-readable list; empty means the grammars agree.
    """
    problems: list[str] = []
    for question, expected_intent in INTENT_PARITY_PHRASES:
        actual_intent = match_intent(question)
        if actual_intent != expected_intent:
            problems.append(
                f"{question!r}: expected intent {expected_intent!r}, "
                f"got {actual_intent!r}"
            )
            continue
        if not is_sql_route_intent(question):
            problems.append(
                f"{question!r}: intent {expected_intent!r} matched but no SQLRoute "
                "trigger fired, so the template is unreachable"
            )
    return problems