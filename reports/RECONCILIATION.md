# Reconciliation — Live DB vs docs/04 §4 (Task 0 output, Phase 0 closeout;
# R1–R2 resolved 2026-09-28, see "Resolution log" below)

Source: `reports/schema_audit.json` (2026-09-28, 9/9 tables present, 40 chunks / 20 pubs;
re-run post-R1/R2: `status=MATCH errors=0 warnings=12 notes=6`, exit 0).
Endpoint final: session pooler `...pooler.supabase.com:5432` (see `reports/TLS_NOTES.md`).

## Requires action (Fase 1 pre-tasks, owner role) — RESOLVED 2026-09-28

- **R1 — Backfill `*_normalized` columns — RESOLVED.** Live `authors`, `institutions`,
  `funding` lacked the normalized columns (AC-DB-3). Fixed via
  `scripts/backfill_r1_r2.py`: TEMP staging + `COPY STDIN` from
  `data/*_cleaned.csv` + `UPDATE ... FROM` (no TRUNCATE/DELETE; junctions
  untouched), one transaction per table: authors 138/138, institutions 107/107,
  funding 33/33, 0 orphans, 0 NULLs → `SET NOT NULL` applied + indexes
  `idx_authors_name_norm`, `idx_institutions_name_norm`,
  `idx_funding_agency_norm`. Types follow spec (`VARCHAR(255)` authors, `TEXT`
  others) → zero drift warnings on the new columns. Accepted deviation (no action):
  CSV normalization is lowercase-only, commas kept (`"Rahayu, Yanti"` →
  `"rahayu, yanti"`); consistent dataset-wide so GROUP BY stays correct.
- **R2 — `publication_references.reference_id` — RESOLVED.** Live table used composite
  PK `(publication_id, reference_order)` (no FK references it — verified leaf).
  Swapped in one transaction: `DROP CONSTRAINT publication_references_pkey` →
  `ADD COLUMN reference_id BIGSERIAL PRIMARY KEY` (4120/4120 dense) →
  `ADD CONSTRAINT publication_references_natural_key UNIQUE (publication_id,
  reference_order)` preserving the natural key.
- **Grants — RESOLVED.** Role `app_readonly` did not exist → created `NOLOGIN`
  (LOGIN credential deferred to backend wiring / Task 3) via
  `scripts/grant_readonly.py`; `GRANT SELECT` on all 9 canonical tables + default
  privileges, verified via `has_table_privilege` (9/9 OK).

## Accepted — no action

- **R3 — text↔varchar drifts (12x).** Functionally equivalent in Postgres. Advisory only.
- **R4 — varchar surrogate IDs** (`keyword_id`, `funding_id`, `chunk_id` live=varchar,
  spec=BIGINT). Live consistently uses natural string IDs (`PUB000001_CH001`, …)
  matching CSVs and FKs. Accepted as prototype canonical (spec permits VARCHAR(64)).
- **R5 — Extra columns tolerated:** `publications.{source_title,link,issn,search_text}`,
  `funding.source_text`, `chunks.source_type`. Ignored via retriever whitelist.
- **R6 — Chunk ratio 40/20** matches blueprint exactly. Role has SELECT on all 9 tables.

## Gate verdict

Task 0 behaved as designed: connected read-only, dumped the full schema, and
returned exit 2 with a classified diff instead of a false "exact match".
Phase 0 acceptance items (build + audit execution) are met; R1–R2 ride as the
first two items of Fase 1 (both need the owner role + re-grant SELECT afterwards).

## Resolution log 2026-09-28 (build mode, owner session as `postgres`)

Executed exactly per plan, no TRUNCATE/DELETE/DROP TABLE. New committed scripts:
`scripts/backfill_r1_r2.py` (R1+R2, per-table transactions, idempotent re-runnable)
and `scripts/grant_readonly.py` (reusable re-grant helper for Task 1/8/8.5 per docs/08).
Re-ran `python scripts/verify_schema.py --dsn-env DB_URL --out-dir reports` →
`status=MATCH errors=0 warnings=12 notes=6`, exit 0. Remaining 12 warnings are the
accepted R3 text↔varchar drifts; 6 notes are the tolerated R4/R5 extras. `root.crt`
parked during local runs, restored after (verified `True`). Nothing committed yet.
