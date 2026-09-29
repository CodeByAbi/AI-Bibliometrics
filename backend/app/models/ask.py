"""Ask endpoint request and response schemas.

Docs Reference: docs/06 Api Design.md §5, docs/05 Retrieval Rag Design.md §4.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilterParams(BaseModel):
    """Structured search filters."""

    model_config = ConfigDict(frozen=True)

    year: Optional[int] = Field(None, ge=1900, le=2026, description="Tahun publikasi eksak")
    year_from: Optional[int] = Field(None, ge=1900, le=2026, description="Tahun awal publikasi")
    year_to: Optional[int] = Field(None, ge=1900, le=2026, description="Tahun akhir publikasi")
    country: Optional[str] = Field(None, max_length=128, description="Negara institusi (lowercase)")
    author_name: Optional[str] = Field(None, max_length=255, description="Nama penulis")
    institution_name: Optional[str] = Field(None, max_length=255, description="Nama institusi")
    topic_name: Optional[str] = Field(None, max_length=255, description="Klaster topik riset")
    document_type: Optional[str] = Field(None, max_length=64, description="Tipe dokumen Scopus")
    keyword: Optional[str] = Field(None, max_length=255, description="Kata kunci publikasi (lowercase)")

    @field_validator("year_to")
    @classmethod
    def validate_year_range(cls, v: Optional[int], info) -> Optional[int]:
        year_from = info.data.get("year_from")
        if v is not None and year_from is not None and v < year_from:
            raise ValueError("year_to must be greater than or equal to year_from")
        return v


class AskRequest(BaseModel):
    """Inbound question request body."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Pertanyaan riset pengguna dalam bahasa alami (ID/EN)",
    )
    filters: Optional[FilterParams] = Field(
        default_factory=FilterParams,
        description="Filter metadata terstruktur",
    )
    developer_mode: Optional[bool] = Field(
        False,
        description="Flag untuk menyertakan metadata debug, SQL, dan latensi",
    )

    @field_validator("question")
    @classmethod
    def clean_question(cls, v: str) -> str:
        trimmed = v.strip()
        if len(trimmed) < 3:
            raise ValueError("question must have at least 3 non-whitespace characters")
        return trimmed


class EvidenceSourceRef(BaseModel):
    """Reference to a specific supporting publication record."""

    model_config = ConfigDict(frozen=True)

    publication_id: str = Field(..., description="ID kanonikal publikasi di PostgreSQL (publications)")
    doi: Optional[str] = Field(None, description="DOI resmi publikasi (jika ada)")
    eid: Optional[str] = Field(None, description="EID Scopus publikasi")
    title: Optional[str] = Field(None, description="Judul publikasi")
    year: Optional[int] = Field(None, description="Tahun publikasi")


class EvidenceObject(BaseModel):
    """Structured evidence unit grounding numerical or factual statements."""

    model_config = ConfigDict(frozen=True)

    claim: str = Field(..., description="Pernyataan faktual spesifik yang disintesis")
    metric: str = Field(
        ...,
        description="Jenis metrik: publication_count | citation_count | expertise_score | growth_score | citation_acceleration | similarity_score",
    )
    value: Union[float, int, str] = Field(..., description="Nilai numerik eksak dari database")
    period: str = Field(..., description="Rentang waktu observasi, misal: '2020-2023' atau 'all-time'")
    sources: List[EvidenceSourceRef] = Field(default_factory=list, description="Daftar publikasi bukti pendukung")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Tingkat keyakinan bukti data")


class SourceItem(BaseModel):
    """Retrieved publication record item."""

    model_config = ConfigDict(frozen=True)

    publication_id: str
    title: str
    year: Optional[int] = None
    doi: Optional[str] = None
    source_type: Literal["sql", "vector", "graph", "analytics"]
    relevance_score: Optional[float] = None
    provenance: Optional[str] = None


class CandidateItem(BaseModel):
    """Ambiguous entity candidate for user clarification."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    type: Literal["author", "institution", "topic"]
    publication_count: int
    affiliation: Optional[str] = None


class DebugInfo(BaseModel):
    """Developer diagnostics information."""

    model_config = ConfigDict(frozen=True)

    sql_executed: Optional[str] = None
    route_reasoning: Optional[str] = None
    latency_breakdown_ms: Dict[str, float] = Field(default_factory=dict)


class AskResponse(BaseModel):
    """Standardized RAG response envelope."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(..., description="UUIDv4 pelacakan request yang unik")
    status: Literal["ok", "not_found", "needs_clarification", "error"]
    route: Literal["SQLRoute", "VectorRoute", "GraphRoute", "HybridRoute"]
    answer: str = Field(..., description="Teks jawaban naratif yang ter-grounding")
    evidence_objects: List[EvidenceObject] = Field(
        default_factory=list,
        description="Array bukti numerik dan tematik terstruktur",
    )
    sources: List[SourceItem] = Field(
        default_factory=list,
        description="Daftar naskah literatur bukti",
    )
    candidates: Optional[List[CandidateItem]] = Field(
        None,
        description="Daftar pilihan entitas ambigu saat status=needs_clarification",
    )
    filters_ignored: List[str] = Field(
        default_factory=list,
        description="Daftar filter yang diabaikan",
    )
    answered_via_fallback: bool = Field(
        False,
        description="Flag bila rute dijatuhkan ke fallback semantik",
    )
    unverified_citations: List[str] = Field(
        default_factory=list,
        description="Sitasi yang dipangkas oleh CitationVerifier",
    )
    debug: Optional[DebugInfo] = Field(
        None,
        description="Metadata debug jika developer_mode=true",
    )
