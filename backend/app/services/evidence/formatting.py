"""Shared canonical formatting helpers for Evidence and answer synthesis.

Single source of truth for ``format_citation`` and ``format_period`` previously
duplicated in ``synthesizer/answer.py`` and ``evidence/unifier.py``.

Docs Reference: docs/05 Retrieval Rag Design.md §7 (citation format),
    docs/06 Api Design.md §5 (EvidenceObject period).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backend.app.models.ask import FilterParams

from backend.app.models.ask import format_citation as _canonical_format_citation


def format_citation(
    title: Optional[str], year: Optional[int], doi: Optional[str]
) -> str:
    """Format citation string adhering strictly to canonical [Title, Year, DOI/no-doi].

    Re-export of the single source of truth defined in
    ``backend.app.models.ask.format_citation``. Kept here so existing
    ``from backend.app.services.evidence.formatting import format_citation``
    call sites (unifier, synthesizer) remain importable unchanged.
    """
    return _canonical_format_citation(title, year, doi)


def format_period(filters: Optional["FilterParams"]) -> str:
    """Render the observation window, honoring exact year and ranges.

    Canonical mapping (user-facing):
    - exact ``year`` -> ``"2023"``
    - ``year_from`` + ``year_to`` -> ``"2020-2023"``
    - only ``year_from`` -> ``"{from}-present"``
    - only ``year_to`` -> ``"up-to-{to}"``
    - none -> ``"all-time"``
    """
    if not filters:
        return "all-time"
    if filters.year is not None:
        return str(filters.year)
    if filters.year_from is not None and filters.year_to is not None:
        return f"{filters.year_from}-{filters.year_to}"
    if filters.year_from is not None:
        return f"{filters.year_from}-present"
    if filters.year_to is not None:
        return f"up-to-{filters.year_to}"
    return "all-time"
