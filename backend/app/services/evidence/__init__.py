"""Evidence Layer package for canonical evidence normalization, ranking, and deduplication.

Docs Reference: docs/05 Retrieval Rag Design.md §4, docs/10 Implementation Plan.md §1 (Task 7), docs/11 Roadmap.md (Fase 5).
"""

from backend.app.models.ask import (
    EvidenceObject,
    EvidenceSourceRef,
    SourceItem,
)
from backend.app.services.evidence.formatting import format_citation, format_period
from backend.app.services.evidence.models import (
    EvidenceItem,
    EvidenceSet,
)
from backend.app.services.evidence.ranker import EvidenceRanker
from backend.app.services.evidence.unifier import EvidenceUnifier

__all__ = [
    "EvidenceItem",
    "EvidenceObject",
    "EvidenceRanker",
    "EvidenceSet",
    "EvidenceSourceRef",
    "EvidenceUnifier",
    "SourceItem",
    "format_citation",
    "format_period",
]
