-- Migration 003: Gold Analytics Tables (topics, topic_evolution, researcher_expertise)
-- Docs Reference: docs/04 Database Schema.md §7, docs/12 Data Pipeline.md §6, docs/10 Implementation Plan.md §1 (Task 8.5)

-- 1. topics table
CREATE TABLE IF NOT EXISTS topics (
    topic_id                BIGSERIAL PRIMARY KEY,
    topic_name              VARCHAR(255) NOT NULL,
    topic_name_normalized   VARCHAR(255) NOT NULL,
    cluster_keywords        TEXT[] NOT NULL,
    representation_vector   vector(1024),
    total_publications      INTEGER NOT NULL DEFAULT 0,
    total_citations         INTEGER NOT NULL DEFAULT 0,
    first_publication_year  SMALLINT,
    latest_publication_year SMALLINT,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_topics_name_norm ON topics (topic_name_normalized);
CREATE INDEX IF NOT EXISTS idx_topics_total_pub ON topics (total_publications DESC);
CREATE INDEX IF NOT EXISTS idx_topics_rep_vector_hnsw ON topics USING hnsw (representation_vector vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- 2. topic_evolution table
CREATE TABLE IF NOT EXISTS topic_evolution (
    evolution_id            BIGSERIAL PRIMARY KEY,
    topic_id                BIGINT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
    year                    SMALLINT NOT NULL,
    publication_count       INTEGER NOT NULL DEFAULT 0,
    citation_count          INTEGER NOT NULL DEFAULT 0,
    growth_score            NUMERIC(6,4) NOT NULL DEFAULT 0.0000,
    citation_acceleration   NUMERIC(6,4) NOT NULL DEFAULT 0.0000,
    recency_weight          NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    is_emerging             BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (topic_id, year)
);

CREATE INDEX IF NOT EXISTS idx_topic_evol_lookup ON topic_evolution (topic_id, year);
CREATE INDEX IF NOT EXISTS idx_topic_evol_emerging ON topic_evolution (year, is_emerging) WHERE is_emerging = TRUE;
CREATE INDEX IF NOT EXISTS idx_topic_evol_growth ON topic_evolution (year, growth_score DESC);

-- 3. researcher_expertise table
CREATE TABLE IF NOT EXISTS researcher_expertise (
    expertise_id            BIGSERIAL PRIMARY KEY,
    author_id               VARCHAR(64) NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    topic_id                BIGINT NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
    expertise_score         NUMERIC(8,4) NOT NULL,
    relevance_score         NUMERIC(6,4) NOT NULL,
    productivity_score      NUMERIC(6,4) NOT NULL,
    impact_score            NUMERIC(6,4) NOT NULL,
    recency_score           NUMERIC(6,4) NOT NULL,
    h_index_topic           INTEGER NOT NULL DEFAULT 0,
    publication_count_topic INTEGER NOT NULL DEFAULT 0,
    citation_count_topic    INTEGER NOT NULL DEFAULT 0,
    coauthor_network_size   INTEGER NOT NULL DEFAULT 0,
    calculated_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (author_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_researcher_exp_rank ON researcher_expertise (topic_id, expertise_score DESC);
CREATE INDEX IF NOT EXISTS idx_researcher_exp_author ON researcher_expertise (author_id);
