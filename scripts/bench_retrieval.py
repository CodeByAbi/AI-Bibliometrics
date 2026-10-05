"""VectorRoute retrieval benchmark + cosine-gate threshold sweep (P1 calibration).

Runs a labelled query set against the live ``chunks`` corpus through the exact
statement ``VectorRetriever`` executes, scores Precision@K / Recall@K / Hit@K /
MRR / nDCG@K per candidate gate, and writes a reproducible JSON + Markdown
artifact pair.

Usage:
    python scripts/bench_retrieval.py
    python scripts/bench_retrieval.py --thresholds 0.50 0.55 --no-verify
    python scripts/bench_retrieval.py --tag sweep_v2

Docs Reference: docs/05 Retrieval Rag Design.md 5.2, docs/11 Roadmap.md (Fase 9
retrieval quality tuning), tests/fixtures/retrieval_benchmark_v1.json.

Why one query per benchmark item instead of one per (item, threshold)
---------------------------------------------------------------------
Each item is embedded once and the production statement is executed once with
the gate opened (``threshold=0.0``) and ``limit=MAX_LIMIT`` so every distinct
publication arrives with its similarity score. Candidate gates are then applied
in Python.

That is exact, not an approximation. ``scored_chunks`` filters on the gate
*before* ``DISTINCT ON (publication_id)``, so a publication survives only if its
minimum-distance chunk clears the gate -- and the minimum-distance chunk is the
same row whether or not the gate is applied. Ranking and truncation are then
applied identically. ``--verify`` re-runs the untouched production statement at
the selected gate for a sample of items and asserts the simulated set matches.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pathlib
import re
import statistics
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ruff: noqa: E402 - the sys.path bootstrap must precede the project imports.

from backend.app.services.retrievers.vector_retriever import (
    MAX_LIMIT,
    VectorRetriever,
)
from scripts.db import get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bench_retrieval")

BENCHMARK_PATH = ROOT / "tests" / "fixtures" / "retrieval_benchmark_v1.json"
OUT_DIR = ROOT / "reports" / "retrieval_benchmark"

DEFAULT_THRESHOLDS: tuple[float, ...] = (
    0.44, 0.45, 0.46, 0.47, 0.48, 0.49, 0.50, 0.51, 0.52, 0.53, 0.54,
    0.55, 0.60, 0.65, 0.70, 0.75,
)
#: The default grid is dense where the decision actually lives. The coarse pass
#: (0.45 / 0.50 / 0.55 / 0.60 / 0.65 / 0.70 / 0.75) located the negative-query
#: false-positive boundary between 0.45 (6 of 15 negatives leaked) and 0.50
#: (none did), so the committed default refines that interval at 0.01 steps and
#: keeps the coarse points above 0.55 to show the ranking-quality collapse.
REFINE_THRESHOLDS: tuple[float, ...] = tuple(
    round(0.44 + 0.01 * step, 2) for step in range(12)
)

#: Grades below this are treated as "not relevant" for binary metrics.
RELEVANT_MIN_GRADE = 1


def load_benchmark(path: pathlib.Path) -> dict[str, Any]:
    """Load the labelled benchmark and validate its structural invariants."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    required = {"benchmark_version", "granularity", "queries"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"benchmark is missing required keys: {sorted(missing)}")

    seen: set[str] = set()
    for item in data["queries"]:
        for key in ("id", "query", "category", "has_answer", "relevance"):
            if key not in item:
                raise ValueError(f"benchmark item missing '{key}': {item!r}")
        if item["id"] in seen:
            raise ValueError(f"duplicate benchmark id: {item['id']}")
        seen.add(item["id"])
        if not item["has_answer"] and any(
            grade >= RELEVANT_MIN_GRADE for grade in item["relevance"].values()
        ):
            raise ValueError(
                f"negative item {item['id']} carries a relevant grade; the "
                "false-positive metric assumes strict-absence negatives"
            )
    return data


def assert_corpus_fingerprint(
    cur: Any, fingerprint: dict[str, Any]
) -> tuple[int, int, int]:
    """Verify the live corpus still matches the labelled corpus.

    Labels are only meaningful against the corpus they were written for, so a
    silent corpus change must invalidate the benchmark rather than quietly
    rescore it.
    """
    cur.execute("SELECT COUNT(*) FROM public.publications;")
    n_pubs = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM public.chunks;")
    n_chunks = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM public.chunks WHERE embedding IS NOT NULL;")
    n_embedded = int(cur.fetchone()[0])

    expected_pubs = int(fingerprint.get("publications", n_pubs))
    expected_chunks = int(fingerprint.get("chunks", n_chunks))
    if (n_pubs, n_chunks) != (expected_pubs, expected_chunks):
        raise ValueError(
            "corpus fingerprint mismatch: benchmark was labelled against "
            f"{expected_pubs} publications / {expected_chunks} chunks but the "
            f"live corpus has {n_pubs} / {n_chunks}. Re-label the benchmark "
            "before trusting these metrics."
        )
    if n_embedded != n_chunks:
        raise ValueError(
            f"{n_chunks - n_embedded} chunk(s) have no embedding; VectorRoute "
            "would silently skip them and the benchmark would overstate recall."
        )
    return n_pubs, n_chunks, n_embedded


def _fetch_dicts(cur: Any, sql: str, named: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute ``sql`` and return rows as dicts keyed by column name.

    Column names come from ``cursor.description`` rather than fixed offsets, so
    the harness keeps working if ``build_query`` reorders or extends its
    projection. Works on both psycopg3 and psycopg2, whose default row factory
    is a plain tuple.
    """
    cur.execute(sql, named)
    columns = [column[0] for column in cur.description]
    return [
        dict(zip(columns, row, strict=True)) for row in cur.fetchall()
    ]


def _to_libpq(sql: str) -> str:
    """Rewrite asyncpg ``$n`` placeholders to psycopg named placeholders.

    Named rather than positional because ``$1`` (the query vector) appears more
    than once in the ANN window: it is referenced by both the distance
    projection and the distance ordering. A positional ``%s`` rewrite would
    count that repetition as extra placeholders.
    """
    return re.sub(r"\$(\d+)", lambda m: f"%(_p{m.group(1)})s", sql)


def _to_libpq_params(params: list[object]) -> dict[str, object]:
    """Positional params -> the named mapping :func:`_to_libpq` expects."""
    return {f"_p{i + 1}": value for i, value in enumerate(params)}


def fetch_full_ranking(cur: Any, query_vector: list[float]) -> list[tuple[str, float]]:
    """Return ``(publication_id, similarity)`` for every distinct publication.

    Executes the production statement with the gate opened and the result limit
    at its maximum, so the full score distribution is available for offline gate
    simulation.
    """
    sql, params, _ = VectorRetriever.build_query(
        query_vector=query_vector, threshold=0.0, limit=MAX_LIMIT
    )
    rows = _fetch_dicts(cur, _to_libpq(sql), _to_libpq_params(params))
    ranked = [
        (str(row["publication_id"]), float(row["similarity_score"]))
        for row in rows
        if row["publication_id"] is not None
    ]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


def gate_ranking(
    ranking: list[tuple[str, float]], threshold: float, top_k: int
) -> list[tuple[str, float]]:
    """Apply the cosine gate and the top-K publication limit to a ranking."""
    return [(pid, score) for pid, score in ranking if score >= threshold][:top_k]


def dcg(grades: list[int]) -> float:
    """Discounted cumulative gain over a graded ranking (0-based positions)."""
    return sum(
        grade / math.log2(position + 2) for position, grade in enumerate(grades)
    )


def query_metrics(
    ranked: list[tuple[str, float]],
    relevance: dict[str, int],
    top_k: int,
    corpus_top_similarity: float,
) -> dict[str, float]:
    """Compute the retrieval metrics for one query at one operating point.

    ``corpus_top_similarity`` is the best score in the corpus *before* the gate,
    so a negative query's margin to the nearest (wrong) chunk stays visible at
    gates where the gate correctly drops it. Without it a correctly-suppressed
    negative reports 0.0 and looks further from the boundary than it is.
    """
    retrieved = [pid for pid, _ in ranked[:top_k]]
    grades = [int(relevance.get(pid, 0)) for pid in retrieved]
    n_relevant = sum(1 for grade in relevance.values() if grade >= RELEVANT_MIN_GRADE)
    hits = sum(1 for grade in grades if grade >= RELEVANT_MIN_GRADE)

    precision = hits / len(grades) if grades else 0.0

    first_hit = next(
        (position for position, grade in enumerate(grades, 1)
         if grade >= RELEVANT_MIN_GRADE),
        0,
    )
    recall = (hits / n_relevant) if n_relevant else 0.0
    recall_ceiling = (
        (min(top_k, n_relevant) / n_relevant) if n_relevant else 0.0
    )
    ideal = sorted(
        (int(g) for g in relevance.values()), reverse=True
    )[:top_k]
    idcg = dcg(ideal)
    ndcg = (dcg(grades) / idcg) if idcg > 0 else 0.0

    return {
        "precision_at_k": round(precision, 4),
        "recall_at_k": round(recall, 4),
        "recall_ceiling": round(recall_ceiling, 4),
        "hit_at_k": 1.0 if hits else 0.0,
        "mrr": round(1.0 / first_hit, 4) if first_hit else 0.0,
        "ndcg_at_k": round(ndcg, 4),
        "retrieved_count": float(len(retrieved)),
        "relevant_count": float(n_relevant),
        "top_similarity": round(ranked[0][1], 4) if ranked else 0.0,
        "corpus_top_similarity": round(corpus_top_similarity, 4),
    }


def aggregate(per_query: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    """Average per-query metrics, separating negatives for the FP rate."""
    positives = [row for row in per_query if row["has_answer"]]
    negatives = [row for row in per_query if not row["has_answer"]]

    def mean(rows: list[dict[str, Any]], field: str) -> float:
        if not rows:
            return 0.0
        return round(statistics.fmean(row[field] for row in rows), 4)

    summary: dict[str, Any] = {
        "n_queries": len(per_query),
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "ndcg_at_k": mean(positives, "ndcg_at_k"),
        "precision_at_k": mean(positives, "precision_at_k"),
        "recall_at_k": mean(positives, "recall_at_k"),
        "recall_ceiling": mean(positives, "recall_ceiling"),
        "hit_at_k": mean(positives, "hit_at_k"),
        "mrr": mean(positives, "mrr"),
        "mean_top_similarity_positive": mean(positives, "top_similarity"),
        "mean_returned_positive": mean(positives, "retrieved_count"),
        "mean_corpus_top_positive": mean(positives, "corpus_top_similarity"),
    }
    if negatives:
        fp_queries = sum(1 for row in negatives if row["retrieved_count"] > 0)
        summary.update(
            {
                "negative_fp_rate": round(fp_queries / len(negatives), 4),
                "negative_fp_count": fp_queries,
                "mean_top_similarity_negative": mean(negatives, "top_similarity"),
                "mean_returned_negative": mean(negatives, "retrieved_count"),
                "mean_corpus_top_negative": mean(
                    negatives, "corpus_top_similarity"
                ),
                "max_corpus_top_negative": round(
                    max(
                        (row["corpus_top_similarity"] for row in negatives),
                        default=0.0,
                    ),
                    4,
                ),
            }
        )
    else:
        summary.update(
            {
                "negative_fp_rate": 0.0,
                "negative_fp_count": 0,
                "mean_top_similarity_negative": 0.0,
                "mean_returned_negative": 0.0,
                "mean_corpus_top_negative": 0.0,
                "max_corpus_top_negative": 0.0,
            }
        )
    return summary


def select_threshold(
    sweep: dict[str, dict[str, Any]], rule_tolerance: float = 0.02
) -> tuple[float | None, str]:
    """Apply the pre-registered selection rule to the sweep.

    1. Discard any gate whose negative false-positive rate is above zero.
    2. Among survivors take the highest nDCG@K.
    3. Tie-break within ``rule_tolerance`` nDCG toward the HIGHER gate, so a
       marginal ranking gain never buys extra noise.

    Returns ``(threshold, rationale)``.
    """
    viable = {
        threshold: row
        for threshold, row in sweep.items()
        if row["negative_fp_rate"] == 0
    }
    if not viable:
        return None, (
            "No candidate gate reached a zero negative false-positive rate, so "
            "the pre-registered rule admits no gate. Keep the canonical 0.65 "
            "and treat recall as unachievable at this corpus size."
        )

    best_ndcg = max(row["ndcg_at_k"] for row in viable.values())
    contenders = sorted(
        threshold
        for threshold, row in viable.items()
        if best_ndcg - row["ndcg_at_k"] <= rule_tolerance
    )
    chosen_key = contenders[-1]
    chosen = float(chosen_key)
    rationale = (
        f"Rule 1 kept {sorted(viable)} (negative FP rate == 0). "
        f"Rule 2 best nDCG@K = {best_ndcg:.4f}. "
        f"Rule 3 tie-break within {rule_tolerance} nDCG over "
        f"{contenders} selected the highest gate, {chosen_key} "
        f"(nDCG@K {viable[chosen_key]['ndcg_at_k']:.4f}, "
        f"FP rate {viable[chosen_key]['negative_fp_rate']:.4f})."
    )
    return chosen, rationale


def verify_simulation(
    conn: Any, item: dict[str, Any], query_vector: list[float],
    threshold: float, top_k: int, simulated: list[tuple[str, float]]
) -> None:
    """Re-run the untouched production statement and assert set equality."""
    sql, params, _ = VectorRetriever.build_query(
        query_vector=query_vector, threshold=threshold, limit=top_k
    )
    with conn.cursor() as cur:
        rows = _fetch_dicts(cur, _to_libpq(sql), _to_libpq_params(params))
    actual = [
        str(row["publication_id"])
        for row in rows
        if row["publication_id"] is not None
    ]
    expected = [pid for pid, _ in simulated]
    if actual != expected:
        raise AssertionError(
            f"simulation diverged from production SQL for {item['id']}: "
            f"expected {expected}, production returned {actual}"
        )


def render_markdown(
    sweep: dict[str, dict[str, Any]],
    per_category: dict[str, dict[str, dict[str, Any]]],
    top_k: int,
    selected: float | None,
    rationale: str,
    meta: dict[str, Any],
) -> str:
    """Render the sweep as Markdown tables for human review."""
    lines: list[str] = []
    lines.append(f"# Threshold Sweep - VectorRoute cosine gate (top_k={top_k})\n")
    lines.append(
        f"- benchmark: `{BENCHMARK_PATH.name}` "
        f"(v{meta['benchmark_version']}, {meta['n_queries']} queries)"
    )
    lines.append(f"- embedding model: `{meta['embedding_model']}`")
    lines.append(f"- embedding backend: `{meta['embedding_backend']}`")
    lines.append(f"- corpus: {meta['corpus_publications']} publications / "
                 f"{meta['corpus_chunks']} chunks")
    lines.append(f"- generated: {meta['timestamp']}\n")

    lines.append("## Global sweep\n")
    lines.append(
        f"| Threshold | nDCG@{top_k} | Precision@{top_k} | Hit@{top_k} | "
        f"MRR@{top_k} | Recall@{top_k} (ceiling) | Neg FP rate | "
        f"max corpus top (neg) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for threshold in sorted(sweep):
        row = sweep[threshold]
        gate = float(threshold)
        is_selected = selected is not None and abs(gate - selected) < 1e-9
        marker = " **<-- selected**" if is_selected else ""
        lines.append(
            f"| {gate:.2f}{marker} | {row['ndcg_at_k']:.4f} | "
            f"{row['precision_at_k']:.4f} | {row['hit_at_k']:.4f} | "
            f"{row['mrr']:.4f} | {row['recall_at_k']:.4f} "
            f"({row['recall_ceiling']:.4f}) | "
            f"{row['negative_fp_rate']:.4f} "
            f"({row['negative_fp_count']}/{row['n_negative']}) | "
            f"{row['max_corpus_top_negative']:.4f} |"
        )
    lines.append(
        "\n`max corpus top (neg)` is the highest score any strict-absence "
        "negative reached in the corpus before gating. A gate at or above that "
        "value cannot admit a negative; a gate below it can.\n"
    )

    lines.append("\n## Selection (pre-registered rule)\n")
    lines.append(f"{rationale}\n")

    lines.append(f"\n## By category (nDCG@{top_k} / Hit@{top_k})\n")
    categories = sorted(per_category)
    header = "| Threshold | " + " | ".join(categories) + " |"
    lines.append(header)
    lines.append("|---" * (len(categories) + 1) + "|")
    for threshold in sorted(per_category[categories[0]]):
        cells = []
        for category in categories:
            row = per_category[category][threshold]
            fp = row.get("negative_fp_rate")
            fp_txt = "" if fp is None else f" (fp {fp:.2f})"
            cells.append(
                f"{row['ndcg_at_k']:.3f}/{row['hit_at_k']:.2f}{fp_txt}"
            )
        lines.append(f"| {float(threshold):.2f} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=pathlib.Path, default=BENCHMARK_PATH)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--tag", default="sweep_v1")
    parser.add_argument(
        "--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS)
    )
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--verify", action="store_true", default=True,
        help="Re-run production SQL at the selected gate and assert equality.",
    )
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument(
        "--verify-sample", type=int, default=8,
        help="How many benchmark items to cross-check against production SQL.",
    )
    args = parser.parse_args()

    import asyncio

    from backend.app.core.config import get_settings
    from backend.app.services.embedding import (
        generate_query_embedding_with_backend,
    )

    settings = get_settings()
    benchmark = load_benchmark(args.benchmark)
    queries: list[dict[str, Any]] = benchmark["queries"]

    logger.info(
        "Loaded benchmark v%s: %d queries (%d positive, %d negative)",
        benchmark["benchmark_version"],
        len(queries),
        sum(1 for q in queries if q["has_answer"]),
        sum(1 for q in queries if not q["has_answer"]),
    )

    # Collect full rankings once; gates are simulated offline from these.
    rankings: dict[str, list[tuple[str, float]]] = {}
    embedding_backends: set[str] = set()
    encode_ms: list[float] = []
    started = time.perf_counter()
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            assert_corpus_fingerprint(cur, benchmark["corpus_fingerprint"])

            for item in queries:
                t0 = time.perf_counter()
                vector, backend = asyncio.run(
                    generate_query_embedding_with_backend(item["query"])
                )
                encode_ms.append((time.perf_counter() - t0) * 1000)
                embedding_backends.add(backend)
                rankings[item["id"]] = fetch_full_ranking(cur, vector)
                if item is queries[0] or item["id"] == queries[-1]["id"]:
                    logger.info(
                        "  %s top3=%s",
                        item["id"],
                        [
                            (pid, round(score, 4))
                            for pid, score in rankings[item["id"]][:3]
                        ],
                    )

        thresholds = sorted(set(args.thresholds))
        sweep: dict[str, dict[str, Any]] = {}
        per_category: dict[str, dict[str, dict[str, Any]]] = {}
        raw: dict[str, dict[str, Any]] = {}

        for threshold in thresholds:
            per_query: list[dict[str, Any]] = []
            raw[str(threshold)] = {}
            for item in queries:
                ranked = gate_ranking(
                    rankings[item["id"]], threshold, args.top_k
                )
                full = rankings[item["id"]]
                metrics = query_metrics(
                    ranked,
                    item["relevance"],
                    args.top_k,
                    full[0][1] if full else 0.0,
                )
                metrics["has_answer"] = item["has_answer"]
                per_query.append(metrics)
                raw[str(threshold)][item["id"]] = {
                    "category": item["category"],
                    "has_answer": item["has_answer"],
                    "results": [
                        {
                            "publication_id": pid,
                            "similarity": round(score, 4),
                            "rank": position,
                            "relevant": int(
                                item["relevance"].get(pid, 0)
                            ) >= RELEVANT_MIN_GRADE,
                        }
                        for position, (pid, score) in enumerate(ranked, 1)
                    ],
                    "metrics": metrics,
                }

            sweep[f"{threshold:.2f}"] = aggregate(per_query, args.top_k)

            for category in sorted({item["category"] for item in queries}):
                subset = [
                    row
                    for row, item in zip(per_query, queries, strict=True)
                    if item["category"] == category
                ]
                # Keyed category-first so the artifact reads
                # by_category[category][threshold] like the rendered table.
                per_category.setdefault(category, {})[f"{threshold:.2f}"] = (
                    aggregate(subset, args.top_k)
                )

        selected, rationale = select_threshold(sweep)
        logger.info("Selection: %s (threshold=%s)", rationale, selected)

        verified = 0
        if args.verify and selected is not None:
            sample = [
                item for item in queries
                if item["has_answer"]
            ][: args.verify_sample]
            sample += [item for item in queries if not item["has_answer"]][
                : max(1, args.verify_sample // 2)
            ]
            for item in sample:
                vector, _ = asyncio.run(
                    generate_query_embedding_with_backend(item["query"])
                )
                verify_simulation(
                    conn, item, vector, selected, args.top_k,
                    gate_ranking(rankings[item["id"]], selected, args.top_k),
                )
                verified += 1
            logger.info(
                "Verification passed: %d items match production SQL at "
                "threshold=%s", verified, selected,
            )

    meta = {
        "benchmark_version": benchmark["benchmark_version"],
        "benchmark_file": args.benchmark.name,
        "n_queries": len(queries),
        "n_positive_queries": sum(1 for q in queries if q["has_answer"]),
        "n_negative_queries": sum(1 for q in queries if not q["has_answer"]),
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "embedding_backend": "+".join(sorted(embedding_backends)),
        "corpus_publications": benchmark["corpus_fingerprint"]["publications"],
        "corpus_chunks": benchmark["corpus_fingerprint"]["chunks"],
        "top_k": args.top_k,
        "granularity": benchmark["granularity"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(time.perf_counter() - started, 1),
        "mean_encode_ms": round(statistics.fmean(encode_ms), 1),
        "verification_items": verified,
        "thresholds": thresholds,
        "selected_threshold": selected,
        "selection_rationale": rationale,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": meta,
        "sweep": sweep,
        "by_category": per_category,
        "raw": raw,
    }
    json_path = args.out_dir / f"{args.tag}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_path = args.out_dir / f"{args.tag}.md"
    md_path.write_text(
        render_markdown(sweep, per_category, args.top_k, selected, rationale, meta),
        encoding="utf-8",
    )

    logger.info("Wrote %s", json_path.relative_to(ROOT))
    logger.info("Wrote %s", md_path.relative_to(ROOT))
    logger.info(
        "Selected threshold: %s | nDCG@%d %.4f | neg FP rate %.4f",
        selected, args.top_k,
        sweep[f"{selected:.2f}"]["ndcg_at_k"] if selected else float("nan"),
        sweep[f"{selected:.2f}"]["negative_fp_rate"] if selected else float("nan"),
    )


if __name__ == "__main__":
    main()