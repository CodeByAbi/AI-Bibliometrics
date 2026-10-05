"""Retrieval-quality regression gate for the VectorRoute cosine gate.

Runs the labelled benchmark in ``tests/fixtures/retrieval_benchmark_v1.json``
against the live corpus and asserts retrieval quality has not regressed below
the committed floor in ``tests/fixtures/retrieval_baseline_v1.json``.

Docs Reference: docs/05 Retrieval Rag Design.md 5.2, docs/11 Roadmap.md (Fase 9
retrieval quality tuning), reports/retrieval_calibration.md.

Live-DB + live-model suite: skipped cleanly where no database credentials
exist, matching ``tests/integration/test_vector_live_hnsw.py``. It needs both a
reachable database AND the ``BAAI/bge-m3`` weights; either missing skips rather
than passing, because a skipped retrieval gate proves nothing.

What this asserts, and why each one exists:

1. **Corpus fingerprint.** Labels are only meaningful against the corpus they
   were written for, so a corpus change fails loudly instead of silently
   rescoring labels against different documents.
2. **nDCG@K floor.** Primary ranking-quality aggregate. Guards against a change
   that quietly costs recall while leaving the obvious cases working.
3. **Negative false-positive rate == 0.** The load-bearing safety property.
   Recall may be traded for this only the other way round: a gate that admits a
   strict-absence negative fails even if its nDCG improves.
4. **Negative ceiling invariant.** Asserts the deployed gate is at or above the
   highest score any negative reached, which is *why* rule 3 holds.
5. **Per-category nDCG floors.** Stops a global average from hiding a
   single-category collapse, which is the failure mode a single scalar misses.
6. **before/after parity.** The metrics reported at the deployed gate must match
   the committed baseline, so a corpus edit cannot quietly move the numbers this
   file claims to protect.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bench_retrieval import (  # noqa: E402 - sys.path bootstrap
    aggregate,
    assert_corpus_fingerprint,
    fetch_full_ranking,
    gate_ranking,
    load_benchmark,
    query_metrics,
)
from scripts.db import get_db_connection  # noqa: E402

BENCHMARK_PATH = ROOT / "tests" / "fixtures" / "retrieval_benchmark_v1.json"
BASELINE_PATH = ROOT / "tests" / "fixtures" / "retrieval_baseline_v1.json"

_LIVE_DB_MISSING = (
    os.environ.get("DB_URL_OWNER") is None and os.environ.get("DB_URL") is None
)
pytestmark = pytest.mark.skipif(
    _LIVE_DB_MISSING,
    reason="Live PostgreSQL required (neither $DB_URL_OWNER nor $DB_URL is set)",
)

#: Absolute tolerance on before/after parity. Scores are cosine values rounded
#: to 4dp by the harness; anything larger than float noise means the corpus or
#: the embedding backend moved, which is what this test exists to catch.
PARITY_TOLERANCE = 1e-3


def _embedding_backend_available() -> bool:
    """True when a query-embedding backend can actually serve the benchmark."""
    import httpx

    try:
        from backend.app.core.config import get_settings
        from backend.app.services.embedding import local_embedding_model_state

        settings = get_settings()
        if local_embedding_model_state() in ("loaded", "loading"):
            return True
        # Local weights absent: only usable if an Ollama embedding model exists.
        url = f"{settings.ollama_host.rstrip('/')}/api/tags"
        response = httpx.get(url, timeout=3.0)
        response.raise_for_status()
        models = response.json().get("models", [])
        return any("bge" in str(m.get("name", "")).lower() for m in models)
    except (httpx.HTTPError, OSError, KeyError, TypeError, ValueError):
        # Availability probe: any failure means "cannot serve", which is exactly
        # what a skip guard needs. Never raise out of a skip decision.
        return False


requires_embeddings = pytest.mark.skipif(
    not _embedding_backend_available(),
    reason="No usable query-embedding backend (no local bge-m3 weights and no "
           "bge-m3 model on the Ollama endpoint)",
)


@pytest.fixture(scope="module")
def benchmark() -> dict:
    return load_benchmark(BENCHMARK_PATH)


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live_run(benchmark, baseline):
    """Execute the benchmark once at the deployed operating point.

    Module-scoped so the 94 encode calls (each ~100 ms on CPU, plus a one-off
    model load) are paid once for the whole module rather than per test.
    """
    import asyncio

    from backend.app.core.config import get_settings
    from backend.app.services.embedding import generate_query_embedding_with_backend

    settings = get_settings()
    gate = baseline["selected_threshold"]
    top_k = baseline["operational"]["top_k"]

    rankings: dict[str, list[tuple[str, float]]] = {}
    backends: set[str] = set()
    with get_db_connection() as conn, conn.cursor() as cur:
        assert_corpus_fingerprint(cur, benchmark["corpus_fingerprint"])
        for item in benchmark["queries"]:
            vector, backend = asyncio.run(
                generate_query_embedding_with_backend(item["query"])
            )
            backends.add(backend)
            rankings[item["id"]] = fetch_full_ranking(cur, vector)

    per_query = []
    per_category: dict[str, list] = {}
    for item in benchmark["queries"]:
        full = rankings[item["id"]]
        ranked = gate_ranking(full, gate, top_k)
        metrics = query_metrics(
            ranked, item["relevance"], top_k, full[0][1] if full else 0.0
        )
        metrics["has_answer"] = item["has_answer"]
        per_query.append(metrics)
        per_category.setdefault(item["category"], []).append(metrics)

    return {
        "gate": gate,
        "top_k": top_k,
        "embedding_backend": "+".join(sorted(backends)),
        "summary": aggregate(per_query, top_k),
        "by_category": {
            category: aggregate(rows, top_k)
            for category, rows in per_category.items()
        },
        "configured_gate": settings.vector_cosine_threshold,
        "configured_top_k": settings.vector_top_k,
    }


@requires_embeddings
def test_deployed_gate_matches_committed_selection(live_run, baseline):
    """The deployed configuration must be the gate the benchmark selected."""
    assert live_run["configured_gate"] == pytest.approx(
        live_run["gate"], abs=1e-9
    ), (
        f"deployed VECTOR_COSINE_THRESHOLD={live_run['configured_gate']} "
        f"differs from the benchmark-selected gate {live_run['gate']}; "
        "re-run scripts/bench_retrieval.py or restore the selected value"
    )
    assert live_run["configured_top_k"] == live_run["top_k"]


@requires_embeddings
def test_retrieval_does_not_regress_below_committed_floor(live_run, baseline):
    """nDCG@K and Hit@K must stay at or above the committed floor."""
    floor = baseline["regression_floor"]["selected_gate"]
    summary = live_run["summary"]
    assert summary["ndcg_at_k"] >= floor["ndcg_at_k"] - PARITY_TOLERANCE, (
        f"nDCG@{live_run['top_k']} regressed: {summary['ndcg_at_k']:.4f} < "
        f"floor {floor['ndcg_at_k']:.4f}"
    )
    assert summary["hit_at_k"] >= floor["hit_at_k"] - PARITY_TOLERANCE, (
        f"Hit@{live_run['top_k']} regressed: {summary['hit_at_k']:.4f} < "
        f"floor {floor['hit_at_k']:.4f}"
    )
    assert summary["precision_at_k"] >= floor["precision_at_k"] - PARITY_TOLERANCE


@requires_embeddings
def test_no_strict_absence_negative_is_admitted(live_run):
    """The load-bearing safety property: recall may never be bought with a FP.

    Negatives in the benchmark are labelled strict-absence, so every label is
    grade 0 and ANY returned publication is a false positive. A gate that admits
    one fails here even when its ranking metrics improve.
    """
    summary = live_run["summary"]
    assert summary["negative_fp_count"] == 0, (
        f"{summary['negative_fp_count']} of {summary['n_negative']} "
        "strict-absence negative queries returned evidence; the gate is below "
        "the negative ceiling and would fabricate an answer on absent topics"
    )


@requires_embeddings
def test_gate_sits_at_or_above_negative_ceiling(live_run):
    """Why the previous assertion holds: gate >= worst negative score."""
    summary = live_run["summary"]
    ceiling = summary["max_corpus_top_negative"]
    assert live_run["gate"] >= ceiling - PARITY_TOLERANCE, (
        f"gate {live_run['gate']} is below the highest score any strict-absence "
        f"negative reached ({ceiling}); negatives can now be admitted"
    )


@requires_embeddings
@pytest.mark.parametrize("category", [
    "natural_language", "paraphrase", "conceptual",
    "title_entity", "short", "negative",
])
def test_per_category_ndcg_does_not_regress(live_run, baseline, category):
    """No single category may collapse behind an acceptable global average."""
    floor = baseline["regression_floor"]["per_category_ndcg_floor"].get(category)
    if floor is None:
        pytest.skip(f"no committed floor for category '{category}'")
    committed = floor[next(iter(floor))]
    observed = live_run["by_category"][category]["ndcg_at_k"]
    assert observed >= committed - PARITY_TOLERANCE, (
        f"category '{category}' regressed: nDCG {observed:.4f} < floor "
        f"{committed:.4f} while the global average may still look acceptable"
    )


@requires_embeddings
def test_metrics_match_committed_baseline_within_tolerance(live_run, baseline):
    """before/after parity: the protected numbers must still be the real ones.

    Guards against a corpus or embedding-backend edit moving the metrics this
    baseline claims to represent, which would make every floor above vacuous.
    """
    committed = baseline["regression_floor"]["selected_gate"]
    observed = live_run["summary"]
    for field in (
        "ndcg_at_k", "precision_at_k", "hit_at_k", "mrr",
        "max_corpus_top_negative",
    ):
        assert abs(observed[field] - committed[field]) <= PARITY_TOLERANCE, (
            f"{field} drifted from the committed baseline: observed "
            f"{observed[field]}, committed {committed[field]}. If the corpus or "
            "embedding backend changed, re-run scripts/bench_retrieval.py and "
            "review the new baseline before committing it."
        )


@requires_embeddings
def test_benchmark_labels_stay_consistent_with_deployed_backend(
    live_run, baseline
):
    """The benchmark was calibrated on one embedding backend; pin it.

    The open fase-8 item B4 records that the Ollama bge-m3 fallback does not
    produce vectors identical to the local HF encoder. A different backend moves
    the whole score distribution, so a threshold calibrated on one backend is
    not valid on the other. Recorded rather than enforced as a hard failure,
    because the backend can legitimately change with no corpus change.
    """
    calibrated = baseline["operational"]["embedding_backend"]
    assert live_run["embedding_backend"] == calibrated, (
        f"benchmark was calibrated on embedding_backend='{calibrated}' but the "
        f"live run served '{live_run['embedding_backend']}'. The cosine gate is "
        "backend-specific (fase-8 B4); re-calibrate before trusting these metrics."
    )