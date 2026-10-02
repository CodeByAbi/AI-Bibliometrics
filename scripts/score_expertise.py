"""Task 8.5 — Score Multidimensional Researcher Expertise (Phase 7 / Task 8.5).

Computes multidimensional researcher expertise scores per author and topic cluster:
    ExpertiseScore = w1 * Relevance + w2 * Productivity + w3 * Impact + w4 * Recency
    (w1 = 0.30, w2 = 0.25, w3 = 0.25, w4 = 0.20)
together with local h_index_topic and coauthor_network_size.

Usage:
    python scripts/score_expertise.py
"""

from __future__ import annotations

import logging
import math
import pathlib
import sys
import time
from typing import Any, Dict, List, Set

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("score_expertise")

W1_RELEVANCE = 0.30
W2_PRODUCTIVITY = 0.25
W3_IMPACT = 0.25
W4_RECENCY = 0.20


def calculate_h_index(citation_counts: List[int]) -> int:
    """Compute local Hirsch index from list of publication citation counts."""
    sorted_cits = sorted(citation_counts, reverse=True)
    h = 0
    for idx, c in enumerate(sorted_cits, 1):
        if c >= idx:
            h = idx
        else:
            break
    return h


def score_researcher_expertise(cur) -> int:
    """Compute and insert researcher_expertise records."""
    start = time.perf_counter()

    # Ensure clean state
    cur.execute("TRUNCATE TABLE researcher_expertise CASCADE;")

    # Fetch topics
    cur.execute(
        """
        SELECT topic_id, topic_name, cluster_keywords
        FROM topics;
        """
    )
    topics = [
        {"topic_id": int(r[0]), "topic_name": str(r[1]), "cluster_keywords": list(r[2])}
        for r in cur.fetchall()
    ]
    if not topics:
        logger.warning("No topics found in `topics` table. Please run build_topics.py first.")
        return 0

    # Fetch all authors and their publications with details
    cur.execute(
        """
        SELECT 
            pa.author_id,
            a.author_name,
            p.publication_id,
            p.title,
            p.year,
            p.citation_count
        FROM pub_author pa
        JOIN authors a ON a.author_id = pa.author_id
        JOIN publications p ON p.publication_id = pa.publication_id;
        """
    )
    rows = cur.fetchall()

    # Fetch keywords per publication
    cur.execute("SELECT publication_id, keyword_normalized FROM keywords;")
    pub_kws: Dict[str, Set[str]] = {}
    for r in cur.fetchall():
        pub_kws.setdefault(str(r[0]), set()).add(str(r[1]).lower())

    # Fetch co-authorship by publication
    cur.execute(
        """
        SELECT publication_id, ARRAY_AGG(author_id::TEXT) AS author_ids
        FROM pub_author
        GROUP BY publication_id;
        """
    )
    pub_coauthors: Dict[str, List[str]] = {str(r[0]): list(r[1]) for r in cur.fetchall()}

    # Group author publications
    author_pubs: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        aid = str(r[0])
        pid = str(r[2])
        author_pubs.setdefault(aid, []).append(
            {
                "publication_id": pid,
                "title": str(r[3]),
                "year": int(r[4]) if r[4] is not None else 2024,
                "citation_count": int(r[5]) if r[5] is not None else 0,
                "keywords": pub_kws.get(pid, set()),
            }
        )

    inserted_count = 0

    # Score each author against each topic
    for topic in topics:
        topic_id = topic["topic_id"]
        topic_kws = [k.lower() for k in topic["cluster_keywords"]]

        for aid, pubs in author_pubs.items():
            # Determine publications relevant to this topic
            topic_matched_pubs = []
            for p in pubs:
                t_lower = p["title"].lower()
                pkws = p["keywords"]

                # Relevance match: keywords overlap or title term match
                overlap = sum(1 for kw in topic_kws if kw in t_lower or any(kw in pk for pk in pkws))
                if overlap > 0:
                    topic_matched_pubs.append(p)

            # If author has publications matching this topic, score them
            if not topic_matched_pubs:
                continue

            pub_count = len(topic_matched_pubs)
            cits_list = [p["citation_count"] for p in topic_matched_pubs]
            total_cits = sum(cits_list)
            h_idx = calculate_h_index(cits_list)

            # Coauthor network in topic
            topic_coauthors: Set[str] = set()
            for p in topic_matched_pubs:
                for ca in pub_coauthors.get(p["publication_id"], []):
                    if ca != aid:
                        topic_coauthors.add(ca)
            coauthor_net_size = len(topic_coauthors)

            # Dimension 1: Relevance Score [0, 100]
            # Fraction of author's total portfolio in this topic
            relevance_ratio = min(1.0, pub_count / max(1, len(pubs)))
            relevance_score = min(100.0, max(20.0, relevance_ratio * 100.0))

            # Dimension 2: Productivity Score [0, 100]
            # Logarithmic scaling of publication volume
            prod_score = min(100.0, (math.log1p(pub_count) / math.log1p(10)) * 100.0)

            # Dimension 3: Impact Score [0, 100]
            # Logarithmic scaling of citation count + h-index bonus
            impact_score = min(100.0, (math.log1p(total_cits) / math.log1p(50)) * 80.0 + min(20.0, h_idx * 5.0))

            # Dimension 4: Recency Score [0, 100]
            # Active publications in recent 3 years (>= 2023)
            recent_pubs = sum(1 for p in topic_matched_pubs if p["year"] >= 2023)
            recency_score = min(100.0, (recent_pubs / max(1, pub_count)) * 100.0)

            # Composite Expertise Score [0.0000, 100.0000]
            expertise_score = (
                W1_RELEVANCE * relevance_score
                + W2_PRODUCTIVITY * prod_score
                + W3_IMPACT * impact_score
                + W4_RECENCY * recency_score
            )
            expertise_score = max(0.0, min(100.0, expertise_score))

            cur.execute(
                """
                INSERT INTO researcher_expertise (
                    author_id, topic_id, expertise_score, relevance_score,
                    productivity_score, impact_score, recency_score,
                    h_index_topic, publication_count_topic, citation_count_topic,
                    coauthor_network_size
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    aid,
                    topic_id,
                    round(expertise_score, 4),
                    round(relevance_score, 4),
                    round(prod_score, 4),
                    round(impact_score, 4),
                    round(recency_score, 4),
                    h_idx,
                    pub_count,
                    total_cits,
                    coauthor_net_size,
                ),
            )
            inserted_count += 1

    elapsed = time.perf_counter() - start
    logger.info(
        "Successfully computed and materialized %d researcher_expertise records across %d topics in %.2fs",
        inserted_count,
        len(topics),
        elapsed,
    )
    return inserted_count


def main() -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            score_researcher_expertise(cur)
        conn.commit()


if __name__ == "__main__":
    main()
