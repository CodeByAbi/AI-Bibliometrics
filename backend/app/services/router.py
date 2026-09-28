"""Question routing and entity resolution gate services.

Docs Reference: docs/05 Retrieval Rag Design.md §3, docs/10 Implementation Plan.md §1 (Task 4).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import FilterParams


RouteType = Literal["SQLRoute", "VectorRoute", "GraphRoute", "HybridRoute"]


class RouteDecision(BaseModel):
    """Result of question intent routing classification."""

    model_config = ConfigDict(frozen=True)

    route: RouteType
    reasoning: str
    answered_via_fallback: bool = False
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)


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
]

# 4. VectorRoute Patterns (Semantic/Conceptual exploration, abstract topics, biological/clinical mechanisms)
VECTOR_PATTERNS = [
    re.compile(r"\b(paper|artikel|jurnal|naskah)\s*(yang|tentang|mengenai|membahas|meneliti)\b", re.IGNORECASE),
    re.compile(r"\b(papers?|articles?|studies|literature)\s*(about|on|discussing|exploring|related\s*to)\b", re.IGNORECASE),
    re.compile(r"\b(konsep|mekanisme|pengaruh|efek|studi|peran|analisis\s*kualitatif)\b", re.IGNORECASE),
    re.compile(r"\b(concept\s*of|mechanism\s*of|role\s*of|effect\s*of|impact\s*of|how\s*does\s*.*work)\b", re.IGNORECASE),
    re.compile(r"\b(penelitian\s*terkait|studi\s*mengenai|literatur\s*tentang)\b", re.IGNORECASE),
]


class QuestionRouter:
    """Rule-based question intent classifier mapping queries to one of 4 RAG routes."""

    @classmethod
    def classify_route(
        cls,
        question: str,
        filters: Optional[FilterParams] = None,
    ) -> RouteDecision:
        """Classify user query and structured filters into target RAG route."""
        q = question.strip()

        # Step 1: Check GraphRoute triggers (High specificity for collaboration & network queries)
        for pattern in GRAPH_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="GraphRoute",
                    reasoning=f"Matched GraphRoute pattern '{pattern.pattern}' for collaboration/network intent",
                    answered_via_fallback=False,
                )

        # Step 2: Check HybridRoute triggers (Trends, evolution, expertise scoring)
        for pattern in HYBRID_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning=f"Matched HybridRoute pattern '{pattern.pattern}' for trend/evolution/expertise intent",
                    answered_via_fallback=False,
                )

        # If filters explicitly specify topic_name combined with general query
        if filters and filters.topic_name:
            # If asking about trends/experts on topic
            if any(term in q.lower() for term in ["tren", "trend", "pakar", "expert", "evolusi", "growth"]):
                return RouteDecision(
                    route="HybridRoute",
                    reasoning="Query combines topic_name filter with trend/expertise keywords",
                    answered_via_fallback=False,
                )

        # Step 3: Check SQLRoute triggers (Counting, aggregation, ranking, top-N, explicit stats)
        for pattern in SQL_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning=f"Matched SQLRoute pattern '{pattern.pattern}' for aggregation/ranking/relational intent",
                    answered_via_fallback=False,
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
        ):
            # If the question asks for lists or counts with filters
            if any(term in q.lower() for term in ["siapa", "who", "berapa", "how many", "daftar", "list", "tampilkan", "show", "publikasi", "papers"]):
                return RouteDecision(
                    route="SQLRoute",
                    reasoning="Query specifies structured metadata filters and listing/counting intent",
                    answered_via_fallback=False,
                )

        # Step 4: Check VectorRoute triggers (Conceptual, abstract exploration)
        for pattern in VECTOR_PATTERNS:
            if pattern.search(q):
                return RouteDecision(
                    route="VectorRoute",
                    reasoning=f"Matched VectorRoute pattern '{pattern.pattern}' for semantic/conceptual intent",
                    answered_via_fallback=False,
                )

        # Step 5: Fallback handling
        # If question has structured filters (e.g. topic_name) -> HybridRoute fallback
        if filters and filters.topic_name:
            return RouteDecision(
                route="HybridRoute",
                reasoning="Fallback to HybridRoute due to present topic_name filter",
                answered_via_fallback=True,
            )

        # Default fallback to VectorRoute with answered_via_fallback = True
        return RouteDecision(
            route="VectorRoute",
            reasoning="No definitive rule pattern matched; defaulting to semantic VectorRoute fallback",
            answered_via_fallback=True,
        )


# NOTE (Phase 3, Task 4b): EntityResolutionGate lands in the next commit.
# This module currently exposes only the deterministic QuestionRouter.
