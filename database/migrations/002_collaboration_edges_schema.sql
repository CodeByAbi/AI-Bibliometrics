-- Migration 002: Derived Collaboration Edge Tables
-- Docs Reference: docs/04 Database Schema.md §6, docs/12 Data Pipeline.md §5

CREATE TABLE IF NOT EXISTS institution_collaboration (
    institution_a       VARCHAR(64) NOT NULL REFERENCES institutions(institution_id) ON DELETE CASCADE,
    institution_b       VARCHAR(64) NOT NULL REFERENCES institutions(institution_id) ON DELETE CASCADE,
    weight              INTEGER     NOT NULL,
    via_publication_ids TEXT[]      NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (institution_a, institution_b),
    CHECK (institution_a < institution_b)
);

CREATE INDEX IF NOT EXISTS idx_inst_collab_a ON institution_collaboration (institution_a);
CREATE INDEX IF NOT EXISTS idx_inst_collab_b ON institution_collaboration (institution_b);
CREATE INDEX IF NOT EXISTS idx_inst_collab_weight ON institution_collaboration (weight DESC);

CREATE TABLE IF NOT EXISTS author_collaboration (
    author_a            VARCHAR(64) NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    author_b            VARCHAR(64) NOT NULL REFERENCES authors(author_id) ON DELETE CASCADE,
    weight              INTEGER     NOT NULL,
    via_publication_ids TEXT[]      NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_a, author_b),
    CHECK (author_a < author_b)
);

CREATE INDEX IF NOT EXISTS idx_author_collab_a ON author_collaboration (author_a);
CREATE INDEX IF NOT EXISTS idx_author_collab_b ON author_collaboration (author_b);
CREATE INDEX IF NOT EXISTS idx_author_collab_weight ON author_collaboration (weight DESC);
