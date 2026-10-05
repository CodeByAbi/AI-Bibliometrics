-- Migration 006: Per-Turn Provenance Snapshots (evidence_objects, sources)
-- Docs Reference: docs/04 Database Schema.md §13, docs/03 System Architecture.md §0.3 #5
--
-- WHY THIS FILE EXISTS:
--
-- Migration 005 created app.research_messages storing the CONVERSATION: who
-- asked what, which route answered, what the answer text was. It deliberately
-- stopped there. This file adds the two columns needed to REPLAY a finished
-- research workspace without re-running the RAG pipeline:
--
--   evidence_objects  the EvidenceObject[] the verified AskResponse carried
--   sources           the SourceItem[] the verified AskResponse carried
--
-- Without them, GET /api/v1/sessions/{id} can restore the prose of a past
-- answer but not the evidence rail and source cards beside it, so reopening a
-- session would either show an answer whose citations dangle or force a re-query
-- that costs the full RAG latency to rebuild provenance that already existed.
--
-- ---------------------------------------------------------------------------
-- DOCUMENTED EXCEPTION TO THE SESSION ISOLATION INVARIANT — READ THIS
-- ---------------------------------------------------------------------------
--
-- Migration 005 states, as an invariant of schema `app`:
--
--     "NO column on any session table holds a bibliometric metric
--      (no publication_count, citation_count, top_authors, expertise_score...)."
--
-- `evidence_objects` and `sources` DO contain bibliometric metric values — an
-- EvidenceObject carries `value` plus its source refs. So this migration is a
-- deliberate, owner-signed-off narrowing of that invariant, and it is fenced
-- rather than quietly relaxed:
--
--   PERMITTED, and the only thing these columns are for:
--     An immutable, per-turn SNAPSHOT of what one already-verified AskResponse
--     contained at the moment it was returned to the caller. They exist so the
--     UI can redraw the evidence rail of a past turn byte-for-byte as it was
--     shown. Per-turn, not per-session. Written once, never recomputed, never
--     aggregated across turns.
--
--   STILL FORBIDDEN — unchanged by this migration:
--     1. NOT a canonical metric store. A `value` here is never the answer to a
--        question. It is a receipt for an answer already given.
--     2. NOT readable as source-of-truth data. No retrieval, aggregation,
--        ranking, analytics, routing, synthesis or citation-verification path
--        may read these snapshots to produce or justify a bibliometric claim.
--        Answering a question re-queries `public` from scratch, always.
--     3. NOT an aggregate. There is deliberately no session-level rollup. A
--        number a user reads off a past turn is re-verified by re-asking, the
--        same rule that already fences `summary` and prior assistant `content`.
--     4. NOT reachable from the retrieval read path. The runtime bibliometric
--        pool pins `SET search_path = public` (backend/app/db/pool.py), so
--        these columns are unreachable from Text-to-SQL by construction — the
--        same structural argument migration 005 makes for the whole schema.
--     5. NOT an ownership inversion. Still no foreign key from `app` to any
--        `public` table, so a session delete still cannot cascade into the
--        corpus. `scripts/verify_schema.py` asserts
--        `foreign_keys_into_public == 0` and continues to do so.
--
--   PROVENANCE KEY: `request_id` (already on the row since migration 005)
--     remains the correlation key for re-verification. Given a stored turn,
--     `request_id` points at the application log line and the AskResponse that
--     produced it; the snapshot is what was rendered, `request_id` is how you
--     go check it.
--
-- FENCING, so the exception cannot silently widen later:
--   * scripts/verify_schema.py §13 audits these two columns explicitly: they
--     must exist, must be JSONB, and must appear on research_messages ONLY —
--     never on research_sessions. A session-level provenance column would
--     reintroduce the aggregate this migration refuses to create.
--   * tests/unit/test_session_provenance.py plants a hostile snapshot
--     ("Dataset memiliki 999999 publications") and proves a re-ask answers from
--     `public`, not from the stored snapshot.
--
-- IDEMPOTENCY: ADD COLUMN IF NOT EXISTS, so re-running is a no-op rather than an
-- error. Additive only — no column is dropped, retyped or renamed, so this
-- cannot change a single previously stored value. Note the ledger in
-- scripts/migrate.py is the primary idempotency mechanism (a recorded migration
-- is never re-executed; a checksum mismatch is reported as drift); IF NOT EXISTS
-- is belt-and-braces for the documented manual-apply path, matching migration
-- 005's treatment of its indexes.
--
-- SAFETY: adding a column with a constant DEFAULT is metadata-only on
-- PostgreSQL 11+ (no table rewrite), so this does not lock the table. Rows
-- written before this migration read back as empty arrays, which renders as
-- "that turn had no recorded evidence" rather than as corrupt data.
--
-- Apply with:  python scripts/migrate.py up

-- ===========================================================================
-- 1. Columns
-- ===========================================================================
-- DEFAULT '[]'::jsonb, NOT NULL: a message always has a defined provenance
-- shape. 'user' rows legitimately carry empty arrays — a user turn cites
-- nothing — and NULL would only add a third state ("never recorded") that the
-- API would then have to distinguish from "recorded as none".

ALTER TABLE app.research_messages
    ADD COLUMN IF NOT EXISTS evidence_objects JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS sources          JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ===========================================================================
-- 2. Shape constraints
-- ===========================================================================
-- These constrain the JSON *shape* only (must be an array). They deliberately do
-- NOT constrain the contents: a snapshot's contents are, by definition, whatever
-- a verified response carried, and validating them here would mean re-deriving
-- evidence validation at the storage layer — a second, divergent copy of
-- EvidenceObject validation that could drift from backend/app/models/ask.py.
-- jsonb_typeof is NULL-safe in the sense that a non-array yields 'object' or
-- 'scalar' and fails the check, which is the intent.

ALTER TABLE app.research_messages
    DROP CONSTRAINT IF EXISTS research_messages_evidence_objects_array;
ALTER TABLE app.research_messages
    ADD CONSTRAINT research_messages_evidence_objects_array
    CHECK (jsonb_typeof(evidence_objects) = 'array');

ALTER TABLE app.research_messages
    DROP CONSTRAINT IF EXISTS research_messages_sources_array;
ALTER TABLE app.research_messages
    ADD CONSTRAINT research_messages_sources_array
    CHECK (jsonb_typeof(sources) = 'array');

-- ===========================================================================
-- 3. Column comments — the audit trail for the exception above
-- ===========================================================================

COMMENT ON COLUMN app.research_messages.evidence_objects IS
    'Immutable per-turn snapshot of the EvidenceObject[] carried by the verified AskResponse for this turn. RENDERING PROVENANCE ONLY: never a source of truth, never an aggregate, never readable by retrieval to justify a bibliometric claim. Re-verify via request_id by re-querying public.';

COMMENT ON COLUMN app.research_messages.sources IS
    'Immutable per-turn snapshot of the SourceItem[] carried by the verified AskResponse for this turn. RENDERING PROVENANCE ONLY, under the same fencing as evidence_objects. request_id is the re-verification key.';

-- ===========================================================================
-- 4. Grants are NOT set here.
-- ===========================================================================
-- Same discipline as migration 005: migration files own DDL,
-- scripts/grant_session_role.py owns privileges. The existing role already
-- holds INSERT/UPDATE on research_messages as a table, so the two new columns
-- are covered by the existing table-level grants and no ACL change is required.
-- That is stated here so a future reader does not assume a grant was forgotten.