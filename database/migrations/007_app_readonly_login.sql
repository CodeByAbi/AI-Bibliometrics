-- Migration 007: enable LOGIN on the read-only application role
-- Docs Reference: docs/02 Functional Requirements.md FR7.4, docs/08 Security.md §1.1,
--                 docs/03 System Architecture.md §0.2
--
-- WHY THIS FILE EXISTS:
--
-- The canonical design requires the runtime to connect as `app_readonly`, an
-- unprivileged role that holds SELECT and nothing else (docs/08 §1.1). Measured on
-- the live deployment, it did not:
--
--   * `app_readonly` existed with its grants correctly applied, but
--     `rolcanlogin = false`. The role could not be used by any client at all.
--   * The runtime actually connected as `postgres` — the table owner — so the
--     documented least-privilege boundary was not in force. Any injection or bug
--     that reached DML had owner-level rights available to it.
--
-- The two facts together mean the documented remedy was not executable as
-- written: a reviewer reading docs/02 FR7.4 would conclude the deployment was
-- read-only, and it was not.
--
-- This migration grants the LOGIN capability. It deliberately does NOT set a
-- password:
--
--   * Migration files are tracked in git and their checksums are recorded in the
--     `schema_migrations` ledger, so a credential written here would be a
--     committed secret. The password is applied out-of-band by
--     `scripts/grant_readonly.py --set-password`, which reads it from the
--     environment and never writes it to a tracked file.
--   * This split follows the convention migration 005 set out in its §6:
--     migrations own DDL and role attributes; scripts own privileges and
--     credentials.
--
-- IDEMPOTENCY: `ALTER ROLE ... LOGIN` is a no-op when the attribute is already
-- set. The ledger in scripts/migrate.py is still the primary idempotency
-- mechanism; this statement is written to be safely re-runnable regardless.
--
-- NOT a migration against `public`: no bibliometric table is touched, so
-- applying it cannot change a single retrieved value.
--
-- Apply with:  python scripts/migrate.py up
-- Then:        python scripts/grant_readonly.py --set-password
-- Then point DB_URL (.env) at the app_readonly DSN.
--
-- ROLLBACK: `ALTER ROLE app_readonly NOLOGIN;` and restore the previous DB_URL.

-- ===========================================================================
-- 1. Enable LOGIN on the role
-- ===========================================================================
-- Narrow on purpose: this grants the ability to authenticate. It grants no
-- privilege. The SELECT-only grant surface is owned by scripts/grant_readonly.py,
-- so this file cannot widen what the role can reach.

ALTER ROLE app_readonly LOGIN;

COMMENT ON ROLE app_readonly IS
    'Runtime retrieval role: LOGIN enabled, SELECT-only on the canonical corpus. '
    'No INSERT/UPDATE/DELETE, no DDL, and not the owner of any table. The password '
    'is applied out-of-band by scripts/grant_readonly.py --set-password and is '
    'never stored in a tracked migration.';

-- ===========================================================================
-- 2. Assert the least-privilege contract
-- ===========================================================================
-- A migration that grants LOGIN to a role whose write privileges were never
-- removed would turn a documented read-only path into a writable one. These
-- guards make that state impossible to apply silently: if any of them fails,
-- the migration aborts rather than leaving a role that can log in and write.
--
-- `has_table_privilege(..., 'INSERT')` is checked against the live table rather
-- than a role-membership summary, because privileges can arrive through PUBLIC,
-- an inherited role, or a default-privileges grant, and only the effective
-- check sees all three.

DO $$
DECLARE
    offending_tables TEXT;
BEGIN
    -- No write privilege on any canonical table.
    SELECT string_agg(quote_ident(table_name), ', ' ORDER BY table_name)
      INTO offending_tables
      FROM information_schema.tables
     WHERE table_schema = 'public'
       AND table_type = 'BASE TABLE'
       AND has_table_privilege('app_readonly',
                               quote_ident(table_schema) || '.' || quote_ident(table_name),
                               'INSERT,UPDATE,DELETE,TRUNCATE');

    IF offending_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'app_readonly has write privileges on: % — refusing to enable LOGIN. '
            'Revoke them first (scripts/grant_readonly.py) so the runtime role '
            'is genuinely read-only.', offending_tables;
    END IF;

    -- The role must not own any table: ownership implies the right to drop and
    -- alter it regardless of the privilege bits checked above.
    SELECT string_agg(quote_ident(c.relname), ', ' ORDER BY c.relname)
      INTO offending_tables
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_roles r ON r.oid = c.relowner
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND r.rolname = 'app_readonly';

    IF offending_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'app_readonly owns public tables: % — refusing to enable LOGIN. '
            'An owner can always ALTER/DROP its tables.', offending_tables;
    END IF;
END
$$;