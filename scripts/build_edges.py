"""Task 8 — Materialize Collaboration Graph Edge Tables (Phase 1 / Task 8).

Materializes `institution_collaboration` and `author_collaboration` derived edge tables
from canonical Silver junction tables `pub_institution` and `pub_author`.

Enforces canonical key order (a < b), computes co-authorship / co-institution weights,
and aggregates `via_publication_ids` array for provenance.

Usage:
    python scripts/build_edges.py
"""

from __future__ import annotations

import logging
import pathlib
import sys
import time

# Ensure project root is in sys.path
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_edges")


def materialize_institution_edges(cur) -> dict:
    logger.info("Materializing institution_collaboration table...")
    start = time.perf_counter()
    
    cur.execute("TRUNCATE TABLE institution_collaboration;")
    
    insert_sql = """
        INSERT INTO institution_collaboration (institution_a, institution_b, weight, via_publication_ids)
        SELECT 
            LEAST(pi_a.institution_id, pi_b.institution_id) AS institution_a,
            GREATEST(pi_a.institution_id, pi_b.institution_id) AS institution_b,
            COUNT(DISTINCT pi_a.publication_id) AS weight,
            ARRAY_AGG(DISTINCT pi_a.publication_id::TEXT) AS via_publication_ids
        FROM pub_institution pi_a
        JOIN pub_institution pi_b 
             ON pi_b.publication_id = pi_a.publication_id
            AND pi_b.institution_id > pi_a.institution_id
        GROUP BY 1, 2;
    """
    cur.execute(insert_sql)
    
    cur.execute("SELECT COUNT(*) FROM institution_collaboration;")
    count = cur.fetchone()[0]
    
    # Check canonical ordering constraint check
    cur.execute("SELECT COUNT(*) FROM institution_collaboration WHERE institution_a >= institution_b;")
    invalid_order = cur.fetchone()[0]
    
    # Check empty via_publication_ids
    cur.execute("SELECT COUNT(*) FROM institution_collaboration WHERE cardinality(via_publication_ids) = 0 OR via_publication_ids IS NULL;")
    empty_prov = cur.fetchone()[0]

    # Sample top edge
    cur.execute("SELECT institution_a, institution_b, weight, via_publication_ids FROM institution_collaboration ORDER BY weight DESC, institution_a LIMIT 1;")
    top_edge = cur.fetchone()

    duration = time.perf_counter() - start
    logger.info("institution_collaboration materialized: %d edges in %.2f seconds (invalid_order=%d, empty_prov=%d)", count, duration, invalid_order, empty_prov)
    if top_edge:
        logger.info("Top institution edge: %s <-> %s (weight=%d, pubs=%s)", top_edge[0], top_edge[1], top_edge[2], top_edge[3][:3])

    return {
        "count": count,
        "invalid_order": invalid_order,
        "empty_provenance": empty_prov,
        "duration_sec": duration,
    }


def materialize_author_edges(cur) -> dict:
    logger.info("Materializing author_collaboration table...")
    start = time.perf_counter()
    
    cur.execute("TRUNCATE TABLE author_collaboration;")
    
    insert_sql = """
        INSERT INTO author_collaboration (author_a, author_b, weight, via_publication_ids)
        SELECT 
            LEAST(pa_a.author_id, pa_b.author_id) AS author_a,
            GREATEST(pa_a.author_id, pa_b.author_id) AS author_b,
            COUNT(DISTINCT pa_a.publication_id) AS weight,
            ARRAY_AGG(DISTINCT pa_a.publication_id::TEXT) AS via_publication_ids
        FROM pub_author pa_a
        JOIN pub_author pa_b 
             ON pa_b.publication_id = pa_a.publication_id
            AND pa_b.author_id > pa_a.author_id
        GROUP BY 1, 2;
    """
    cur.execute(insert_sql)
    
    cur.execute("SELECT COUNT(*) FROM author_collaboration;")
    count = cur.fetchone()[0]
    
    # Check canonical ordering constraint check
    cur.execute("SELECT COUNT(*) FROM author_collaboration WHERE author_a >= author_b;")
    invalid_order = cur.fetchone()[0]
    
    # Check empty via_publication_ids
    cur.execute("SELECT COUNT(*) FROM author_collaboration WHERE cardinality(via_publication_ids) = 0 OR via_publication_ids IS NULL;")
    empty_prov = cur.fetchone()[0]

    # Sample top edge
    cur.execute("SELECT author_a, author_b, weight, via_publication_ids FROM author_collaboration ORDER BY weight DESC, author_a LIMIT 1;")
    top_edge = cur.fetchone()

    duration = time.perf_counter() - start
    logger.info("author_collaboration materialized: %d edges in %.2f seconds (invalid_order=%d, empty_prov=%d)", count, duration, invalid_order, empty_prov)
    if top_edge:
        logger.info("Top author edge: %s <-> %s (weight=%d, pubs=%s)", top_edge[0], top_edge[1], top_edge[2], top_edge[3][:3])

    return {
        "count": count,
        "invalid_order": invalid_order,
        "empty_provenance": empty_prov,
        "duration_sec": duration,
    }


def main() -> int:
    logger.info("Starting graph edge materialization...")
    with get_db_connection(autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                inst_res = materialize_institution_edges(cur)
                auth_res = materialize_author_edges(cur)

            conn.commit()
            logger.info("Transaction committed successfully.")
        except Exception as exc:
            conn.rollback()
            logger.error("Materialization failed, transaction rolled back: %s", exc)
            return 1

    # Re-grant SELECT to app_readonly
    from scripts.grant_readonly import main as grant_main
    logger.info("Re-granting SELECT privileges on edge tables to app_readonly...")
    grant_rc = grant_main()
    if grant_rc != 0:
        logger.warning("Grant script returned non-zero code %d", grant_rc)

    if inst_res["invalid_order"] > 0 or inst_res["empty_provenance"] > 0:
        logger.error("Institution edge validation failed: %s", inst_res)
        return 1

    if auth_res["invalid_order"] > 0 or auth_res["empty_provenance"] > 0:
        logger.error("Author edge validation failed: %s", auth_res)
        return 1

    logger.info("Graph edge materialization completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
