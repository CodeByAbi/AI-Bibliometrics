"""Post-hoc CitationVerifier service.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §5.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.logging import logger
from backend.app.models.ask import EvidenceSourceRef, SourceItem

# Standard canonical citation pattern: [Title, Year, DOI] or [Title, Year, no-doi]
CITATION_PATTERN = re.compile(
    r"\[([^,\[\]]+),\s*(\d{4}|n\.d\.),\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|no-doi)\]"
)


class CitationVerificationResult(BaseModel):
    """Output of post-hoc citation verification."""

    model_config = ConfigDict(frozen=True)

    cleaned_text: str
    verified_citations: List[str] = Field(default_factory=list)
    unverified_citations: List[str] = Field(default_factory=list)


def _normalize_title(title: str) -> str:
    """Normalize title for matching (lowercase, alphanumeric only)."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _titles_match(cite_norm: str, valid_norm: str) -> bool:
    """Strict title matching: exact equality or high token overlap.

    Replaces the former bidirectional substring check (which let a single
    token such as "Graph" match "Knowledge Graph Advances"). Single-token
    cites require exact equality; multi-token cites require Jaccard >= 0.8.
    """
    if not cite_norm or not valid_norm:
        return False
    if cite_norm == valid_norm:
        return True
    cite_tokens = set(cite_norm.split())
    valid_tokens = set(valid_norm.split())
    if len(cite_tokens) < 2 or len(valid_tokens) < 2:
        return False
    union = cite_tokens | valid_tokens
    if not union:
        return False
    return len(cite_tokens & valid_tokens) / len(union) >= 0.8


def _years_match(cite_year: Optional[int], evidence_year: Optional[int]) -> bool:
    """Strict year check: numeric years must be equal.

    Either side unknown (None / n.d.) defers to title matching alone so
    legitimate "n.d." citations are not rejected.
    """
    if cite_year is None or evidence_year is None:
        return True
    return cite_year == evidence_year


class CitationVerifier:
    """Deterministic regex-based citation verifier against retrieved EvidenceSet / Sources."""

    @classmethod
    def verify(
        cls,
        text: str,
        valid_sources: Sequence[Any],
    ) -> CitationVerificationResult:
        """Scan text for citations, verify against known valid sources, and strip fictitious ones.

        Parameters
        ----------
        text : str
            Narrative answer text containing citations.
        valid_sources : Sequence[Any]
            List of valid publication objects, SourceItems, EvidenceSourceRefs, or dicts.
        """
        if not text:
            return CitationVerificationResult(cleaned_text="", verified_citations=[], unverified_citations=[])

        # Build index of valid citations
        valid_dois: Set[str] = set()
        valid_titles: Set[str] = set()
        valid_title_years: Set[Tuple[str, Optional[int]]] = set()
        valid_doi_to_year: Dict[str, Optional[int]] = {}

        for src in valid_sources:
            doi = None
            title = None
            year = None

            if isinstance(src, dict):
                doi = src.get("doi")
                title = src.get("title")
                year = src.get("year")
            else:
                doi = getattr(src, "doi", None)
                title = getattr(src, "title", None)
                year = getattr(src, "year", None)

            norm_year = int(year) if year is not None else None
            if doi and str(doi).strip() and str(doi).strip().lower() != "no-doi":
                doi_key = str(doi).strip().lower()
                valid_dois.add(doi_key)
                valid_doi_to_year[doi_key] = norm_year
            if title:
                norm_t = _normalize_title(str(title))
                if norm_t:
                    valid_titles.add(norm_t)
                    valid_title_years.add((norm_t, norm_year))

        verified: List[str] = []
        unverified: List[str] = []

        def _replace_cite(match: re.Match) -> str:
            full_cite = match.group(0)
            cite_title = match.group(1).strip()
            cite_year_str = match.group(2).strip()
            cite_doi = match.group(3).strip()

            cite_year = int(cite_year_str) if cite_year_str.isdigit() else None
            norm_cite_title = _normalize_title(cite_title)

            # Verification rule 1: DOI + strict year match.
            # DOI alone is not enough: a correct DOI with a hallucinated
            # year must still be stripped (zero-hallucination invariant).
            if cite_doi.lower() != "no-doi" and cite_doi.lower() in valid_dois:
                evidence_year = valid_doi_to_year.get(cite_doi.lower())
                if _years_match(cite_year, evidence_year):
                    verified.append(full_cite)
                    return full_cite
                logger.warning("Stripped citation with DOI/year mismatch: %s", full_cite)
                unverified.append(full_cite)
                return ""

            # Verification rule 2: Title + strict year match.
            is_valid_title = False
            for vt, vy in valid_title_years:
                if _titles_match(norm_cite_title, vt) and _years_match(cite_year, vy):
                    is_valid_title = True
                    break

            if is_valid_title:
                verified.append(full_cite)
                return full_cite

            # Unverified / Hallucinated citation: strip from text and record
            logger.warning("Stripped unverified citation from answer: %s", full_cite)
            unverified.append(full_cite)
            return ""

        cleaned = CITATION_PATTERN.sub(_replace_cite, text)
        # Clean up any leftover double spaces
        cleaned = re.sub(r" +", " ", cleaned).strip()

        return CitationVerificationResult(
            cleaned_text=cleaned,
            verified_citations=verified,
            unverified_citations=unverified,
        )
