-- Migration 000: Canonical Silver Schema (9 relational tables)
-- Docs Reference: docs/04 Database Schema.md §4, docs/12 Data Pipeline.md §3
--
-- WHY THIS FILE EXISTS: until now the 9 Silver tables had NO committed DDL.
-- They existed only in the live database, so the prototype could not be
-- rebuilt from the repository and `scripts/verify_schema.py` reported 12 type
-- drifts plus 10 "extra" columns that no migration owned.
--
-- This file reproduces the LIVE schema as the runtime actually depends on it,
-- which is deliberately NOT identical to the aspirational DDL in docs/04 §4.
-- Where the two disagree, this file wins and docs/04 is the document that is
-- wrong. Known divergences (reconciled in docs/04):
--   1. Identifiers are VARCHAR(20), not VARCHAR(64). The derived edge and Gold
--      tables use VARCHAR(64) for the same values — harmless for FKs, but the
--      widths are inconsistent across layers.
--   2. `keywords` is keyed by (publication_id, keyword_id, keyword_type), not
--      by a surrogate `keyword_id BIGSERIAL`. `keyword_id` is itself VARCHAR(20).
--      The composite key is why `keywords.publication_id` already has index
--      coverage and does not need a separate FK index.
--   3. `chunks.chunk_id` is VARCHAR(30), not BIGINT.
--   4. `publications.open_access` is TEXT, not a boolean flag.
--   5. No CHECK constraints exist on any Silver table (only the derived edge
--      tables carry them). `publication_references.reference_id` uses a plain
--      BIGSERIAL sequence rather than an identity column.
--
-- IDEMPOTENCY: these are plain CREATE TABLE statements with no IF NOT EXISTS,
-- on purpose. The ledger in scripts/migrate.py is the idempotency mechanism:
-- a recorded migration is never re-executed, and a checksum mismatch is
-- reported as drift instead of being silently swallowed. Adopting a database
-- that already has these tables is `migrate.py baseline`, not a silent no-op.
--
-- ORDERING: parents before children (publications/authors/institutions first),
-- and before 001 which ALTERs chunks to add the vector column.

-- ---------------------------------------------------------------------------
-- 4.1 publications (core entity)
-- ---------------------------------------------------------------------------
CREATE TABLE publications (
    publication_id                 VARCHAR(20)  NOT NULL,
    eid                            VARCHAR(50),
    doi                            VARCHAR(150),
    title                          TEXT,
    year                           SMALLINT,
    source_title                   TEXT,
    volume                         VARCHAR(20),
    issue                          VARCHAR(20),
    art_no                         VARCHAR(30),
    page_start                     VARCHAR(20),
    page_end                       VARCHAR(20),
    citation_count                 INTEGER,
    link                           TEXT,
    abstract                       TEXT,
    document_type                  VARCHAR(50),
    publication_stage              VARCHAR(50),
    open_access                    TEXT,
    issn                           VARCHAR(20),
    language_of_original_document  VARCHAR(50),
    publisher                      TEXT,
    source                         VARCHAR(50),
    search_text                    TEXT,
    PRIMARY KEY (publication_id)
);

-- 'eid' is the Scopus Electronic Identifier and is declared UNIQUE in
-- docs/04 §4.1. Enforced as an index here rather than a UNIQUE constraint:
-- the prototype holds no duplicate eid, but a partial/soft guarantee is
-- preferable to failing ingestion on a data-quality edge case.
CREATE INDEX publications_eid_idx ON publications (eid);

-- ---------------------------------------------------------------------------
-- 4.2 authors
-- ---------------------------------------------------------------------------
CREATE TABLE authors (
    author_id                VARCHAR(20)  NOT NULL,
    author_name              TEXT,
    author_name_normalized   VARCHAR(255) NOT NULL,
    PRIMARY KEY (author_id)
);

-- AC-DB-3: *_normalized aggregation columns must be indexed (router/entity
-- resolution compares on the normalized form, never on the raw name).
CREATE INDEX authors_name_normalized_idx ON authors (author_name_normalized);

-- ---------------------------------------------------------------------------
-- 4.3 institutions
-- ---------------------------------------------------------------------------
CREATE TABLE institutions (
    institution_id                VARCHAR(20)  NOT NULL,
    institution_name              TEXT,
    city                          TEXT,
    country                       TEXT,
    institution_name_normalized   TEXT         NOT NULL,
    PRIMARY KEY (institution_id)
);

CREATE INDEX institutions_name_normalized_idx
    ON institutions (institution_name_normalized);

-- ---------------------------------------------------------------------------
-- 4.4 keywords & funding
-- ---------------------------------------------------------------------------
-- Composite natural key: a publication may repeat a keyword across keyword
-- types, and keyword_id is an externally-derived VARCHAR, not a surrogate.
CREATE TABLE keywords (
    keyword_id         VARCHAR(20)  NOT NULL,
    publication_id     VARCHAR(20)  NOT NULL,
    keyword            TEXT,
    keyword_type       VARCHAR(20)  NOT NULL,
    PRIMARY KEY (publication_id, keyword_id, keyword_type),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id)
);

CREATE TABLE funding (
    funding_id                 VARCHAR(20)  NOT NULL,
    publication_id             VARCHAR(20),
    funding_agency             TEXT,
    grant_number               TEXT,
    funding_text               TEXT,
    source_text                TEXT,
    funding_agency_normalized  TEXT         NOT NULL,
    PRIMARY KEY (funding_id),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id)
);

CREATE INDEX funding_agency_normalized_idx ON funding (funding_agency_normalized);

-- ---------------------------------------------------------------------------
-- 4.5 junctions & references
-- ---------------------------------------------------------------------------
CREATE TABLE pub_author (
    publication_id  VARCHAR(20) NOT NULL,
    author_id       VARCHAR(20) NOT NULL,
    author_order    SMALLINT,
    PRIMARY KEY (publication_id, author_id),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id),
    FOREIGN KEY (author_id)      REFERENCES authors (author_id)
);

CREATE TABLE pub_institution (
    publication_id   VARCHAR(20) NOT NULL,
    institution_id   VARCHAR(20) NOT NULL,
    PRIMARY KEY (publication_id, institution_id),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id),
    FOREIGN KEY (institution_id) REFERENCES institutions (institution_id)
);

CREATE TABLE publication_references (
    publication_id    VARCHAR(20) NOT NULL,
    reference_order   INTEGER     NOT NULL,
    reference_text    TEXT,
    reference_id      BIGSERIAL   NOT NULL,
    PRIMARY KEY (reference_id),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id),
    CONSTRAINT publication_references_natural_key UNIQUE (publication_id, reference_order)
);

-- ---------------------------------------------------------------------------
-- 5. chunks (semantic units; vector column arrives in migration 001)
-- ---------------------------------------------------------------------------
CREATE TABLE chunks (
    chunk_id          VARCHAR(30) NOT NULL,
    publication_id    VARCHAR(20),
    section           TEXT,
    chunk_text        TEXT,
    source_type       VARCHAR(50),
    PRIMARY KEY (chunk_id),
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id)
);

-- ---------------------------------------------------------------------------
-- Indexes that migration 004 adds later are intentionally absent here; this
-- file reproduces the pre-004 baseline so the migration sequence stays honest.
-- ---------------------------------------------------------------------------
ANALYZE publications;
ANALYZE authors;
ANALYZE institutions;
ANALYZE keywords;
ANALYZE funding;
ANALYZE pub_author;
ANALYZE pub_institution;
ANALYZE publication_references;
ANALYZE chunks;