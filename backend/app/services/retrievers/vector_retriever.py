"""VectorRetriever service: semantic similarity search across publication chunks.

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/10 Implementation Plan.md §1 (Task 6), docs/11 Roadmap.md (Fase 4).
"""

from __future__ import annotations

from typing import Any, List, Optional
import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.ask import FilterParams
from backend.app.services.embedding import generate_query_embedding


class VectorMatchItem(BaseModel):
    """A single publication matched via vector chunk similarity."""

    model_config = ConfigDict(frozen=True)

    publication_id: str
    title: str
    year: Optional[int] = None
    doi: Optional[str] = None
    eid: Optional[str] = None
    citation_count: Optional[int] = 0
    chunk_id: str
    chunk_text: str
    similarity_score: float


class VectorRetrievalResult(BaseModel):
    """Result payload of vector similarity retrieval."""

    model_config = ConfigDict(frozen=True)

    matches: List[VectorMatchItem] = Field(default_factory=list)
    threshold: float = 0.65
    filters_ignored: List[str] = Field(default_factory=list)
    sql_executed: str

    @property
    def is_empty(self) -> bool:
        """True if no chunk matches exceeded the similarity threshold."""
        return len(self.matches) == 0

    @property
    def match_count(self) -> int:
        """Total number of deduplicated publications matched."""
        return len(self.matches)
