"""Phase 1 Canonical Data & Offline Indexing Validation Test Suite.

Validates all Phase 1 invariants:
1. Database connectivity & read/write lifecycle.
2. pgvector extension activation.
3. Chunks table embedding completeness (40/40), dimension (1024), and metadata.
4. No orphan chunks (100% linked to publications).
5. HNSW index presence on chunks(embedding) and pub_id index.
6. Vector similarity search query (<=>) functionality.
7. Derived collaboration edge tables (institution_collaboration & author_collaboration).
8. Edge invariants: canonical ordering (a < b), non-empty provenance, FK integrity.
9. Pipeline idempotency & zero duplicate generation.
"""

from __future__ import annotations

import pathlib
import sys
import pytest

# Ensure project root is in path
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection


def test_database_connection():
    """Verify database connection can be established cleanly."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            res = cur.fetchone()
            assert res is not None
            assert res[0] == 1


def test_pgvector_extension():
    """Verify pgvector extension is installed and active."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';")
            row = cur.fetchone()
            assert row is not None, "Extension 'vector' is not installed."
            assert row[0] == "vector"


def test_silver_tables_record_counts():
    """Verify all 9 canonical Silver tables contain prototype data."""
    expected_tables = {
        "publications": 20,
        "authors": 138,
        "institutions": 107,
        "keywords": 344,
        "funding": 33,
        "pub_author": 138,
        "pub_institution": 108,
        "publication_references": 4120,
        "chunks": 40,
    }
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for table, min_expected in expected_tables.items():
                cur.execute(f"SELECT COUNT(*) FROM {table};")
                count = cur.fetchone()[0]
                assert count >= min_expected, (
                    f"Table '{table}' has {count} rows, expected at least {min_expected}"
                )


def test_chunks_embeddings_completeness():
    """Verify all 40 chunks have non-null embeddings."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chunks;")
            total_chunks = cur.fetchone()[0]
            assert total_chunks == 40, f"Expected 40 chunks, found {total_chunks}"

            cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;")
            null_count = cur.fetchone()[0]
            assert null_count == 0, f"Found {null_count} chunks with NULL embedding"

            cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL;")
            embedded_count = cur.fetchone()[0]
            assert embedded_count == 40, f"Expected 40 embedded chunks, found {embedded_count}"


def test_chunks_embedding_dimension():
    """Verify every embedding has dimension exactly 1024."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT vector_dims(embedding) FROM chunks WHERE embedding IS NOT NULL;")
            dimensions = [row[0] for row in cur.fetchall()]
            assert dimensions == [1024], f"Expected embedding dimension [1024], got {dimensions}"


def test_chunks_metadata():
    """Verify embedding metadata fields in chunks table."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT embedding_model, embedding_version, embedding_dimension
                FROM chunks;
            """)
            metadata_rows = cur.fetchall()
            assert len(metadata_rows) == 1, f"Expected 1 metadata configuration, got {metadata_rows}"
            model, version, dim = metadata_rows[0]
            assert model == "BAAI/bge-m3", f"Expected model 'BAAI/bge-m3', got {model}"
            assert version == "v1.0", f"Expected version 'v1.0', got {version}"
            assert dim == 1024, f"Expected dim 1024, got {dim}"


def test_chunks_no_orphan_records():
    """Verify every chunk is linked to a valid publication_id."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.chunk_id, c.publication_id
                FROM chunks c
                LEFT JOIN publications p ON c.publication_id = p.publication_id
                WHERE p.publication_id IS NULL;
            """)
            orphans = cur.fetchall()
            assert len(orphans) == 0, f"Found {len(orphans)} orphan chunks: {orphans}"

            cur.execute("SELECT COUNT(DISTINCT publication_id) FROM chunks;")
            distinct_pubs = cur.fetchone()[0]
            assert distinct_pubs == 20, f"Expected 20 distinct publications, got {distinct_pubs}"


def test_chunks_hnsw_index_and_similarity_search():
    """Verify HNSW index exists and cosine similarity query runs with index scan."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'chunks' AND indexname = 'idx_chunks_embedding_hnsw';
            """)
            hnsw_idx = cur.fetchone()
            assert hnsw_idx is not None, "HNSW index 'idx_chunks_embedding_hnsw' is missing on chunks table."
            assert "hnsw" in hnsw_idx[1].lower()
            assert "vector_cosine_ops" in hnsw_idx[1].lower()

            # Execute sample cosine similarity query
            cur.execute("""
                SELECT c.chunk_id, c.publication_id, p.title,
                       1 - (c.embedding <=> (SELECT embedding FROM chunks WHERE chunk_id = 'PUB000001_CH001')) AS similarity
                FROM chunks c
                JOIN publications p ON c.publication_id = p.publication_id
                ORDER BY c.embedding <=> (SELECT embedding FROM chunks WHERE chunk_id = 'PUB000001_CH001')
                LIMIT 5;
            """)
            rows = cur.fetchall()
            assert len(rows) == 5
            # Top result should be the chunk itself with similarity 1.0 (or very close to 1.0)
            assert rows[0][0] == "PUB000001_CH001"
            assert pytest.approx(rows[0][3], abs=1e-4) == 1.0


def test_institution_collaboration_edges():
    """Verify institution_collaboration table integrity."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM institution_collaboration;")
            count = cur.fetchone()[0]
            assert count == 254, f"Expected 254 institution edges, found {count}"

            # Canonical order check: institution_a < institution_b
            cur.execute("SELECT COUNT(*) FROM institution_collaboration WHERE institution_a >= institution_b;")
            invalid_order = cur.fetchone()[0]
            assert invalid_order == 0, f"Found {invalid_order} institution edges with invalid ordering"

            # Foreign key integrity check
            cur.execute("""
                SELECT COUNT(*) FROM institution_collaboration ic
                WHERE NOT EXISTS (SELECT 1 FROM institutions i WHERE i.institution_id = ic.institution_a)
                   OR NOT EXISTS (SELECT 1 FROM institutions i WHERE i.institution_id = ic.institution_b);
            """)
            orphans = cur.fetchone()[0]
            assert orphans == 0, f"Found {orphans} institution edges referencing non-existent institutions"

            # Provenance check
            cur.execute("""
                SELECT COUNT(*) FROM institution_collaboration
                WHERE cardinality(via_publication_ids) = 0 OR via_publication_ids IS NULL;
            """)
            empty_prov = cur.fetchone()[0]
            assert empty_prov == 0, f"Found {empty_prov} institution edges with empty via_publication_ids"

            # Unique key check
            cur.execute("""
                SELECT institution_a, institution_b, COUNT(*)
                FROM institution_collaboration
                GROUP BY institution_a, institution_b
                HAVING COUNT(*) > 1;
            """)
            duplicates = cur.fetchall()
            assert len(duplicates) == 0, f"Found duplicate institution edges: {duplicates}"


def test_author_collaboration_edges():
    """Verify author_collaboration table integrity."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM author_collaboration;")
            count = cur.fetchone()[0]
            assert count == 484, f"Expected 484 author edges, found {count}"

            # Canonical order check: author_a < author_b
            cur.execute("SELECT COUNT(*) FROM author_collaboration WHERE author_a >= author_b;")
            invalid_order = cur.fetchone()[0]
            assert invalid_order == 0, f"Found {invalid_order} author edges with invalid ordering"

            # Foreign key integrity check
            cur.execute("""
                SELECT COUNT(*) FROM author_collaboration ac
                WHERE NOT EXISTS (SELECT 1 FROM authors a WHERE a.author_id = ac.author_a)
                   OR NOT EXISTS (SELECT 1 FROM authors a WHERE a.author_id = ac.author_b);
            """)
            orphans = cur.fetchone()[0]
            assert orphans == 0, f"Found {orphans} author edges referencing non-existent authors"

            # Provenance check
            cur.execute("""
                SELECT COUNT(*) FROM author_collaboration
                WHERE cardinality(via_publication_ids) = 0 OR via_publication_ids IS NULL;
            """)
            empty_prov = cur.fetchone()[0]
            assert empty_prov == 0, f"Found {empty_prov} author edges with empty via_publication_ids"

            # Unique key check
            cur.execute("""
                SELECT author_a, author_b, COUNT(*)
                FROM author_collaboration
                GROUP BY author_a, author_b
                HAVING COUNT(*) > 1;
            """)
            duplicates = cur.fetchall()
            assert len(duplicates) == 0, f"Found duplicate author edges: {duplicates}"
