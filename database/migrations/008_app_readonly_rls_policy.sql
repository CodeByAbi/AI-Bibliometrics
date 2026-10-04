-- Migration 008: RLS SELECT policy for the read-only application role
-- Docs Reference: docs/08 Security.md §1.1, docs/02 Functional Requirements.md FR7.4,
--                 docs/03 System Architecture.md §0.2
--
-- WHY THIS FILE EXISTS:
--
-- Migration 007 made `app_readonly` able to authenticate. That is necessary but
-- nowhere near sufficient, and the reason is worth stating precisely because it
-- is invisible from the client side.
--
-- Measured on the live database after 007 was applied:
--
--   * Row-Level Security is ENABLED on all 15 tables in `public`
--     (`relrowsecurity = true`).
--   * There are ZERO policies on `public` (`pg_policies` is empty).
--   * RLS with no policy is default-deny for every role that is not the table
--     owner and does not hold BYPASSRLS.
--
-- Therefore, as `app_readonly`:
--
--   SELECT COUNT(*) FROM publications;  -->  0
--
-- while the same query as `postgres` returns 20. `postgres` sees the rows purely
-- because it OWNS the tables, and owners are exempt from their own RLS.
--
-- This is precisely why the deployment was connecting as `postgres`. Switching
-- DB_URL to `app_readonly` without this migration would not fail loudly: every
-- request would return HTTP 200 with `status="not_found"` and zero evidence,
-- forever. The application would look healthy and be silently empty.
--
-- ---------------------------------------------------------------------------
-- WHY A POLICY, AND NOT THE TWO ALTERNATIVES
-- ---------------------------------------------------------------------------
--
-- (a) `ALTER ROLE app_readonly BYPASSRLS` — UNAVAILABLE. It requires a superuser
--     to grant, and this deployment's `postgres` role has `rolsuper = false`.
--     Verified on the live server.
--
-- (b) `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` — REJECTED. It would undo the
--     hardening that RLS provides. `scripts/grant_readonly.py` reports that the
--     15 public tables still carry `anon`/`authenticated` grants; RLS is the
--     only thing currently denying the Supabase REST API access to the corpus.
--     Disabling RLS would convert a latent grant into a live one and publish the
--     bibliometric data through the public API. That is a far worse outcome than
--     the problem being fixed here.
--
-- (c) An explicit `FOR SELECT` policy TO app_readonly — ADOPTED. RLS stays
--     enabled on every table, so `anon` and `authenticated` remain denied exactly
--     as they are today, and the runtime role gets precisely the access its
--     documented SELECT grant already promised. Defence in depth is preserved
--     rather than removed: a future accidental INSERT/UPDATE grant to
--     app_readonly would still be blocked, because no policy covers those
--     commands and a policy without `WITH CHECK` is read-only by construction.
--
-- SECURITY POSTURE IS UNCHANGED FOR EVERY OTHER ROLE: the policy is scoped to
-- `app_readonly` alone.
--
-- NOT a migration against data: no row is inserted, updated, or deleted, so
-- applying it cannot change a single retrieved value for an authorized reader.
--
-- Apply with:  python scripts/migrate.py up
-- Verify with: python scripts/verify_schema.py

-- ===========================================================================
-- 1. One read policy per canonical table
-- ===========================================================================
-- Applied in a DO block rather than 15 literal CREATE POLICY statements: the
-- policy is identical for all of them, and a generated loop means a table added
-- to `public` later is covered by re-running the body instead of needing a
-- hand-written 16th block that someone would forget.
--
-- `USING (true)` is safe here precisely BECAUSE the policy is FOR SELECT only
-- and TO one role. It grants no write path: RLS additionally requires a
-- `WITH CHECK` clause to permit INSERT/UPDATE, and a SELECT-only policy has none.

DO $$
DECLARE
    t          RECORD;
    created_n  INTEGER := 0;
BEGIN
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind = 'r'
           -- schema_migrations is migration bookkeeping owned by the owner
           -- role; the runtime must not read it.
           AND c.relname <> 'schema_migrations'
    LOOP
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO app_readonly USING (true)',
            'app_readonly_select_' || t.relname,
            t.relname
        );
        created_n := created_n + 1;
    END LOOP;

    IF created_n = 0 THEN
        RAISE EXCEPTION
            'No public tables found — refusing to apply an empty policy set. '
            'app_readonly would authenticate and then see zero rows.';
    END IF;

    RAISE NOTICE 'app_readonly: created % SELECT policies on public tables', created_n;
END
$$;

-- ===========================================================================
-- 2. Assert the policy set is read-only
-- ===========================================================================
-- A future edit that widens a policy to ALL commands, or to PUBLIC, would
-- silently reopen the write path this whole exercise exists to close. Fail the
-- migration instead of letting that land unnoticed.

DO $$
DECLARE
    bad TEXT;
BEGIN
    -- `pg_policies.roles` is name[], NOT text. Comparing it as text is a type
    -- error (`operator does not exist: text && text[]`), so both checks below
    -- cast the literal to name[] instead.
    SELECT string_agg(tablename || '.' || policyname, ', ' ORDER BY tablename, policyname)
      INTO bad
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname LIKE 'app_readonly_select_%'
       AND (cmd <> 'SELECT'
            OR roles IS DISTINCT FROM ARRAY['app_readonly']::name[]);

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION
            'app_readonly policies are wider than SELECT TO app_readonly: %', bad;
    END IF;

    -- The corpus must remain invisible to the public API roles. RLS plus "no
    -- policy for these roles" is what denies them; if a policy ever named them,
    -- that protection is gone.
    SELECT string_agg(tablename || '.' || policyname, ', ' ORDER BY tablename, policyname)
      INTO bad
      FROM pg_policies
     WHERE schemaname = 'public'
       AND policyname LIKE 'app_readonly_select_%'
       AND roles && ARRAY['anon', 'authenticated']::name[];

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION
            'app_readonly policies also grant anon/authenticated: %', bad;
    END IF;
END
$$;