"""Question routing and entity resolution gate services.

Docs Reference: docs/05 Retrieval Rag Design.md §3, docs/10 Implementation Plan.md §1 (Task 4).
"""

from __future__ import annotations

import asyncio
import re
import string
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.models.ask import CandidateItem, FilterParams
from backend.app.services.retrievers.sql_security import escape_like_pattern

STATEMENT_TIMEOUT_S = 10.0


RouteType = Literal["SQLRoute", "VectorRoute", "GraphRoute", "HybridRoute"]


class RouteDecision(BaseModel):
    """Result of question intent routing classification."""

    model_config = ConfigDict(frozen=True)

    route: RouteType
    reasoning: str
    answered_via_fallback: bool = False
    extracted_entities: dict[str, Any] = Field(default_factory=dict)


class EntityResolutionResult(BaseModel):
    """Result of entity disambiguation and normalization gate."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "needs_clarification", "not_found"] = "ok"
    candidates: list[CandidateItem] | None = None
    resolved_author_id: str | None = None
    resolved_author_name: str | None = None
    resolved_institution_id: str | None = None
    resolved_institution_name: str | None = None
    clarification_message: str | None = None


YearOp = Literal["eq", "gt", "gte", "lt", "lte", "between"]


class YearFilter(BaseModel):
    """Typed year constraint with allowlisted operator (FR2.3, docs/08 §2.2).

    Operators are enumerated as Literal so raw strings can never be
    concatenated into SQL — SqlRetriever maps each op to a bound-param
    predicate (=, >, >=, <, <=, BETWEEN).
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
        r"\b(?:setelah(?:\s+tahun)?|sejak|setelah\s+tahun|after|since)\s+(?:tahun\s+)?(19\d\d|20\d\d)\b"
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


def build_extracted_entities(
    question: str,
    filters: FilterParams | None = None,
) -> dict[str, Any]:
    """Build the typed entity contract for RouterOutput (FR2.3).

    Sources: explicit FilterParams first, free-text YearFilter second.
    Keys: year_filter, country, author_name, institution_name, keyword,
    topic_name, document_type (only when present).
    """
    entities: dict[str, Any] = {}
    year_filter = build_year_filter(question, filters)
    if year_filter is not None:
        entities["year_filter"] = year_filter.model_dump(exclude_none=True)
    if filters is not None:
        if filters.country:
            entities["country"] = filters.country
        if filters.author_name:
            entities["author_name"] = filters.author_name
        if filters.institution_name:
            entities["institution_name"] = filters.institution_name
        if filters.keyword:
            entities["keyword"] = filters.keyword
        if filters.topic_name:
            entities["topic_name"] = filters.topic_name
        if filters.document_type:
            entities["document_type"] = filters.document_type
    return entities


# --- Regex Pattern Definitions for 4 Routes (ID & EN) ---

# 1. SQLRoute Patterns (Counting, Aggregation, Ranking, Metadata, List, Exact stats)
SQL_PATTERNS = [
    re.compile(r"\b(berapa|jumlah|total|hitung)\b.*\b(publikasi|paper|artikel|sitasi|author|penulis|institusi|dana|grant)\b", re.IGNORECASE),
    re.compile(r"\b(siapa|daftar|tampilkan|sebutkan)\b.*\b(top\s*\d+|\d+\s*teratas|paling\s*(produktif|banyak|sering|banyak disitasi))\b", re.IGNORECASE),
    re.compile(r"\b(top\s*\d+|\d+\s*teratas|peringkat|ranking)\b", re.IGNORECASE),
    re.compile(r"\b(sitasi\s*terbanyak|most\s*cited|highest\s*citations?)\b", re.IGNORECASE),
    re.compile(r"\b(how\s*many|count\s*of|total\s*(number\s*of)?\s*(publications|papers|articles|citations|authors|grants?))\b", re.IGNORECASE),
    re.compile(r"\b(who\s*is|who\s*are|list|show)\b.*\b(top\s*\d+|\d+\s*most\s*(productive|cited)|most\s*prolific)\b", re.IGNORECASE),
    re.compile(r"\b(most\s*productive|prolific\s*authors?|most\s*active)\b", re.IGNORECASE),
    re.compile(r"\b(publikasi|papers?|articles?)\s*(tahun|pada\s*tahun|in\s*year|published\s*in)\s*\d{4}\b", re.IGNORECASE),
    re.compile(r"\b(funding\s*agency|sumber\s*dana|hibah|grant\s*number)\b", re.IGNORECASE),
    re.compile(r"\b(open\s*access|document\s*type|tipe\s*dokumen|bahasa\s*dokumen)\b", re.IGNORECASE),
]
# 2. GraphRoute Patterns (Collaboration, Co-authorship, Networks, Partnerships)
GRAPH_PATTERNS = [
    re.compile(r"\b(kolaborasi|berkolaborasi|kerjasama|kemitraan)\b", re.IGNORECASE),
    re.compile(r"\b(co-?authors?(hip)?|rekan\s*penulis|teman\s*menulis)\b", re.IGNORECASE),
    re.compile(r"\b(jaringan\s*(riset|penelitian|peneliti|institusi|kolaborasi)|network(ing)?)\b", re.IGNORECASE),
    re.compile(r"\b(collaborat\w*|partner\w*)\b", re.IGNORECASE),
    re.compile(r"\b(who\s*(has\s*)?worked\s*with|joint\s*publications?|who\s*published\s*with)\b", re.IGNORECASE),
    re.compile(r"\b(mitra\s*riset|mitra\s*institusi|institutions?\s*collaborating)\b", re.IGNORECASE),
]

# 3. HybridRoute Patterns (Trends, Topic Evolution, Emerging Topics, Researcher Expertise Scoring)
HYBRID_PATTERNS = [
    re.compile(r"\b(tren|perkembangan|evolusi)\b.*\b(topik|bidang|riset|terapi|teknologi|domain)\b", re.IGNORECASE),
    re.compile(r"\b(topik\s*(berkembang|baru|populer)|emerging\s*topics?|topic\s*evolution)\b", re.IGNORECASE),
    re.compile(r"\b(skor\s*kepakaran|expertise\s*score|pakar\s*(utama|terbaik|terkemuka)|leading\s*experts?)\b", re.IGNORECASE),
    re.compile(r"\b(sintesis\s*kebijakan|policy\s*synthesis|rekomendasi\s*kebijakan|arah\s*riset)\b", re.IGNORECASE),
    re.compile(r"\b(trend(s)?\s*(in|of)|how\s*has\s*.*evolved|growth\s*score|citation\s*acceleration)\b", re.IGNORECASE),
    # Emerging-topic intent stated as "<adjective> ... topics" rather than the
    # literal "emerging topics". Without these, "What are emerging stem cell
    # therapy topics after 2020 and who are the experts?" matched nothing and
    # fell through to the VectorRoute default — measured, not hypothetical.
    re.compile(
        r"\b(emerging|emergent|naissant|berkembang|baru|muncul|terbaru)\b"
        r"[^?]{0,80}?\b(topics?|topik|bidang|research\s*areas?|clusters?)\b",
        re.IGNORECASE,
    ),
    # "who are the experts" carries expertise intent on its own — Gold
    # `researcher_expertise` answers it, VectorRoute does not. Deliberately
    # narrow: only the copular forms, never a bare mention of the word
    # "researcher", so "top 5 researchers by publication count" keeps its
    # SQLRoute ranking semantics (HybridRoute is evaluated before SQLRoute).
    re.compile(
        r"\bwho\s+(?:are|is)\s+(?:the\s+|these\s+)?(?:top\s+|leading\s+|main\s+)?experts?\b"
        r"|\bwho\s+(?:are|is)\s+the\s+(?:leading|top|main|key)\s+(?:researchers?|scientists?|authors?)\b"
        r"|\bsiapa\s+(?:para\s+)?(?:pakar|ahli)\b"
        r"|\bpakar\s+(?:terbaik|terutama|terkemuka)\b",
        re.IGNORECASE,
    ),
]

# 4. VectorRoute Patterns (Semantic/Conceptual exploration, abstract topics, biological/clinical mechanisms)
VECTOR_PATTERNS = [
    re.compile(r"\b(paper|artikel|jurnal|naskah)\s*(yang|tentang|mengenai|membahas|meneliti)\b", re.IGNORECASE),
    re.compile(r"\b(papers?|articles?|studies|literature)\s*(about|on|discussing|exploring|related\s*to)\b", re.IGNORECASE),
    re.compile(r"\b(konsep|mekanisme|pengaruh|efek|studi|peran|analisis\s*kualitatif)\b", re.IGNORECASE),
    re.compile(r"\b(concept\s*of|mechanism\s*of|role\s*of|effect\s*of|impact\s*of|how\s*does\s*.*work)\b", re.IGNORECASE),
    re.compile(r"\b(penelitian\s*terkait|studi\s*mengenai|literatur\s*tentang)\b", re.IGNORECASE),
]


# P7 js-*: translation table built once at import, not per normalize_text call.
_PUNCT_TRANSLATOR = str.maketrans("", "", string.punctuation)


def normalize_text(text: str) -> str:
    """Normalize string by lowercasing, stripping punctuation, and compressing whitespace."""
    if not text:
        return ""
    # Strip punctuation and lower
    clean = text.translate(_PUNCT_TRANSLATOR).lower()
    return " ".join(clean.split())


# Tokens that mark a regex capture as query phrasing rather than a person or
# institution name (e.g. "penulis paling produktif" is not a person).
# NOTE (Phase 3 fix P0-2): geographic/entity tokens such as "indonesia"
# must NOT be stoplisted — "Universitas Indonesia" is a real institution.
# Only query verbs/adjectives/rank words are rejected here.
# NOTE (top-N "by <metric>" fix): English metric words must be stoplisted too —
# otherwise "by publication count" is extracted as author_name="publication count",
# matches zero rows, and the gate short-circuits to not_found before SQL retrieval.
_NON_NAME_TOKENS = frozenset({
    "top", "most", "paling", "terbanyak", "teratas", "terbaik", "utama",
    "produktif", "prolific", "productive", "active", "aktif", "cited",
    "sitasi", "publikasi", "paper", "papers", "artikel", "tahun", "total",
    "jumlah", "berapa", "siapa", "daftar", "tampilkan", "sebutkan",
    "penulis", "author", "authors", "peneliti", "institusi", "institution",
    "kolaborasi", "collaboration", "jaringan", "network", "tren", "trend",
    "yang", "dan", "dari", "dengan",
    "tentang", "mengenai", "terkait",
    "mana", "apa", "bagaimana", "apakah", "kapan", "dimana", "kenapa", "mengapa",
    # English metric phrasing in top-N / ranked queries ("by <metric>").
    # None of these can be part of a person name, so any capture containing
    # them is query phrasing, not an entity.
    "publication", "publications", "count", "counts", "counting",
    "citation", "citations", "number", "numbers",
    "statistic", "statistics", "stats",
    "rank", "ranking", "ranked", "score", "scores",
    "year", "years",
    # Connectors that never appear inside a person/institution name.
    "by", "with",
})


def _looks_like_name(captured: str) -> bool:
    """Reject captures containing query phrasing instead of a real name."""
    tokens = normalize_text(captured).split()
    if not tokens:
        return False
    if any(t in _NON_NAME_TOKENS for t in tokens):
        return False
    # Metric phrasing ("by publication count") is lowercase; real person and
    # institution names carry at least one capital in natural queries
    # ("Septi Gumiandari", "Universitas Indonesia"). A lowercase-only capture
    # is never trusted as an entity: missing a lowercase-spelled name degrades
    # gracefully to an unfiltered search, while trusting a metric phrase causes
    # a hard false not_found before retrieval even runs.
    return any(ch.isupper() for ch in captured)


# Generic type words a user may type as a *label* in front of an institution
# name ("institusi Universitas Andalas"). Matched case-insensitively so the
# capitalisation of the captured text stays available as the discriminator.
_INST_TYPE_PREFIX_RE = re.compile(
    r"^(?:institusi|institut|universitas|university|institute)\s+",
    re.IGNORECASE,
)


def _strip_lowercase_type_prefix(captured: str) -> str:
    """Drop a leading type word the user typed as a lowercase label.

    The type word is part of the capture (see extract_candidate_names), because
    Indonesian/Scopus institution names routinely begin with it — "Universitas
    Indonesia" must not degrade to "Indonesia". But when the user wrote it as a
    generic label it is not part of the name and must be removed, or the lookup
    key "institusi Universitas Andalas" matches nothing and the gate reports a
    false not_found.

    Capitalisation is the discriminator, reusing the convention already applied
    by :func:`_looks_like_name`: a word the user means as part of a proper noun
    is capitalised ("Universitas Indonesia"), a generic label is lowercase
    ("institusi Universitas Andalas").
    """
    m = _INST_TYPE_PREFIX_RE.match(captured)
    if m is None:
        return captured
    prefix = captured[: m.end()]
    if any(ch.isupper() for ch in prefix):
        return captured
    remainder = captured[m.end() :].strip()
    return remainder or captured


# Query grammar that terminates an entity-name capture, ID + EN. Shared by the
# author and institution extractors so both stop at the same boundary.
#
# Only verbs, auxiliaries, and temporal words belong here. Prepositions such as
# "of"/"and"/"from" are deliberately excluded because they occur INSIDE real
# institution names ("University of Papua", "Universitas Sebelas Maret") and
# would truncate the candidate to "University".
#
# Every alternative is matched with a trailing \b, so a short form never cuts a
# longer word: "in" cannot split "Indonesia"/"Informatika", and "is" cannot split
# "Ismail".
_NAME_TERMINATOR_ALT = (
    r"pada|tahun|sejak|setelah|sebelum|antara|"
    r"di|in|with|yang|by|"
    r"published|publishes|publishing|publish|"
    r"produced|produces|producing|produce|"
    r"written|writes|wrote|write|"
    r"have|has|had|does|did|were|was|are|is|do"
)


class QuestionRouter:
    """Rule-based question intent classifier mapping queries to one of 4 RAG routes."""

    @classmethod
    def classify_route(
        cls,
        question: str,
        filters: FilterParams | None = None,
    ) -> RouteDecision:
        """Classify user query and structured filters into target RAG route."""
        q = question.strip()
        entities = build_extracted_entities(question, filters)

        # Step 1: Check GraphRoute triggers (High specificity for collaboration & network queries).
        # Decision: collaboration specificity wins over aggregate wording, so
        # "Berapa jumlah kolaborasi institusi ...?" routes to GraphRoute, not SQLRoute.
        for pattern in GRAPH_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="GraphRoute",
                    reasoning=f"Matched GraphRoute pattern '{pattern.pattern}' for collaboration/network intent",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # Step 2: Check HybridRoute triggers (Trends, evolution, expertise scoring)
        for pattern in HYBRID_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning=f"Matched HybridRoute pattern '{pattern.pattern}' for trend/evolution/expertise intent",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # If filters explicitly specify topic_name combined with general query
        if filters and filters.topic_name:
            # If asking about trends/experts on topic
            if any(term in q.lower() for term in ["tren", "trend", "pakar", "expert", "evolusi", "growth"]):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning="Query combines topic_name filter with trend/expertise keywords",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # Step 3: Check SQLRoute triggers (Counting, aggregation, ranking, top-N, explicit stats)
        for pattern in SQL_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning=f"Matched SQLRoute pattern '{pattern.pattern}' for aggregation/ranking/relational intent",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # Check if structured filters strongly imply SQL relational search
        if filters and (
            filters.year is not None
            or filters.year_from is not None
            or filters.year_to is not None
            or filters.author_name
            or filters.institution_name
            or filters.country
            or filters.document_type
            or filters.keyword
        ):
            # If the question asks for lists or counts with filters
            if any(term in q.lower() for term in ["siapa", "who", "berapa", "how many", "daftar", "list", "tampilkan", "show", "publikasi", "papers"]):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning="Query specifies structured metadata filters and listing/counting intent",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # Step 4: Check VectorRoute triggers (Conceptual, abstract exploration)
        for pattern in VECTOR_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="VectorRoute",
                    reasoning=f"Matched VectorRoute pattern '{pattern.pattern}' for semantic/conceptual intent",
                    answered_via_fallback=False,
                    extracted_entities=entities,
                )

        # Step 5: Fallback handling
        # If question has structured filters (e.g. topic_name) -> HybridRoute fallback
        if filters and filters.topic_name:
            return RouteDecision(
                route="HybridRoute",
                reasoning="Fallback to HybridRoute due to present topic_name filter",
                answered_via_fallback=True,
                extracted_entities=entities,
            )

        # Default fallback to VectorRoute with answered_via_fallback = True
        return RouteDecision(
            route="VectorRoute",
            reasoning="No definitive rule pattern matched; defaulting to semantic VectorRoute fallback",
            answered_via_fallback=True,
            extracted_entities=entities,
        )


class EntityResolutionGate:
    """Disambiguation and entity validation gate against live PostgreSQL records."""

    @classmethod
    def extract_candidate_names(
        cls,
        question: str,
        filters: FilterParams | None = None,
    ) -> tuple[str | None, str | None]:
        """Extract possible author and institution candidate names from query or filters."""
        author_name: str | None = None
        institution_name: str | None = None

        if filters:
            if filters.author_name:
                author_name = filters.author_name.strip()
            if filters.institution_name:
                institution_name = filters.institution_name.strip()

        # If not in filters, try regex heuristics on the query
        if not author_name:
            # e.g., "penulis Dr. Ahmad", "author John Doe", "oleh Septi Gumiandari", "by Septi Gumiandari"
            # Terminators carry \b so "in" never cuts "Indonesia"/"Informatika".
            # "by" is a terminator too: "author Septi Gumiandari by year" must
            # capture just the name, not "Septi Gumiandari by year".
            # Iterate all matches: "penulis mana ..." must not block a later real name.
            for auth_match in re.finditer(
                rf"\b(?:penulis|author|peneliti|oleh|by)\s+([A-Z][a-zA-Z\.\'\-\s]+?)(?:\s+(?:{_NAME_TERMINATOR_ALT})\b|\?|$)",
                question,
                re.IGNORECASE,
            ):
                extracted = auth_match.group(1).strip()
                # Exclude captures that are query phrasing, not person names
                if _looks_like_name(extracted):
                    author_name = extracted
                    break

        if not institution_name:
            # e.g., "institusi Universitas Andalas". Bare prepositions (di/at)
            # are deliberately excluded: they over-match phrases like
            # "Paper di Indonesia" or "published at ..." and poison the gate.
            #
            # The type word is captured INSIDE the name, not consumed as a
            # prefix. Indonesian/Scopus institution names frequently *begin*
            # with the type word ("Universitas Indonesia, Depok, Indonesia"),
            # so consuming it truncated the candidate to "Indonesia" and sent a
            # country word into institution resolution — measured to return 10
            # bogus candidates for "Berapa publikasi Universitas Indonesia
            # tahun 2023". _strip_lowercase_type_prefix removes the word again
            # when the user typed it as a lowercase label instead.
            for inst_match in re.finditer(
                rf"\b((?:institusi|universitas|university|institut)\s+[A-Z][a-zA-Z\.\'\-\s]+?)(?:\s+(?:{_NAME_TERMINATOR_ALT})\b|\?|$)",
                question,
                re.IGNORECASE,
            ):
                extracted = _strip_lowercase_type_prefix(
                    inst_match.group(1).strip()
                )
                if _looks_like_name(extracted):
                    institution_name = extracted
                    break

        return author_name, institution_name

    @classmethod
    async def resolve_entities(
        cls,
        conn: asyncpg.Connection,
        question: str,
        filters: FilterParams | None = None,
    ) -> EntityResolutionResult:
        """Resolve author and institution entities, checking for ambiguous candidate sets."""
        author_query, inst_query = cls.extract_candidate_names(question, filters)

        # Accumulators: both entities resolve jointly so a resolved author
        # never masks an ambiguous institution (and vice versa).
        resolved_author_id: str | None = None
        resolved_author_name: str | None = None
        resolved_institution_id: str | None = None
        resolved_institution_name: str | None = None

        # 1. Author resolution
        if author_query and len(author_query) >= 3:
            norm_name = normalize_text(author_query)

            # Exact match check
            exact_rows = await asyncio.wait_for(
                conn.fetch(
                    """
                    SELECT author_id, author_name, author_name_normalized
                    FROM authors
                    WHERE author_name_normalized = $1
                       OR author_name ILIKE $2 ESCAPE '\'
                    LIMIT 10;
                    """,
                    norm_name,
                    author_query,
                ),
                timeout=STATEMENT_TIMEOUT_S,
            )

            if len(exact_rows) == 1:
                row = exact_rows[0]
                resolved_author_id = row["author_id"]
                resolved_author_name = row["author_name"]
            elif len(exact_rows) > 1:
                # Multiple candidates found -> needs clarification
                candidate_items: list[CandidateItem] = []
                # P1 async-*: one GROUP BY over ANY($1) instead of N sequential
                # COUNT round-trips (author_id is VARCHAR — text[] comparison is safe).
                author_count_rows = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT author_id, COUNT(publication_id) AS cnt FROM pub_author "
                        "WHERE author_id = ANY($1) GROUP BY author_id;",
                        [r["author_id"] for r in exact_rows[:5]],
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
                author_count_by_id = {str(cr["author_id"]): int(cr["cnt"]) for cr in author_count_rows}
                for r in exact_rows[:5]:
                    pub_count = author_count_by_id.get(str(r["author_id"]), 0)
                    candidate_items.append(
                        CandidateItem(
                            id=r["author_id"],
                            name=r["author_name"],
                            type="author",
                            publication_count=int(pub_count),
                            affiliation=None,
                        )
                    )
                return EntityResolutionResult(
                    status="needs_clarification",
                    candidates=candidate_items,
                    clarification_message=(
                        f"Ditemukan {len(exact_rows)} penulis yang cocok dengan '{author_query}'. "
                        "Silakan pilih penulis yang dimaksud."
                    ),
                )
            else:
                # Partial ILIKE search if not exact
                partial_rows = await asyncio.wait_for(
                    conn.fetch(
                        """
                        SELECT a.author_id, a.author_name, COUNT(pa.publication_id) AS pub_count
                        FROM authors a
                        LEFT JOIN pub_author pa ON pa.author_id = a.author_id
                        WHERE a.author_name ILIKE $1 ESCAPE '\'
                        GROUP BY a.author_id, a.author_name
                        ORDER BY pub_count DESC
                        LIMIT 10;
                        """,
                        escape_like_pattern(author_query),
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
                if len(partial_rows) > 1:
                    candidates = [
                        CandidateItem(
                            id=r["author_id"],
                            name=r["author_name"],
                            type="author",
                            publication_count=int(r["pub_count"]),
                            affiliation=None,
                        )
                        for r in partial_rows[:5]
                    ]
                    return EntityResolutionResult(
                        status="needs_clarification",
                        candidates=candidates,
                        clarification_message=(
                            f"Ditemukan {len(partial_rows)} kandidat penulis untuk '{author_query}'. "
                            "Mohon pilih entitas yang tepat."
                        ),
                    )
                elif len(partial_rows) == 1:
                    r = partial_rows[0]
                    resolved_author_id = r["author_id"]
                    resolved_author_name = r["author_name"]
                else:
                    # Mentioned author matches zero records -> deterministic not_found (FR2.4)
                    return EntityResolutionResult(
                        status="not_found",
                        clarification_message=(
                            f"Tidak ditemukan penulis yang cocok dengan '{author_query}' dalam database."
                        ),
                    )

        # 2. Institution resolution
        if inst_query and len(inst_query) >= 3:
            norm_inst = normalize_text(inst_query)

            # Exact match check
            exact_insts = await asyncio.wait_for(
                conn.fetch(
                    """
                    SELECT institution_id, institution_name, country
                    FROM institutions
                    WHERE institution_name_normalized = $1
                       OR institution_name ILIKE $2 ESCAPE '\'
                    LIMIT 10;
                    """,
                    norm_inst,
                    inst_query,
                ),
                timeout=STATEMENT_TIMEOUT_S,
            )

            if len(exact_insts) == 1:
                row = exact_insts[0]
                resolved_institution_id = row["institution_id"]
                resolved_institution_name = row["institution_name"]
            elif len(exact_insts) > 1:
                candidate_items = []
                # P1 async-*: one GROUP BY over ANY($1) instead of N sequential
                # COUNT round-trips (institution_id is VARCHAR — text[] comparison is safe).
                inst_count_rows = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT institution_id, COUNT(publication_id) AS cnt FROM pub_institution "
                        "WHERE institution_id = ANY($1) GROUP BY institution_id;",
                        [r["institution_id"] for r in exact_insts[:5]],
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
                inst_count_by_id = {str(cr["institution_id"]): int(cr["cnt"]) for cr in inst_count_rows}
                for r in exact_insts[:5]:
                    pub_count = inst_count_by_id.get(str(r["institution_id"]), 0)
                    candidate_items.append(
                        CandidateItem(
                            id=r["institution_id"],
                            name=r["institution_name"],
                            type="institution",
                            publication_count=int(pub_count),
                            affiliation=r["country"] or None,
                        )
                    )
                return EntityResolutionResult(
                    status="needs_clarification",
                    candidates=candidate_items,
                    clarification_message=(
                        f"Ditemukan {len(exact_insts)} institusi yang cocok dengan '{inst_query}'. "
                        "Silakan pilih institusi yang dimaksud."
                    ),
                )
            else:
                # Partial search
                partial_insts = await asyncio.wait_for(
                    conn.fetch(
                        """
                        SELECT i.institution_id, i.institution_name, i.country, COUNT(pi.publication_id) AS pub_count
                        FROM institutions i
                        LEFT JOIN pub_institution pi ON pi.institution_id = i.institution_id
                        WHERE i.institution_name ILIKE $1 ESCAPE '\'
                        GROUP BY i.institution_id, i.institution_name, i.country
                        ORDER BY pub_count DESC
                        LIMIT 10;
                        """,
                        escape_like_pattern(inst_query),
                    ),
                    timeout=STATEMENT_TIMEOUT_S,
                )
                if len(partial_insts) > 1:
                    candidates = [
                        CandidateItem(
                            id=r["institution_id"],
                            name=r["institution_name"],
                            type="institution",
                            publication_count=int(r["pub_count"]),
                            affiliation=r["country"] or None,
                        )
                        for r in partial_insts[:5]
                    ]
                    return EntityResolutionResult(
                        status="needs_clarification",
                        candidates=candidates,
                        clarification_message=(
                            f"Ditemukan {len(partial_insts)} kandidat institusi untuk '{inst_query}'. "
                            "Mohon pilih institusi yang tepat."
                        ),
                    )
                elif len(partial_insts) == 1:
                    r = partial_insts[0]
                    resolved_institution_id = r["institution_id"]
                    resolved_institution_name = r["institution_name"]
                else:
                    # Mentioned institution matches zero records -> deterministic not_found (FR2.4)
                    return EntityResolutionResult(
                        status="not_found",
                        clarification_message=(
                            f"Tidak ditemukan institusi yang cocok dengan '{inst_query}' dalam database."
                        ),
                    )

        # Joint result: either entity may be resolved while the other was unmentioned
        return EntityResolutionResult(
            status="ok",
            resolved_author_id=resolved_author_id,
            resolved_author_name=resolved_author_name,
            resolved_institution_id=resolved_institution_id,
            resolved_institution_name=resolved_institution_name,
        )
