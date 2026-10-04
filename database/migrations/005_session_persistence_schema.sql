-- Migration 005: Application Schema — Session Persistence (conversation state)
-- Docs Reference: docs/04 Database Schema.md §13, docs/03 System Architecture.md §0.3
--
-- WHY THIS FILE EXISTS:
--
-- The system is two LOGICAL domains that happen to share one physical
-- PostgreSQL instance. This file materialises the second one. Nothing in this
-- file is bibliometric: it stores conversation state and nothing else.
--
--   public → canonical bibliometric source of truth (9 Silver + 2 Derived Edge
--            + Gold + chunks). Unchanged by this migration.
--   app    → research_sessions, research_messages,
--            research_session_summaries. Conversation state only.
--
-- WHY A SEPARATE POSTGRESQL SCHEMA RATHER THAN MORE TABLES IN `public`:
-- The runtime bibliometric pool pins `SET search_path = public`
-- (backend/app/db/pool.py). A session object living in `app` is therefore
-- UNREACHABLE from the retrieval read path by construction, not merely by
-- convention — even a buggy or generated Text-to-SQL statement that somehow
-- named a session table would fail to resolve it. Placing these tables in
-- `public` would put them one `search_path` change away from the AST-whitelisted
-- read role, which is exactly the boundary this feature must not erode.
--
-- ARCHITECTURAL INVARIANT (docs/03 §0.3 #5, Session Isolation Invariant):
-- These tables store conversation state only. No lifecycle operation on a
-- session may alter or delete canonical bibliometric data. Every bibliometric
-- fact and metric must still be obtained by retrieval against `public`.
-- Concretely, that means:
--   * NO foreign key in this file references any `public` table.
--   * NO column on any session table holds a bibliometric metric
--     (no publication_count, citation_count, top_authors, expertise_score...).
--   * ON DELETE CASCADE is confined to the application domain, so deleting a
--     session structurally cannot reach the corpus.
--
-- IDEMPOTENCY: plain CREATE TABLE without IF NOT EXISTS on the tables, on
-- purpose — the ledger in scripts/migrate.py is the idempotency mechanism
-- (a recorded migration is never re-executed; a checksum mismatch is reported
-- as drift instead of being silently swallowed). See migration 000 for the same
-- reasoning. `CREATE SCHEMA IF NOT EXISTS` and `IF NOT EXISTS` on the INDEXES
-- are used because they are additive no-ops on an existing schema rather than
-- silent drift on a table body.
--
-- NOT a migration against `public`: this file intentionally performs NO
-- ALTER/INSERT/UPDATE on any bibliometric table, so applying it cannot change
-- a single retrieved value.
--
-- ORDERING: schema, then parent (research_sessions), then the two children
-- that reference it.

-- ===========================================================================
-- 1. Application schema
-- ===========================================================================
-- REVOKE ALL from PUBLIC first: on PostgreSQL < 15 a freshly created schema
-- grants CREATE to PUBLIC, which would let any role create objects in the
-- application domain. (On PG15+ this is already the default, so it is a no-op
-- there — kept because this file is also the documented setup path for
-- self-hosted PostgreSQL, mirroring scripts/grant_readonly.py §_apply_readonly_grants.)

CREATE SCHEMA IF NOT EXISTS app;
REVOKE ALL ON SCHEMA app FROM PUBLIC;

COMMENT ON SCHEMA app IS
    'Application domain: conversation state only. NOT bibliometric source of truth. '
    'All bibliometric facts must be retrieved from schema public.';

-- ===========================================================================
-- 2. research_sessions — one research conversation / workspace
-- ===========================================================================
-- Deliberately carries NO bibliometric aggregates. A `publication_count` column
-- here would be a stale copy of `publications` the moment the corpus is
-- re-ingested, and the UI has no way to know which of the two is authoritative.
-- Anything the UI wants to show is fetched by asking /api/v1/ask.

CREATE TABLE app.research_sessions (
    session_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title            TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at  TIMESTAMPTZ,

    CONSTRAINT research_sessions_title_not_blank
        CHECK (btrim(title) <> ''),
    -- Lifecycle is deliberately two-state. A session is either the live
    -- conversation or an archived one; there is no third state to reconcile.
    CONSTRAINT research_sessions_status_valid
        CHECK (status IN ('active', 'archived')),
    -- updated_at can only move forward relative to created_at.
    CONSTRAINT research_sessions_updated_after_created
        CHECK (updated_at >= created_at)
);

COMMENT ON TABLE app.research_sessions IS
    'Conversation workspace. Conversation state only; contains no bibliometric data.';
COMMENT ON COLUMN app.research_sessions.updated_at IS
    'Bumped on every persisted turn. Written by an in-place UPDATE (never read-modify-write) so concurrent turns cannot lose the increment.';
COMMENT ON COLUMN app.research_sessions.last_message_at IS
    'Timestamp of the newest persisted turn. Drives the Recent Sessions ordering in GET /api/v1/sessions; NULL for a session with no messages.';

-- ===========================================================================
-- 3. research_messages — the conversation transcript
-- ===========================================================================
-- `seq` is a table-level identity, not per-session. It gives a total insertion
-- order that breaks `created_at` ties deterministically: two turns written in
-- the same millisecond (Test: concurrent requests on one session) otherwise have
-- no defined order, and `GET /api/v1/sessions/{id}` promises chronological
-- ordering.
--
-- `request_id` correlates a stored turn back to the application log line and
-- the AskResponse that produced it. Provenance, not evidence.
--
-- `applied_filters` is the RESOLVED SCOPE for that turn — see the invariant
-- note below. It is deliberately narrow: only FilterParams-shaped scope keys
-- (country, author_name, institution_name, topic_name, keyword, document_type,
-- year*), and NEVER any retrieval output, count, or metric.
--
-- INVARIANT — applied_filters is provenance metadata only:
--   applied_filters records the scope resolved for that turn. It MUST NOT
--   contain or be interpreted as bibliometric evidence, metrics, or
--   source-of-truth facts. It lets a follow-up question inherit an unspecified
--   filter ("siapa yang paling produktif?" -> still scoped to Indonesia), after
--   which retrieval re-queries `public` from scratch. The database evidence
--   still wins: applied_filters says what scope was resolved, not what the
--   answer is.
--
-- `status` carries the failure state required by the spec's failure handling —
-- when retrieval or the LLM fails, the user turn is kept and the assistant turn
-- is recorded as 'failed' rather than the conversation silently losing its tail.

CREATE TABLE app.research_messages (
    message_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    session_id       UUID        NOT NULL,
    role             TEXT        NOT NULL,
    content          TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'complete',
    applied_filters  JSONB,
    request_id       TEXT,
    route            TEXT,
    seq              BIGINT      GENERATED ALWAYS AS IDENTITY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT research_messages_pkey PRIMARY KEY (message_id),

    -- The only foreign key in this migration, and it points at another session
    -- table. ON DELETE CASCADE is scoped to the application domain: deleting a
    -- session removes its transcript and summary and can reach nothing in
    -- `public`.
    CONSTRAINT research_messages_session_fk
        FOREIGN KEY (session_id)
        REFERENCES app.research_sessions (session_id)
        ON DELETE CASCADE,

    CONSTRAINT research_messages_role_valid
        CHECK (role IN ('user', 'assistant')),
    CONSTRAINT research_messages_status_valid
        CHECK (status IN ('complete', 'failed')),
    CONSTRAINT research_messages_content_not_blank
        CHECK (btrim(content) <> '')
);

COMMENT ON TABLE app.research_messages IS
    'Conversation transcript. `content` is what was said, NOT what is factually true about the corpus.';
COMMENT ON COLUMN app.research_messages.applied_filters IS
    'Resolved request scope for this turn (provenance only, never evidence). See table comment.';
COMMENT ON COLUMN app.research_messages.route IS
    'Which retrieval route answered this turn (SQLRoute/VectorRoute/GraphRoute/HybridRoute). Provenance only.';
COMMENT ON COLUMN app.research_messages.status IS
    '''failed'' when retrieval or synthesis errored, so a broken turn is visible in history instead of vanishing.';

-- ===========================================================================
-- 4. research_session_summaries — rolling conversation memory
-- ===========================================================================
-- ONE row per session (session_id is the PK), not an append-only history.
-- Rationale: the summary is a single rolling projection of the transcript,
-- rebuilt from the last N turns. Making session_id the PK turns the
-- concurrent-update problem into an INSERT ... ON CONFLICT upsert, which is
-- atomic by construction — two concurrent turns cannot interleave into a
-- corrupt summary, because one of the two upserts simply wins.
--
-- An index on (session_id) is deliberately NOT created: the PK already indexes
-- it exactly. Migration 004 §B removed indexes that were exact prefixes of a
-- composite PK for the same reason; that discipline applies to new indexes too.
--
-- `messages_covered` records how many transcript rows the summary reflects, so
-- staleness is detectable (summary.messages_covered < COUNT(messages)) without
-- re-reading the summary text.

CREATE TABLE app.research_session_summaries (
    session_id       UUID        PRIMARY KEY,
    summary          TEXT        NOT NULL,
    messages_covered INTEGER     NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT research_session_summaries_session_fk
        FOREIGN KEY (session_id)
        REFERENCES app.research_sessions (session_id)
        ON DELETE CASCADE,

    CONSTRAINT research_session_summaries_summary_not_blank
        CHECK (btrim(summary) <> ''),
    CONSTRAINT research_session_summaries_covered_non_negative
        CHECK (messages_covered >= 0),
    CONSTRAINT research_session_summaries_updated_after_created
        CHECK (updated_at >= created_at)
);

COMMENT ON TABLE app.research_session_summaries IS
    'Conversation memory: topic, scope, questions asked, entities discussed. '
    'NOT bibliometric memory — no publication counts, citation counts, expertise '
    'scores or growth scores belong here (docs/04 §13).';

-- ===========================================================================
-- 5. Indexes
-- ===========================================================================
-- A. Transcript read path. Covers two distinct access patterns with one index:
--    GET /api/v1/sessions/{id}      -> full ordered history per session
--    conversation-context assembly  -> the newest N turns per session
--    Both are (session_id, created_at) ordered, so leading with session_id
--    serves the per-session filter and created_at supplies the order.
--    `seq` is the tiebreaker for same-millisecond inserts and is covered as a
--    trailing INCLUDE column, so the ordering never needs a sort node.
CREATE INDEX IF NOT EXISTS idx_research_messages_session_created
    ON app.research_messages (session_id, created_at)
    INCLUDE (seq);

-- B. Recent Sessions ordering: ORDER BY last_message_at DESC NULLS LAST.
--    A session with no messages has last_message_at IS NULL and sorts last
--    (NULLS LAST is explicit rather than relying on the ASC/DC default, which
--    is DESC-nulls-first — the opposite of what this endpoint wants).
--    Partial: an archived session is never listed by default, so it does not
--    need to sit in the index at all.
CREATE INDEX IF NOT EXISTS idx_research_sessions_last_message
    ON app.research_sessions (last_message_at DESC NULLS LAST)
    WHERE status = 'active';

-- C. requested by the spec: research_sessions(updated_at). Serves the
--    "most recently touched" ordering and any staleness sweep over sessions.
CREATE INDEX IF NOT EXISTS idx_research_sessions_updated
    ON app.research_sessions (updated_at DESC);

-- ===========================================================================
-- 6. Grants are NOT set here.
-- ===========================================================================
-- Deliberate: migration files own DDL, scripts/grant_session_role.py owns
-- privileges, exactly as scripts/grant_readonly.py owns app_readonly's grants.
-- Keeping them apart means the role/password policy is reviewable in one place
-- and never drifts into a migration checksum.
--
-- Apply with:  python scripts/migrate.py up
-- Then grant:   python scripts/grant_session_role.py