"""Live-DB validation for Phase 4 vector storage (HNSW + cosine probe).

Docs Reference: docs/05 Retrieval Rag Design.md §5.2, docs/11 Roadmap.md
(Fase 1 acceptance + Fase 4 runtime checklist), docs/03 §3 (NFR1).

Live-DB suite: skipped cleanly in CI where no database credentials exist —
same gating convention as ``tests/test_phase1_validation.py``. These tests
turn the Phase 4 audit runtime checklist (audit M-status: "checklist
runtime live-DB belum artefak") into executable artefacts:

1. ``chunks.embedding`` completeness (NULL count = 0).
2. HNSW index presence + index-scan proof via EXPLAIN on a ``<=>`` probe.
3. Cosine probe latency within the VectorRoute retrieval budget.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection

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


def test_live_hnsw_index_used_for_cosine_probe():
    """EXPLAIN proof that a cosine probe can use idx_chunks_embedding_hnsw.

    A stored embedding is reused as the probe vector, so the test needs no
    model load. ``enable_seqscan = off`` forces the planner to reveal index
    usability on the tiny prototype table (40 rows would otherwise seq-scan).
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        vec_schema = _vector_extension_schema(cur)
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'chunks' AND indexname = 'idx_chunks_embedding_hnsw';"
        )
        assert cur.fetchone() is not None, "idx_chunks_embedding_hnsw is missing."

        cur.execute(
            "SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 1;"
        )
        probe_row = cur.fetchone()
        assert probe_row is not None, "No embedded chunk available as probe."
        probe_vec = probe_row[0]

        cur.execute("SET LOCAL enable_seqscan = off;")
        explain_query: str = (
            f"EXPLAIN SELECT chunk_id, "
            f"(embedding OPERATOR({vec_schema}.<=>) '{probe_vec}'::{vec_schema}.vector) AS dist "
            f"FROM chunks ORDER BY dist ASC LIMIT 8"
        )
        # NOTE: `cur` is a psycopg3/psycopg2 union cursor (see scripts/db.py);
        # the f-string query matches the convention used across live tests.
        cur.execute(explain_query)  # type: ignore
        plan = "\n".join(r[0] for r in cur.fetchall())
    assert "idx_chunks_embedding_hnsw" in plan, (
        "HNSW index not used for cosine probe. Plan:\n" + plan
    )


def test_live_cosine_probe_latency_within_budget():
    """Cosine probe latency stays within the VectorRoute retrieval budget (NFR1: <=1.5s)."""
    with get_db_connection() as conn, conn.cursor() as cur:
        vec_schema = _vector_extension_schema(cur)
        cur.execute(
            "SELECT embedding::text FROM chunks WHERE embedding IS NOT NULL LIMIT 1;"
        )
        probe_row = cur.fetchone()
        assert probe_row is not None, "No embedded chunk available as probe."
        probe_vec = probe_row[0]

        t0 = time.perf_counter()
        probe_query: str = (
            f"SELECT DISTINCT ON (p.publication_id) p.publication_id, "
            f"1 - (c.embedding OPERATOR({vec_schema}.<=>) "
            f"'{probe_vec}'::{vec_schema}.vector) AS similarity_score "
            f"FROM chunks c JOIN publications p ON p.publication_id = c.publication_id "
            f"WHERE c.embedding IS NOT NULL "
            f"AND (1 - (c.embedding OPERATOR({vec_schema}.<=>) "
            f"'{probe_vec}'::{vec_schema}.vector)) >= 0.65 "
            f"ORDER BY p.publication_id LIMIT 8"
        )
        # NOTE: union cursor typing, see comment above.
        cur.execute(probe_query)  # type: ignore
        rows = cur.fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"\n[vector-live] cosine probe: {len(rows)} matches in {elapsed_ms:.1f}ms")
    assert elapsed_ms < 1500.0, f"Cosine probe took {elapsed_ms:.1f}ms (>1500ms NFR1 budget)."
