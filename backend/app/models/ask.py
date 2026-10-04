"""Ask endpoint request and response schemas.

Docs Reference: docs/06 Api Design.md §5, docs/05 Retrieval Rag Design.md §4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def format_citation(
    title: Optional[str], year: Optional[int], doi: Optional[str]
) -> str:
    """Canonical [Title, Year, DOI/no-doi] citation string.

    Defined here (not in ``services.evidence.formatting``) to keep the
    ``models`` layer free of a circular dependency on the services layer.
    ``backend.app.services.evidence.formatting`` re-exports the same logic.
    """
    t = title.strip() if title and title.strip() else "Untitled"
    y = str(year) if year is not None else "n.d."
    d = doi.strip() if doi and doi.strip() else "no-doi"
    return f"[{t}, {y}, {d}]"


class FilterParams(BaseModel):
    """Structured search filters."""

    model_config = ConfigDict(frozen=True)

    year: Optional[int] = Field(default=None, ge=1900, le=2026, description="Tahun publikasi eksak")
    year_from: Optional[int] = Field(default=None, ge=1900, le=2026, description="Tahun awal publikasi")
    year_to: Optional[int] = Field(default=None, ge=1900, le=2026, description="Tahun akhir publikasi")
    country: Optional[str] = Field(default=None, max_length=128, description="Negara institusi (lowercase)")
    author_name: Optional[str] = Field(default=None, max_length=255, description="Nama penulis")
    institution_name: Optional[str] = Field(default=None, max_length=255, description="Nama institusi")
    topic_name: Optional[str] = Field(default=None, max_length=255, description="Klaster topik riset")
    document_type: Optional[str] = Field(default=None, max_length=64, description="Tipe dokumen Scopus")
    keyword: Optional[str] = Field(default=None, max_length=255, description="Kata kunci publikasi (lowercase)")

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
    llm_synthesis: Optional[bool] = Field(
        False,
        description="Opt-in sintesis naratif LLM (Qwen2.5-Coder via Ollama, docs/05 §6) "
        "di atas EvidenceSet; fallback deterministik bila LLM tak tersedia",
    )
    session_id: Optional[UUID] = Field(
        default=None,
        description="Sesi riset opsional (schema `app`). Bila diisi, percakapan "
        "sebelumnya dipakai sebagai konteks pemHAMAN pertanyaan — bukan sebagai "
        "sumber fakta. Hanya filter yang tidak disebutkan pertanyaan saat ini "
        "yang diisi dari sesi; `question` sendiri tidak pernah diubah, sehingga "
        "routing, Text-to-SQL, dan verifikasi sitasi tetap persis sama "
        "(docs/03 §0.3 invarian 5).",
    )
    use_session_context: bool = Field(
        True,
        description=(
            "Set false untuk mengabaikan konteks sesi pada permintaan ini. "
            "Mematikannya berarti dua hal sekaligus, bukan satu: (1) scope "
            "dihitung dari nol sehingga filter sesi tidak diwariskan, dan "
            "(2) transkrip percakapan sebelumnya TIDAK dikirim ke prompt LLM "
            "untuk narasi. Persistensi turn tetap berjalan — permintaan ini "
            "masuk ke riwayat sesi seperti biasa. Berguna ketika pengguna "
            "ingin pertanyaan bersifat global, bukan melanjuti sesi."
        ),
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

    def format_citation_tag(self) -> str:
        """Render the first supporting source as a canonical [Title, Year, DOI/no-doi] tag.

        Uses the primary source (first in the deterministic ordering) so that
        every claim line carries exactly one verifiable citation. Returns an
        empty string when the claim has no supporting sources (e.g. aggregate
        scalar counts with no publication-level provenance).
        """
        if not self.sources:
            return ""
        ref = self.sources[0]
        return format_citation(ref.title, ref.year, ref.doi)


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
    #: Which generator produced ``sql_executed`` — ``"deterministic"`` (rule
    #: template with bound parameters) or ``"llm"`` (Ollama Text-to-SQL).
    #: The two paths differ by two orders of magnitude in measured latency
    #: (84-90 ms vs 6,036 ms warm / 21.9-41.4 s cold), so a request that fell
    #: through to the LLM is otherwise indistinguishable from a slow database.
    sql_source: Optional[Literal["deterministic", "llm"]] = None
    #: Typed entity contract extracted from the question (year_filter,
    #: country, author_name, institution_name, keyword, topic_name,
    #: document_type) — the router's structured output, not model reasoning.
    entities: Optional[Dict[str, Any]] = None
    #: VectorRoute gate diagnostics: embedding model/dimension/backend,
    #: ANN candidate window and rows, rows after the cosine threshold, unique
    #: publications, and ``top_similarity`` even when the gate dropped every
    #: row. Lets a zero-evidence answer be classified as corpus-absence versus
    #: gate-miscalibration instead of collapsing into "data not found".
    vector_diagnostics: Optional[Dict[str, Any]] = None
    #: Why the request returned zero evidence, when it did. One of:
    #: ``entity_not_found`` | ``entity_needs_clarification`` | ``no_candidates``
    #: | ``below_vector_threshold`` | ``empty_result_set`` | ``unhandled_route``.
    #: The user-facing answer stays concise; this keeps the internal failure
    #: class distinguishable.
    zero_evidence_class: Optional[str] = None
    scored_chunks: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Deduped vector matches for inspection (VectorRoute only): "
        "publication_id, title, year, doi, chunk_id, similarity_score",
    )
    embedding_backend: Optional[str] = Field(
        None,
        description="Which query-embedding backend served the request "
        '("local" | "ollama", VectorRoute only; Phase 4 audit D1)',
    )
    synthesis_backend: Optional[str] = Field(
        None,
        description="Which synthesis engine produced the answer "
        '("deterministic" | "llm" | "deterministic-fallback"; Fase 7 B1)',
    )
    evidence_set: Optional[Dict[str, Any]] = None
    #: Which filter keys were auto-filled from session context because the
    #: current question left them unspecified. Reported so an inherited scope
    #: is visible rather than silently narrowing the answer — an auto-applied
    #: filter a user cannot see is indistinguishable, from the outside, from a
    #: wrong answer.
    session_filters_applied: Optional[List[str]] = None
    #: Rendered conversation block handed to the LLM narration step. Untrusted
    #: conversational recall, never evidence. Only populated when a session is
    #: attached AND llm_synthesis=true AND developer_mode=true.
    session_context_used: Optional[bool] = None


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
    session_id: Optional[UUID] = Field(
        None,
        description="Echo dari session_id permintaan bila sesi terlampir. "
        "Hanya penanda percakapan; tidak memuat fakta bibliometrik apa pun.",
    )
