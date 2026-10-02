-- Migration 001: Vector Extension and Chunks Embedding Schema
-- Docs Reference: docs/04 Database Schema.md §5, docs/12 Data Pipeline.md §4
--
-- NOTE (Phase 4 audit): CREATE EXTENSION without a schema installs into the
-- first schema on search_path (Supabase: `extensions` via its managed setup;
-- vanilla local installs: `public`). Runtime SQL does NOT assume either
-- layout — VectorRetriever qualifies the operator/cast with the validated
-- VECTOR_SCHEMA setting (default `extensions`, override to `public` for
-- local installs; see .env.example). Keep this file as-is; do NOT hardcode
-- a schema-qualified CREATE EXTENSION, which would break one layout or the
-- other on re-run (IF NOT EXISTS is layout-agnostic only unqualified).

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64) DEFAULT 'BAAI/bge-m3';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(32) DEFAULT 'v1.0';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_dimension SMALLINT DEFAULT 1024;

-- Production HNSW Index (Cosine Similarity)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_chunks_pub_id ON chunks (publication_id);
