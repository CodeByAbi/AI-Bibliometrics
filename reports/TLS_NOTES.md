# TLS & Connectivity Notes — Phase 0 (machine-specific, no secrets)

Date: 2026-09-28 · Target: Supabase Session Pooler (`*.pooler.supabase.com`, port 6543)

## Findings

1. **Pooler uses a private CA.** Server cert `CN=*.pooler.supabase.com` is issued by
   `Supabase Intermediate 2021 CA` (valid to 2030-03-11), absent from all public
   bundles (certifi 2026.5.20 checked). Chain verification against public CAs can
   never succeed.
2. **Stray `%APPDATA%\postgresql\root.crt`** (contains only ISRG X1/X2 roots) forces
   libpq `require`/`prefer` into verify-ca behavior (documented libpq behavior:
   "if a root CA file is present, verify as verify-ca"). Hence `certificate verify
   failed` in every SSL mode via psycopg2/libpq on this machine. Per-process
   `APPDATA` redirect does NOT help — libpq on Windows resolves the path via the
   Shell API, not the env var.
3. **Auth rejects the tenant/user** (`ENOTFOUND tenant/user ... not found`) on both
   plaintext and TLS-unverified paths via asyncpg (non-libpq driver). TLS handshake
   itself succeeds — connectivity is fine, the tenant/user is unknown to the pooler.
4. `.env` originally pointed at pooler host with **port 5432** (pooler = 6543;
   5432 is the direct-connection port on a different hostname).

## Implications

- **Docker backend (Fase 2+) is unaffected**: the image has no `root.crt`, so
  `sslmode=require` behaves as plain encrypted (Supabase's documented pooler posture).
- **Local script runs on this machine** need one of:
  - (a) Proper CA: download from Supabase dashboard (Database Settings → SSL
      Configuration, `prod-ca-2021.crt`) and either append to
      `%APPDATA%\postgresql\root.crt` or pass `--sslrootcert <file>` to
      `scripts/verify_schema.py` (supported), then use `verify-full`; or
  - (b) A non-libpq driver (asyncpg) with explicit TLS context; or
  - (c) Remove/relocate the stray `root.crt` if no other local tool needs it.
- **Status**: endpoint + tenant verified 2026-09-28 — see Resolved below.

## Resolved 2026-09-28 — final endpoint

- **Use session pooler `...pooler.supabase.com:5432`** (TablePlus-proven; transaction
  mode `:6543` also authenticates but session mode fits our workload and matches the
  user's working config). The earlier "tenant/user not found" was a one-character
  ref typo in `.env`, not a port problem.
- The stray `root.crt` (ISRG roots only) still forces libpq `require` into verify
  behavior on this machine: for local psycopg2 runs, park the file temporarily
  (restore after) or pass `--sslrootcert` with the dashboard-downloaded Supabase CA.

## Probes used (ephemeral, under system temp dir, not committed)

`ssl_probe.py`, `cert_inspect.py`, `tls_sanity.py`, `pg_cert_grab.py`,
`pg_cert_grab2.py`, `ssl_probe3.py`, `async_probe.py` + `pydeps/asyncpg-0.31.0`.
Re-run audit after `.env` fix with:
`python scripts/verify_schema.py` (reads `DB_URL` from process env).
