"""Live-DB validation for Phase 4 vector storage (HNSW + cosine probe).

Docs Reference: docs/05 Retrieval Rag Design.md 5.2, docs/11 Roadmap.md
(Fase 1 acceptance + Fase 4 runtime checklist), docs/03 3 (NFR1).

Live-DB suite: skipped cleanly in CI where no database credentials exist --
same gating convention as ``tests/test_phase1_validation.py``. These tests
turn the Phase 4 audit runtime checklist (audit M-status: "checklist
runtime live-DB belum artefak") into executable artefacts:

1. ``chunks.embedding`` completeness (NULL count = 0).
2. HNSW index presence + index-scan proof via EXPLAIN on the **exact statement
   the request path executes**, obtained from ``VectorRetriever.build_query``
   rather than a hand-copied paraphrase. The earlier version of this file
   EXPLAINed a simplified query with ``enable_seqscan = off`` while the real
   query led its sort with ``publication_id``, which no HNSW index can serve:
   the probe passed while the production plan could not use the index.
3. Correctness of the returned set (distinct publications, score ordering).
4. Cosine probe latency within the VectorRoute retrieval budget.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection

from backend.app.services.retrievers.vector_retriever import (
    COSINE_SIMILARITY_THRESHOLD,
    VECTOR_TOP_K,
    VectorRetriever,
)

_LIVE_DB_MISSING = os.environ.get("DB_URL_OWNER") is None and os.environ.get("DB_URL") is None
pytestmark = pytest.mark.skipif(
    _LIVE_DB_MISSING,
    reason="Live PostgreSQL required (neither $DB_URL_OWNER nor $DB_URL is set)",
)


def _vector_extension_schema(cur) -> str:
    """Detect the schema holding the pgvector extension (Supabase: extensions)."""
    cur.execute(
        """
        SELECT n.nspname
        FROM pg_extension e
        JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector';
        """
    )
    row = cur.fetchone()
    assert row is not None, "pgvector extension 'vector' is not installed."
    return row[0]


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


def _probe_vector(cur) -> list[float]:
    """Reuse a stored embedding as the probe vector, so no model load is needed.

    The pgvector text output (``[0.1,0.2,...]``) is valid JSON, so it round-trips
    through ``json.loads`` into the float sequence ``build_query`` expects.
    """
    cur.execute("SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 1;")
    row = cur.fetchone()
    assert row is not None, "No embedded chunk available as probe."
    return [float(v) for v in json.loads(row[0])]


def _production_query(probe_vector: list[float]) -> tuple[str, list[object]]:
    """The exact statement VectorRetriever.retrieve executes for this probe."""
    sql, params, _ignored = VectorRetriever.build_query(
        query_vector=probe_vector,
        threshold=COSINE_SIMILARITY_THRESHOLD,
        limit=VECTOR_TOP_K,
    )
    return sql, params


def test_live_chunks_embedding_complete():
    """Runtime checklist: no chunk may lack an embedding (gate input invariant)."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;")
        null_row = cur.fetchone()
        assert null_row is not None
        null_cnt = null_row[0]
        cur.execute("SELECT COUNT(*) FROM chunks;")
        total_row = cur.fetchone()
        assert total_row is not None
        total_cnt = total_row[0]
    assert total_cnt > 0, "chunks table is empty; cannot validate vector storage."
    assert null_cnt == 0, f"{null_cnt}/{total_cnt} chunks lack embeddings."


def test_live_ann_window_ordering_is_hnsw_compatible():
    """The ANN window must be ordered by the cosine operator, nothing else.

    This is the property that decides whether HNSW is usable at all: pgvector's
    index only supplies rows pre-sorted by distance, so any leading sort key
    other than ``embedding <=> $1`` (the pre-fix shape led with
    ``publication_id``) forces a full scan plus top-N sort. Asserted against the
    live statement so it cannot drift from production.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        _vector_extension_schema(cur)
        sql, _params = _production_query(_probe_vector(cur))

    ann_block = sql.split("scored_chunks")[0]
    assert "<=>" in ann_block, "ANN window no longer references the cosine operator."
    assert "DISTINCT ON" not in ann_block, "Dedup leaked back into the ANN window."
    # The distance expression is the whole ORDER BY of the ANN window.
    order_by = ann_block.rsplit("ORDER BY", 1)[1].split("LIMIT", 1)[0].strip()
    assert order_by == (
        "(c.embedding OPERATOR(extensions.<=>) $1::extensions.vector) ASC"
    )


def test_live_hnsw_scan_serves_the_ann_window_shape():
    """EXPLAIN proof that an operator-ordered, LIMIT-bounded scan over chunks
    uses idx_chunks_embedding_hnsw — the exact access pattern the ANN window
    depends on, with the publication join stripped.

    ``enable_seqscan = off`` is needed because the prototype holds 40 chunks,
    where a sequential scan is genuinely cheaper and the planner picks it
    without help. It is not masking a wrong sort key: the ORDER BY here is the
    cosine operator alone, so the only index that can satisfy it is the HNSW one.

    Note the deliberate limit of this assertion: it proves the ANN window is
    index-serviceable, not that PostgreSQL *chooses* HNSW for the full request
    today. It does not, and should not, at 40 chunks. The planner switches once
    the corpus is large enough for the index scan to beat a sequential scan
    plus sort.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        vec_schema = _vector_extension_schema(cur)
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'chunks' AND indexname = 'idx_chunks_embedding_hnsw';"
        )
        assert cur.fetchone() is not None, "idx_chunks_embedding_hnsw is missing."

        probe_vec = _probe_vector(cur)
        ann_candidates = VectorRetriever.build_query(
            query_vector=probe_vec,
            threshold=0.65,
            limit=VECTOR_TOP_K,
        )[1][1]

        cur.execute("SET LOCAL enable_seqscan = off;")
        explain_query = (
            f"EXPLAIN SELECT c.chunk_id, "
            f"(c.embedding OPERATOR({vec_schema}.<=>) %(_p1)s::{vec_schema}.vector) AS distance "
            f"FROM chunks c WHERE c.embedding IS NOT NULL "
            f"ORDER BY (c.embedding OPERATOR({vec_schema}.<=>) %(_p1)s::{vec_schema}.vector) ASC "
            f"LIMIT %(_p2)s"
        )
        # NOTE: `cur` is a psycopg3/psycopg2 union cursor (see scripts/db.py).
        probe_literal = "[" + ",".join(f"{v:.8f}" for v in probe_vec) + "]"
        explain_params = {"_p1": probe_literal, "_p2": ann_candidates}
        cur.execute(explain_query, explain_params)  # type: ignore
        plan = "\n".join(r[0] for r in cur.fetchall())
    assert "idx_chunks_embedding_hnsw" in plan, (
        "HNSW index not used for the operator-ordered ANN scan. Plan:\n" + plan
    )


def test_live_production_query_returns_distinct_publications_in_score_order():
    """The dedup-after-ANN contract: at most ``VECTOR_TOP_K`` rows, one row per
    publication, ordered by descending similarity."""
    with get_db_connection() as conn, conn.cursor() as cur:
        sql, params = _production_query(_probe_vector(cur))
        cur.execute(_to_libpq(sql), _to_libpq_params(params))  # type: ignore
        rows = cur.fetchall()

    assert len(rows) <= 8, f"ANN retrieval returned {len(rows)} rows for a LIMIT of 8."
    pub_ids = [r[0] for r in rows]
    assert len(pub_ids) == len(set(pub_ids)), "DISTINCT ON dedup collapsed nothing."
    scores = [float(r[-1]) for r in rows]
    assert scores == sorted(scores, reverse=True), f"Scores not descending: {scores}"
    assert all(s >= 0.65 for s in scores), f"Score below the 0.65 gate leaked: {scores}"


def test_live_cosine_probe_latency_within_budget():
    """Cosine probe latency stays within the VectorRoute retrieval budget (NFR1: <=1.5s)."""
    with get_db_connection() as conn, conn.cursor() as cur:
        sql, params = _production_query(_probe_vector(cur))
        t0 = time.perf_counter()
        cur.execute(_to_libpq(sql), _to_libpq_params(params))  # type: ignore
        rows = cur.fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"\n[vector-live] cosine probe: {len(rows)} matches in {elapsed_ms:.1f}ms")
    assert elapsed_ms < 1500.0, f"Cosine probe took {elapsed_ms:.1f}ms (>1500ms NFR1 budget)."