# Phase 0 Goals — Baseline Repository, Environment & Schema Audit

**Branch:** `phase0/baseline` · **Status:** COMPLETE (2026-09-28, audit executed)
**Normative refs:** `docs/11 Roadmap.md §Fase 0`, `docs/10 Implementation Plan.md §Task 0`, `docs/04 Database Schema.md §4 + §10`

## Goals (must all be met before Fase 1)

1. **Scaffold layout** — `backend/app/{routers,services,models,db,core}/`, `database/migrations/`, `scripts/`, `tests/`, `docker/` (+ `.gitkeep` so empty dirs are committable).
2. **Environment contract** — `.env.example` (DB_URL, Ollama host, model IDs, timeouts; placeholders only) + `.gitignore` protecting real `.env`.
3. **Dependency lock** — `backend/requirements.txt` pinned (FastAPI, uvicorn, Pydantic v2, asyncpg, psycopg, sqlglot, python-dotenv).
4. **Container definitions** — `docker-compose.yml` (`backend` + `ollama`) + `docker/backend.Dockerfile` (python:3.11-slim). No Postgres service (external live DB), no frontend.
5. **Task 0 schema audit** — `scripts/verify_schema.py`: read-only `information_schema` introspection of the 9 canonical Silver tables vs `docs/04 §4`; stdout diff + `reports/schema_audit.{json,md}`; exit `0` match / `2` mismatch / `1` connection error.

## Acceptance gate

- [x] `docker compose build` exits clean, no secrets baked in image.
- [x] `python scripts/verify_schema.py` connects via `DB_URL`, dumps all columns, confirms match/mismatch vs `docs/04 §4` (result: classified MISMATCH, see `reports/RECONCILIATION.md`).

## Explicit non-goals (deferred)

Task 1 DDL (`CREATE EXTENSION vector`, `ALTER chunks ADD embedding`, HNSW), edge/Gold DDL + materialization, FastAPI app code (Task 2), Ollama model pull (Task 3). Phase 0 touches the live DB with `SELECT` only.
