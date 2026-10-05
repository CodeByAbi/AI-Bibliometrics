-- Migration 009: Persist the `not_found` retrieval outcome
-- Docs Reference: docs/04 Database Schema.md §13, docs/06 Api Design.md §6.2
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Migration 005 constrains `research_messages.status` to ('complete','failed').
-- That pair answers one question: "did this turn finish?". By that question a
-- `not_found` turn IS complete — retrieval ran, correctly found zero evidence,
-- and returned deterministically without ever calling the LLM.
--
-- A restored research workspace needs a different question answered: "did this
-- turn find anything?". `status = 'complete'` cannot carry that. So a
-- conversation whose most recent turn found nothing restores looking exactly
-- like one that found something, and the reader concludes a result was produced
-- when none was. That is the same class of defect this project treats as a
-- correctness bug everywhere else: presenting absence as presence.
--
-- The three values are deliberately on two different axes:
--
--   'complete'   a grounded answer with evidence was produced
--   'not_found'  retrieval ran and correctly returned zero evidence — the
--                Zero-Hallucination short-circuit. A real, truthful outcome,
--                not an error and not an omission.
--   'failed'     the turn errored before any answer existed
--
-- Collapsing `not_found` into either neighbour loses information: merge it into
-- `complete` and absence masquerades as a result; merge it into `failed` and a
-- correct deterministic short-circuit is reported as a system error, which
-- would page someone for working as designed.
--
-- SCOPE
-- -----
-- One constraint. No data is rewritten: existing 'complete' and 'failed' rows
-- keep their meaning, and rows written before this migration are unaffected.
-- Nothing in `public` is touched, so this cannot change a retrieved value.
--
-- IDEMPOTENCY
-- -----------
-- DROP ... IF EXISTS then ADD, so re-running converges rather than erroring. The
-- migration ledger in scripts/migrate.py remains the primary mechanism; a
-- checksum mismatch is reported as drift instead of being silently swallowed.
--
-- Apply with:  python scripts/migrate.py up

ALTER TABLE app.research_messages
    DROP CONSTRAINT IF EXISTS research_messages_status_valid;

ALTER TABLE app.research_messages
    ADD CONSTRAINT research_messages_status_valid
    CHECK (status IN ('complete', 'failed', 'not_found'));

COMMENT ON COLUMN app.research_messages.status IS
    '''failed'' when retrieval or synthesis errored and no answer exists. ''not_found'' when retrieval ran and correctly returned zero evidence (deterministic Zero-Hallucination short-circuit, no LLM call). ''complete'' when a grounded answer with evidence was produced. The three are distinct outcomes and must not be collapsed: a restored workspace has to be able to say "checked, found nothing" without either hiding the turn or dressing it up as an answer.';