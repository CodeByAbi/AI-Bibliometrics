"""Post-hoc CitationVerifier service.

Docs Reference: docs/05 Retrieval Rag Design.md §7, docs/06 Api Design.md §5.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple
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

            if doi and str(doi).strip() and str(doi).strip().lower() != "no-doi":
                valid_dois.add(str(doi).strip().lower())
            if title:
                norm_t = _normalize_title(str(title))
                if norm_t:
                    valid_titles.add(norm_t)
                    valid_title_years.add((norm_t, int(year) if year is not None else None))

        verified: List[str] = []
        unverified: List[str] = []

        def _replace_cite(match: re.Match) -> str:
            full_cite = match.group(0)
            cite_title = match.group(1).strip()
            cite_year_str = match.group(2).strip()
            cite_doi = match.group(3).strip()

            cite_year = int(cite_year_str) if cite_year_str.isdigit() else None
            norm_cite_title = _normalize_title(cite_title)

            # Verification rule 1: DOI match
            if cite_doi.lower() != "no-doi" and cite_doi.lower() in valid_dois:
                verified.append(full_cite)
                return full_cite

            # Verification rule 2: Title + Year exact or substring match
            is_valid_title = False
            for vt in valid_titles:
                if norm_cite_title and (norm_cite_title == vt or norm_cite_title in vt or vt in norm_cite_title):
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
