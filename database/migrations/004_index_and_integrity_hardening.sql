-- Migration 004: Index Coverage, Integrity Constraints & Timestamp Maintenance
-- Docs Reference: docs/04 Database Schema.md §4-§7
--
-- Scope (three audit classes):
--   A. Cover every FOREIGN KEY column that a leading-column PK does not
--      already cover (docs/08 §1.1 read-only workload does seq scans otherwise).
--   B. Drop indexes that are exact prefixes of their table's composite PK.
--   C. Encode the ranges docs/04 already specifies as CHECK constraints, and
--      maintain topics.updated_at from a trigger instead of trusting callers.
--
-- SAFETY: no DDL in this file takes an ACCESS EXCLUSIVE lock that blocks
-- reads. Constraints are added NOT VALID then VALIDATEd (SHARE UPDATE
-- EXCLUSIVE, non-blocking for SELECT/INSERT/UPDATE), so a pre-existing
-- violating row surfaces as a validation error rather than a locked table.
-- For a production-size rebuild, swap CREATE INDEX for CREATE INDEX
-- CONCURRENTLY and run it outside a transaction block.

-- ===========================================================================
-- A. Foreign-key index coverage
-- ===========================================================================
-- Composite PK (publication_id, <x>) already indexes publication_id, so only
-- the reverse side of each junction needs its own index. Without these, every
-- author- or institution-scoped query and every ON DELETE CASCADE from the
-- parent table degrades to a sequential scan.

CREATE INDEX IF NOT EXISTS idx_pub_author_author_id
    ON pub_author (author_id, publication_id);
CREATE INDEX IF NOT EXISTS idx_pub_institution_institution_id
    ON pub_institution (institution_id, publication_id);
CREATE INDEX IF NOT EXISTS idx_keywords_publication_id
    ON keywords (publication_id);
CREATE INDEX IF NOT EXISTS idx_funding_publication_id
    ON funding (publication_id);
CREATE INDEX IF NOT EXISTS idx_publication_refs_publication_id
    ON publication_references (publication_id);

-- publications.citation_count / year back the "most cited" and year-scoping
-- templates in SqlRetriever, which always sort or filter on them directly.
CREATE INDEX IF NOT EXISTS idx_publications_citation_count
    ON publications (citation_count DESC);
CREATE INDEX IF NOT EXISTS idx_publications_year
    ON publications (year);

-- Trigram indexes for the leading-wildcard ILIKE predicates the SQL and
-- vector retrievers emit (`country ILIKE '%…%'`, `author_name ILIKE '%…%'`).
-- A B-tree can never serve a '%'-prefixed pattern, so entity filters were
-- seq-scanning the whole table before this existed.
--
-- pg_trgm's opclass lives in the extension's schema, which is NOT `public` on
-- Supabase (it is `extensions`, mirroring pgvector). Resolving it from the
-- catalog keeps this file layout-agnostic the same way migration 001 does for
-- CREATE EXTENSION.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
DECLARE
    trgm_schema text;
BEGIN
    SELECT n.nspname INTO trgm_schema
    FROM pg_extension e
    JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'pg_trgm';

    IF trgm_schema IS NULL THEN
        RAISE EXCEPTION
            'pg_trgm extension is not installed; cannot create trigram indexes';
    END IF;

    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_institutions_country_trgm '
        'ON institutions USING gin (country %s.gin_trgm_ops)', trgm_schema);
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_institutions_name_trgm '
        'ON institutions USING gin (institution_name %s.gin_trgm_ops)', trgm_schema);
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS idx_authors_name_trgm '
        'ON authors USING gin (author_name %s.gin_trgm_ops)', trgm_schema);
END $$;

-- ===========================================================================
-- B. Drop indexes that duplicate a composite PK prefix
-- ===========================================================================
-- PRIMARY KEY (institution_a, institution_b) creates a btree whose leading
-- column is institution_a, so idx_inst_collab_a is an exact duplicate. The
-- _b index is NOT redundant: it is the only path for the reverse hop of the
-- recursive CTE (T1-T4) and for ON DELETE CASCADE from institutions.

DROP INDEX IF EXISTS idx_inst_collab_a;
DROP INDEX IF EXISTS idx_author_collab_a;

-- ===========================================================================
-- C. Integrity constraints + timestamp maintenance
-- ===========================================================================

-- researcher_expertise score columns were NUMERIC(6,4), which tops out at
-- 99.9999 — narrower than the [0, 100] range docs/04 §7.3 specifies and than
-- score_expertise.py actually produces (it caps each sub-score at 100.0).
-- Widen before the CHECKs below would reject a legitimate 100.0000.
ALTER TABLE researcher_expertise
    ALTER COLUMN relevance_score    TYPE NUMERIC(8,4),
    ALTER COLUMN productivity_score TYPE NUMERIC(8,4),
    ALTER COLUMN impact_score       TYPE NUMERIC(8,4),
    ALTER COLUMN recency_score      TYPE NUMERIC(8,4);

-- expertise_score: [0.0000, 100.0000] (docs/04 §7.3)
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_expertise_score
        CHECK (expertise_score BETWEEN 0 AND 100) NOT VALID;
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_relevance_score
        CHECK (relevance_score BETWEEN 0 AND 100) NOT VALID;
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_productivity_score
        CHECK (productivity_score BETWEEN 0 AND 100) NOT VALID;
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_impact_score
        CHECK (impact_score BETWEEN 0 AND 100) NOT VALID;
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_recency_score
        CHECK (recency_score BETWEEN 0 AND 100) NOT VALID;
ALTER TABLE researcher_expertise
    ADD CONSTRAINT chk_researcher_exp_counts_non_negative
        CHECK (h_index_topic >= 0
           AND publication_count_topic >= 0
           AND citation_count_topic >= 0
           AND coauthor_network_size >= 0) NOT VALID;

-- topic_evolution: counts and recency_weight are never negative; the year
-- window must be ordered. growth_score / citation_acceleration are signed by
-- nature (a topic can decline), so they are deliberately left unbounded.
ALTER TABLE topic_evolution
    ADD CONSTRAINT chk_topic_evol_counts_non_negative
        CHECK (publication_count >= 0 AND citation_count >= 0) NOT VALID;
ALTER TABLE topic_evolution
    ADD CONSTRAINT chk_topic_evol_recency_weight
        CHECK (recency_weight >= 0) NOT VALID;

ALTER TABLE topics
    ADD CONSTRAINT chk_topics_totals_non_negative
        CHECK (total_publications >= 0 AND total_citations >= 0) NOT VALID;
ALTER TABLE topics
    ADD CONSTRAINT chk_topics_year_window
        CHECK (first_publication_year IS NULL
            OR latest_publication_year IS NULL
            OR first_publication_year <= latest_publication_year) NOT VALID;

-- Citation counts are external data (Scopus) and must never go negative.
ALTER TABLE publications
    ADD CONSTRAINT chk_publications_citation_count
        CHECK (citation_count >= 0) NOT VALID;

-- Edge weights are shared-publication counts (docs/04 §6).
ALTER TABLE institution_collaboration
    ADD CONSTRAINT chk_inst_collab_weight_non_negative
        CHECK (weight >= 0) NOT VALID;
ALTER TABLE author_collaboration
    ADD CONSTRAINT chk_author_collab_weight_non_negative
        CHECK (weight >= 0) NOT VALID;

-- topics.updated_at had a DEFAULT but no trigger, so it silently went stale
-- on every UPDATE. Maintain it in the database instead of relying on callers.
CREATE OR REPLACE FUNCTION public.touch_topics_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_topics_updated_at ON topics;
CREATE TRIGGER trg_topics_updated_at
    BEFORE UPDATE ON topics
    FOR EACH ROW
    EXECUTE FUNCTION public.touch_topics_updated_at();

-- Promote the NOT VALID checks now that they exist. Each VALIDATE is a
-- non-blocking scan; if a legacy row violates a range, that statement raises
-- and names the offending constraint instead of silently tolerating it.
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_expertise_score;
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_relevance_score;
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_productivity_score;
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_impact_score;
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_recency_score;
ALTER TABLE researcher_expertise VALIDATE CONSTRAINT chk_researcher_exp_counts_non_negative;
ALTER TABLE topic_evolution      VALIDATE CONSTRAINT chk_topic_evol_counts_non_negative;
ALTER TABLE topic_evolution      VALIDATE CONSTRAINT chk_topic_evol_recency_weight;
ALTER TABLE topics               VALIDATE CONSTRAINT chk_topics_totals_non_negative;
ALTER TABLE topics               VALIDATE CONSTRAINT chk_topics_year_window;
ALTER TABLE publications         VALIDATE CONSTRAINT chk_publications_citation_count;
ALTER TABLE institution_collaboration VALIDATE CONSTRAINT chk_inst_collab_weight_non_negative;
ALTER TABLE author_collaboration      VALIDATE CONSTRAINT chk_author_collab_weight_non_negative;

ANALYZE pub_author;
ANALYZE pub_institution;
ANALYZE keywords;
ANALYZE funding;
ANALYZE publication_references;
ANALYZE publications;
ANALYZE institutions;
ANALYZE authors;
ANALYZE researcher_expertise;
ANALYZE topic_evolution;
ANALYZE topics;