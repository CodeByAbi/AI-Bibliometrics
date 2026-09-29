"""Deterministic EvidenceRanker for scoring, sorting, and confidence calibration.

Docs Reference: docs/05 Retrieval Rag Design.md §4, §5; docs/10 Implementation Plan.md §1 (Task 7); docs/11 Roadmap.md (Fase 5).
"""

from __future__ import annotations

from typing import List, Optional
from backend.app.models.ask import EvidenceObject, SourceItem
from backend.app.services.evidence.models import EvidenceItem


class EvidenceRanker:
    """Deterministic ranking engine and confidence calculator for normalized evidence."""

    @staticmethod
    def calculate_vector_confidence(similarity_score: float) -> float:
        """Passthrough confidence for vector similarity matches.

        Returns ``round(similarity, 4)`` clamped to [0, 1], i.e. range
        0.65-1.0 above the canonical ``>= 0.65`` cosine gate (docs/05 §5.2).
        No rescaling to 0.70-1.0 is applied; docs/05 §4.1 records the raw
        similarity as confidence.
        """
        clamped = max(0.0, min(1.0, float(similarity_score)))
        return round(clamped, 4)

    @staticmethod
    def calculate_sql_confidence() -> float:
        """Exact relational queries carry maximal confidence (1.0)."""
        return 1.0

    @staticmethod
    def calculate_graph_confidence() -> float:
        """Precomputed derived edge tables carry exact relational confidence (1.0)."""
        return 1.0

    @staticmethod
    def calculate_analytics_confidence() -> float:
        """Precomputed Gold analytics tables carry verified analytical confidence (1.0)."""
        return 1.0
    @classmethod
    def rank_evidence_objects(
        cls, evidence_objects: List[EvidenceObject]
    ) -> List[EvidenceObject]:
        """Deterministically sort EvidenceObjects by confidence (DESC), numeric value (DESC), metric (ASC), and claim (ASC)."""
        def _ev_key(ev: EvidenceObject):
            num_val = float(ev.value) if isinstance(ev.value, (int, float)) else 0.0
            return (
                -ev.confidence,
                -num_val,
                ev.metric,
                ev.claim,
            )

        return sorted(evidence_objects, key=_ev_key)

    @classmethod
    def rank_sources(cls, sources: List[SourceItem]) -> List[SourceItem]:
        """Deterministically sort SourceItems by relevance_score (DESC), year (DESC), title (ASC), publication_id (ASC)."""
        return sorted(
            sources,
            key=lambda s: (
                -(s.relevance_score if s.relevance_score is not None else 0.0),
                -(s.year if s.year is not None else 0),
                s.title or "",
                s.publication_id,
            ),
        )

    @classmethod
    def rank_items(cls, items: List[EvidenceItem]) -> List[EvidenceItem]:
        """Deterministically sort EvidenceItems by score (DESC), confidence (DESC), year (DESC), title (ASC), source_id (ASC)."""
        return sorted(
            items,
            key=lambda it: (
                -it.score,
                -it.confidence,
                -(it.year if it.year is not None else 0),
                it.title or "",
                it.source_id,
            ),
        )
