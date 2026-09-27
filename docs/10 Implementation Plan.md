# Implementation Plan — Urutan Build Menuju End-to-End (Hybrid Master Blueprint)

**Document Version:** 3.2.0 (Consolidated Hybrid Master Blueprint)  
**Status Date:** 2026-09-28  
**Authoritative Context:** Aligned with `README.md` and `docs/00` through `docs/12`  

> **Status Implementasi (Verifikasi Repositori 2026-09-28):**  
> Repositori saat ini hanya berisi dokumentasi Markdown (`README.md` dan `docs/00–12`). Direktori `backend/`, `frontend/`, `database/`, `scripts/`, `docker/`, dan `tests/` belum ada. Seluruh modul di bawah ini berstatus **PLANNED / NOT IMPLEMENTED** dan mendefinisikan panduan eksekusi linier berurutan.

---

## 0. Implementation Status Matrix

| Component | Current Status | Target Status | Gap & Action |
|---|---|---|---|
| **FastAPI Skeleton** | `NOT IMPLEMENTED` | MVP | Bangun skeleton, `/api/v1/ask`, `/api/v1/health`, validation, request_id, error handling (Task 2) |
| **API Contract v1** | `NOT IMPLEMENTED` | MVP | Kontrak `POST /api/v1/ask` dengan skema `evidence_objects`; `/api/query` resmi superseded (Task 10) |
| **Question Router** | `NOT IMPLEMENTED` | MVP | Router 4-rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`) + Entity Resolution Gate (Task 4) |
| **SqlRetriever** | `NOT IMPLEMENTED` | MVP | Text-to-SQL + AST validation `sqlglot` + role `app_readonly` (Task 5) |
| **VectorRetriever** | `BLOCKED BY INFRASTRUCTURE` | MVP | `chunks.embedding` kosong; generate `BAAI/bge-m3` 1024-dim + HNSW index (Task 1 & 6) |
| **Evidence Layer** | `NOT IMPLEMENTED` | MVP | `EvidenceUnifier` + `EvidenceRanker` + `EvidenceObject` enforcement (Task 7 & 9) |
| **Graph Materialization**| `NOT IMPLEMENTED` | MVP | Materialisasi `institution_collaboration` dan `author_collaboration` (Task 8) |
| **GraphRetriever** | `NOT IMPLEMENTED` | MVP | GraphRetriever + 4 templat Recursive CTE terparameterisasi T1–T4 (Task 8) |
| **Gold Analytics Engine**| `NOT IMPLEMENTED` | MVP | Komputasi `topics`, `topic_evolution`, dan `researcher_expertise` (Task 8.5) |
| **Answer Synthesizer** | `NOT IMPLEMENTED` | MVP | Analytical LLM Synthesizer + `CitationVerifier` + not_found deterministik (Task 9) |
| **Next.js UI** | `NOT IMPLEMENTED` | MVP | Dense Notion/Linear layout + collapsible sources + Dev Mode inspector (Task 11) |
| **E2E Verification** | `NOT IMPLEMENTED` | MVP | Pengujian 12 kueri end-to-end + baseline latensi (Task 12) |

---

## 1. Urutan Build Linier (Tasks 0 s.d. 12)

```
[Task 0: Schema Audit] ──> [Task 1: Batch Embeddings] ──> [Task 2: FastAPI & DB Skeleton]
                                                                     │
┌────────────────────────────────────────────────────────────────────┘
▼
[Task 3: Ollama Setup] ──> [Task 4: Router & Gate] ──> [Task 5 & 6: SQL & Vector Engine]
                                                               │
┌──────────────────────────────────────────────────────────────┘
▼
[Task 7: Evidence Unifier] ──> [Task 8: Graph & Gold Analytics] ──> [Task 9: Answer Synthesizer]
                                                                         │
┌────────────────────────────────────────────────────────────────────────┘
▼
[Task 10: API Endpoints] ──> [Task 11: Next.js UI] ──> [Task 12: E2E Verification]
```

### Task 0 — Verifikasi Skema Basis Data (Blocker Awal)
- Eksekusi kueri `information_schema.columns` pada Supabase real.
- Rekonsiliasi nama dan tipe kolom terhadap `04 Database Schema.md`.

### Task 1 — Pipeline Batch Embedding (`chunks.embedding`)
- Eksekusi script embedding `BAAI/bge-m3` (1024 dimensi, Float32) pada tabel `chunks`.
- Pembuatan indeks HNSW: `CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);`.

### Task 2 — FastAPI & Database Skeleton
- Setup project FastAPI, koneksi asyncpg pool dengan role `app_readonly`, middleware `X-Request-ID`.
- Implementasi endpoint `GET /api/v1/health`.

### Task 3 — Setup Ollama & Model Qwen2.5-Coder-7B
- Setup instance Ollama CPU-only, pulling model `Qwen2.5-Coder-7B-Instruct`.

### Task 4 — Question Router & Entity Resolution Gate
- Implementasi `QuestionRouter` untuk klasifikasi 4 rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`).
- Implementasi `EntityResolutionGate` untuk validasi nama author/institusi (`needs_clarification` jika >1 kandidat).

### Task 5 — SqlRetriever (Silver Relational)
- Text-to-SQL generator + validasi AST multi-lapis via `sqlglot` + enforce `LIMIT 50`.

### Task 6 — VectorRetriever (Silver Semantic)
- Embed kueri + pencarian kemiripan kosinus dengan klausa `DISTINCT ON (publication_id) LIMIT 8`.

### Task 7 — EvidenceUnifier & EvidenceRanker
- Normalisasi seluruh output retriever menjadi `EvidenceSet`.
- Ekstraksi fakta numerik dan pembuatan objek bukti dasar.

### Task 8 — Graph Edge Tables & Gold Analytics Materialization
- Materialisasi idempoten tabel `institution_collaboration` dan `author_collaboration`.
- Implementasi 4 templat recursive CTE (T1–T4).
- Materialisasi tabel Gold Layer: `topics`, `topic_evolution`, dan `researcher_expertise` ($\text{ExpertiseScore} = w_1 \cdot \text{Relevance} + w_2 \cdot \text{Productivity} + w_3 \cdot \text{Impact} + w_4 \cdot \text{Recency}$).

### Task 9 — Answer Synthesizer & CitationVerifier
- Synthesizer analitik LLM yang mengonsumsi nilai terverifikasi dan menghasilkan `evidence_objects`.
- Post-hoc `CitationVerifier` berbasis regex untuk membersihkan sitasi fiktif ke `unverified_citations`.
- Short-circuit deterministik pada 0 evidence items (`status: not_found`, < 200ms).

### Task 10 — API Endpoint `POST /api/v1/ask`
- Integrasi pipeline Task 4–9 ke dalam handler FastAPI `POST /api/v1/ask` dengan validasi Pydantic v2.

### Task 11 — Next.js Frontend (Dense Notion/Linear Style)
- Antarmuka web dua panel, tabel numerik monospace, collapsible sources, visualisasi `evidence_objects`, dan Dev Mode inspector.

### Task 12 — Verifikasi End-to-End & Baseline Latensi
- Pengujian 12 kueri uji lintas 4 rute dan pencatatan baseline latensi server CPU.
