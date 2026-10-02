# Rencana Implementasi — Urutan Build Menuju End-to-End (Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.4 (Fase 6 GraphRetriever Sync — aturan bahasa: narasi Indonesia, teknis Inggris)
**Tanggal Status:** 2026-10-02
**Menggantikan:** `10 Implementation Plan.md` v3.6.3 (2026-09-29)
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`. Dokumen ini adalah execution-oriented layer — bukan pengganti `docs/03 System Architecture.md`, `docs/05 Retrieval Rag Design.md`, `docs/06 Api Design.md`, `docs/11 Roadmap.md`, atau `docs/12 Data Pipeline.md`. Detail kanonikal dirujuk, tidak diduplikasi.

> **Catatan Audit (2026-09-29 — wajib dibaca sebelum eksekusi):**
> 1. **Hierarki sumber kebenaran:** Level 1 (kode + migration/schema + tests + runtime terverifikasi) > Level 2 (`docs/03`, `docs/05`, `docs/06`, `docs/04`, `docs/08`) > Level 3 (`docs/11`, `docs/10`) > Level 4 (snapshot status lama).
> 2. **Legenda status baku:** `[DONE — VERIFIED]` (kode + test + evidence runtime ada) · `[IMPLEMENTED — VERIFICATION PENDING]` (kode + sebagian test ada, verifikasi runtime/E2E belum lengkap) · `[IN PROGRESS]` (sedang dikerjakan aktif) · `[NEXT]` (tugas berikutnya yang siap dikerjakan, preconditions terpenuhi) · `[BLOCKED]` (menunggu dependency) · `[PLANNED]` (terjadwal, preconditions belum terpenuhi) · `[POST-MVP]` · `[FUTURE]`. Label `[DONE]` tanpa evidence tidak digunakan.
> 3. **Rekonsiliasi Phase 4 / Task 6 vs downstream:** `VectorRetriever`, `CitationVerifier`, dan `VectorAnswerSynthesizer` sudah ada di kode dan hijau pada 200 tests (lihat Task 6 + Task 9a). Ini adalah **shared early component berlingkup Vector-only**, bukan penyelesaian Phase 5 (`EvidenceUnifier`) atau Phase 7 (`Unified AnswerSynthesizer`). Tidak ada renumbering task. Bedakan: component exists ≠ component integrated (semua route) ≠ phase complete ≠ E2E complete.
> 4. **Stale yang diketahui di luar dokumen ini (tidak diperbaiki di sini, hanya direferensikan):** `README.md` dan `docs/11 Roadmap.md` masih menyatakan Task 6 `PLANNED` / Phase 3 `IN PROGRESS` dengan 23 tests — stale terhadap realitas 177 tests. `docs/09 §5` masih menyatakan `chunks.embedding` + HNSW `PENDING` dan `VectorRoute BLOCKED` — stale dan bertentangan dengan matriksnya sendiri. `docs/11 §2` dan `docs/12 §3` masih memuat baris `NEXT/PENDING Task 1a–1c/Task 8` yang bertentangan dengan klaim `DONE 40/40 + 254/484 edges` di dokumen yang sama.

> **Status Implementasi & Kesiapan Basis Data (sinkronisasi Level 1, 2026-09-29):**
> 1. **Database PostgreSQL & Vector Storage — `[DONE — VERIFIED]`:** 9 tabel relasional kanonikal + 40 chunk ber-embedding `vector(1024)` `BAAI/bge-m3` + indeks HNSW aktif + tabel edge `institution_collaboration` (254 edge) dan `author_collaboration` (484 edge). Lihat Task 0, 1, 8-edge.
> 2. **Cleaning & Cleaned Export — `[DONE — VERIFIED]`:** 9 file `data/*_cleaned.csv` + load Silver (lihat `docs/12 §3`).
> 3. **Kerangka FastAPI & DB Pool + Ollama (Task 2 & 3) — `[DONE — VERIFIED]`:** `backend/app/`, pool async, `GET /api/v1/health`, kontrak `POST /api/v1/ask`, middleware `X-Request-ID`, rate limiting, logging terstruktur, Ollama client.
> 4. **Slice Router + SQL + Vector + Graph + Evidence (Task 4 + 5 + 6 + 7 + 8 + 9a) — `[IMPLEMENTED — VERIFICATION PENDING]`:** `QuestionRouter` + `EntityResolutionGate`, `SqlRetriever` tervalidasi AST `sqlglot`, `VectorRetriever`, `GraphRetriever` (T1–T4 templat terparameterisasi), `VectorAnswerSynthesizer` + `GraphAnswerSynthesizer` + `CitationVerifier` + `EvidenceUnifier`/`EvidenceRanker`/`EvidenceSet`/`EvidenceItem` sudah terintegrasi untuk `SQLRoute`/`VectorRoute`/`GraphRoute` dan hijau pada 261 tests (unit + integration). Verifikasi runtime terhadap live DB + sign-off E2E (Task 12) masih pending. `HybridRoute` masih stub jujur `not_found`.
>
> ### Progress Tracker
> **DONE — VERIFIED:** Database setup · Prototype data preparation · Cleaning · Cleaned data export · Task 0 (Schema Audit) · Task 1a–1d (Prepare, Generate, Store pgvector, Validate HNSW) · Task 8-edge (Edge Materialization) · Task 2 (FastAPI Framework & DB Pool) · Task 3 (Ollama Client & Health).
> **IMPLEMENTED — VERIFICATION PENDING:** Task 4 (QuestionRouter & EntityResolutionGate) · Task 5 (SqlRetriever & AST gate) · Task 6 (VectorRetriever) · Task 7 (EvidenceUnifier & EvidenceRanker & EvidenceSet) · Task 8-retriever (GraphRetriever T1–T4 + GraphAnswerSynthesizer) · Task 9a (Vector/Graph-scoped CitationVerifier + Synthesizers) · Task 10-parsial (wiring `SQLRoute`/`VectorRoute`/`GraphRoute` di `POST /api/v1/ask`).
> **NEXT:** Task 8.5 (Gold Analytics) / Task 9-full (Unified AnswerSynthesizer).
> **PLANNED (BLOCKED sampai NEXT selesai):** Task 8.5 (Gold Analytics) · Task 9-full (Unified AnswerSynthesizer) · Task 10-full · Task 11 (Frontend) · Task 12 (E2E 12 queries).
## 0. Matriks Status Implementasi

| Komponen | Status Saat Ini | Status Target | Gap & Aksi |
|---|---|---|---|
| **Baseline Database (9 tabel kanonikal)** | `[DONE — VERIFIED]` | Baseline Prototipe | PostgreSQL memuat 9 tabel prototipe; tidak perlu membuat DB dari nol. Evidence: Task 0. |
| **Pembersihan Data (Cleaning) Scopus** | `[DONE — VERIFIED]` | Baseline Prototipe | Sesuai `docs/12 §3`; output `data/*_cleaned.csv` (9 file), sudah ter-load Silver. |
| **Kerangka FastAPI + DB Pool** | `[DONE — VERIFIED]` | MVP | `backend/app/`, pool `asyncpg`, `GET /api/v1/health`, kontrak `POST /api/v1/ask`, middleware `X-Request-ID`, rate limiting, logging (Task 2). |
| **Ollama + Qwen2.5-Coder-7B** | `[DONE — VERIFIED]` | MVP | Health probe + embedding fallback path (Task 3). Sisa: pin versi di `requirements.txt` (Known Gap). |
| **QuestionRouter + EntityResolutionGate** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP | 4-route + gate ada + unit/integration hijau; E2E sign-off pending (Task 4). |
| **SqlRetriever + sqlglot AST gate** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP | Text-to-SQL + whitelist + `LIMIT 50` ada; E2E pending (Task 5). |
| **VectorRetriever + Online Embedding** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP | `bge-m3` + `<=>` + `DISTINCT ON` + `LIMIT 8` + threshold `>= 0.65` + `filters_ignored` ada; runtime-DB checklist pending (Task 6). Inti unit+integration `[DONE — VERIFIED]`. |
| **CitationVerifier + VectorAnswerSynthesizer (Task 9a, Vector-scoped)** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP-parsial | Regex post-hoc + `unverified_citations` + short-circuit `not_found` untuk `VectorRoute`/`SQLRoute` ada; unifikasi semua-route belum (Task 9a vs 9-full). Diketahui: `cite_year` diparse tapi belum dibandingkan — dicatat sebagai Known Gap, bukan blocker. |
| **EvidenceUnifier + EvidenceRanker + EvidenceSet** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP | `unifier.py` + `ranker.py` + `models.py` ada; unit 21 + integration 9 hijau; explicit `EvidenceSet` gate + `evidence_unify_ms` di `ask.py`; live E2E 12-query pending (Task 12). `from_graph sources` resolved di Fase 6. |
| **Materialisasi Graf (Edge Tables)** | `[DONE — VERIFIED]` | MVP | `institution_collaboration` (254 edge) + `author_collaboration` (484 edge), idempoten, `CHECK (a < b)` (Task 8-edge). |
| **GraphRetriever (T1–T4)** | `[IMPLEMENTED — VERIFICATION PENDING]` | MVP | 4 templat Recursive CTE terparameterisasi + `GraphAnswerSynthesizer` + wiring `GraphRoute` di `ask.py` selesai; 19 unit + 6 integration tests hijau (Task 8-retriever). T4 = ego-BFS berbatas; pairwise path A↔B Fase 9. |
| **Gold Analytics (`topics`, `topic_evolution`, `researcher_expertise`)** | `[PLANNED]` | MVP | Belum ada DDL runtime/Gold job; `[BLOCKED]` sampai Task 8.5 jelas. |
| **Unified AnswerSynthesizer (semua route)** | `[PLANNED]` | MVP | Menunggu Task 8.5 (Task 9-full). |
| **API `POST /api/v1/ask` full wiring** | `[IN PROGRESS]` | MVP | `SQLRoute`/`VectorRoute`/`GraphRoute` wired + latensi; `HybridRoute` stub `not_found` (Task 10). |
| **UI Next.js** | `[PLANNED]` | MVP | Spec `docs/07` ada; implementasi belum (Task 11). |
| **Verifikasi E2E 12 queries** | `[PLANNED]` | MVP sign-off | Menunggu Task 7–10-full (Task 12). |

---

## 1. Urutan Build Linier (Tasks 0 s.d. 12)

```
[Task 0: Schema Audit] ──> [Task 1: Batch Embeddings] ──> [Task 2: FastAPI & DB Skeleton]
                                                                     │
┌────────────────────────────────────────────────────────────────────┘
▼
[Task 3: Ollama Setup] ──> [Task 4: Router & Gate] ──> [Task 5 & 6: SQL & Vector Engine (+ 9a parsial)]
                                                               │
┌──────────────────────────────────────────────────────────────┘
▼
[Task 7: Evidence Unifier] ──> [Task 8: GraphRetriever & Gold Analytics] ──> [Task 9-full: Unified Synthesizer]
                                                                         │
┌────────────────────────────────────────────────────────────────────────┘
▼
[Task 10-full: API Endpoints] ──> [Task 11: Next.js UI] ──> [Task 12: E2E Verification]
```

> Nomor task tidak diubah. Task 8 dipecah dokumentatif menjadi `8-edge` (DONE) vs `8-retriever` (NEXT); Task 8.5 tetap Gold; Task 9 dipecah menjadi `9a` (Vector-scoped, sudah ada) vs `9-full` (unified, PLANNED).

### Task 0 — Schema Audit `[DONE — VERIFIED]`

- **Status:** `[DONE — VERIFIED]`
- **Objective:** Membuktikan 9 tabel Silver prototipe cocok dengan `docs/04 Database Schema.md` sebelum task lain berjalan.
- **Why This Exists:** Tanpa baseline skema yang MATCH, seluruh retriever (SQL/Vector/Graph) tidak memiliki kontrak kolom yang stabil.
- **Preconditions / Dependencies:** PostgreSQL 15+ eksternal provisioned; kredensial internal tersedia; `docs/04 §2–§5` sebagai referensi kolom/tipe.
- **Scope:** Eksekusi `scripts/verify_schema.py` (`information_schema.columns`, `table_schema='public'`); rekonsiliasi nama kolom + tipe 9 tabel kanonikal.
- **Out of Scope:** Embedding, HNSW, edge tables, Gold (milik Task 1/8/8.5). Tidak membuat DB dari nol.
- **Implementation Surface:** `scripts/verify_schema.py`; `database/migrations/` sebagai pembanding; `docs/04`.
- **Data / Database Dependency:** Tabel `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` (kolom teks). Role baca saja cukup.
- **API / Contract Dependency:** Tidak ada.
- **Security / Guardrails:** Koneksi read-only; tidak ada DDL/DML dari script audit.
- **Test & Verification:** Unit: `pytest tests/` terkait schema (bagian koleksi 200); runtime: `python scripts/verify_schema.py` → `0 errors` (MATCH).
- **Acceptance Criteria:** Audit melaporkan MATCH dengan 0 errors terhadap `docs/04`.
- **Evidence of Completion:** Output `verify_schema.py` (0 errors); 9 tabel terkonfirmasi di `information_schema`.
- **Downstream Impact:** Unlock Task 1 (kolom `chunks` sebagai input embedding) dan Task 2 (pool + health).
- **Known Gaps:** Tidak ada.

### Task 1 — Pipeline Batch Embedding (`chunks.embedding`) `[DONE — VERIFIED]`

- **Status:** `[DONE — VERIFIED]` (sub-task 1a–1d semuanya DONE)
- **Objective:** Mengisi `chunks.embedding vector(1024)` + metadata + indeks HNSW untuk `VectorRoute`.
- **Why This Exists:** `VectorRetriever` (Task 6) tidak dapat berjalan tanpa vektor dense + indeks kosinus.
- **Preconditions / Dependencies:** Task 0 MATCH; ekstensi `pgvector` tersedia; model `BAAI/bge-m3` dapat diunduh sekali (offline batch, CPU).
- **Scope:**
  - **1a Prepare (DONE):** seleksi + validasi 40 chunk; format input terkunci `Title: {title}\nAbstract: {abstract}` (`docs/12 §4`).
  - **1b Generate (DONE):** batch `BAAI/bge-m3`, 1024-dim Float32, batch 32, idempotent resume.
  - **1c Store (DONE):** `ALTER TABLE chunks ADD embedding vector(1024)` + `embedding_model DEFAULT 'BAAI/bge-m3'` + `embedding_version DEFAULT 'v1.0'` + `embedding_dimension DEFAULT 1024`; insert 40 vektor.
  - **1d Validate (DONE):** `SELECT COUNT(*) FROM chunks WHERE embedding IS NULL` = `0`; 100% join ke publikasi asal; HNSW + `idx_chunks_pub_id` aktif dan teruji `<=>`.
- **Out of Scope:** Online query embedding (milik Task 6 via `backend/app/services/embedding.py`). Tidak mengubah 9 tabel Silver selain kolom vektor `chunks`.
- **Implementation Surface:** `scripts/embed_chunks.py` (`--model BAAI/bge-m3 --dimension 1024 --batch-size 32 --commit-every 100 --resume`); `database/migrations/001_vector_and_chunks_schema.sql` (17 baris: ekstensi + kolom + HNSW `m=16, ef_construction=64` + `idx_chunks_pub_id`); `docs/04 §5`, `docs/12 §4`.
- **Data / Database Dependency:** `chunks(chunk_id, publication_id, chunk_text, section='title_abstract', embedding, embedding_model/version/dimension)`; indeks `idx_chunks_embedding_hnsw USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`; `ANALYZE chunks` pasca-index.
- **API / Contract Dependency:** Tidak ada (offline job).
- **Security / Guardrails:** Job offline memakai role bermigrasi; runtime read path tetap `app_readonly`; re-`GRANT SELECT` setelah DDL (lihat `docs/08 §1.1`, `scripts/grant_readonly.py`).
- **Test & Verification:** Unit/integration: `tests/test_phase1_validation.py` (10 tests) + bagian koleksi 200; runtime: `SELECT COUNT(*)` NULL = 0 + uji `<=>` mengembalikan ranking.
- **Acceptance Criteria:** 40/40 chunk terisi; HNSW + `idx_chunks_pub_id` ada di `\di`; kueri `<=>` valid.
- **Evidence of Completion:** Migrasi 001 teraplikasi; 40 vektor + indeks terverifikasi; `embed_chunks.py --resume` idempoten.
- **Downstream Impact:** Unlock Task 6 (`VectorRetriever` online). Tidak unlock Graph/Gold.
- **Known Gaps:** `requirements.txt` belum mem-pin `sentence-transformers/torch` (TBD `docs/09 TBD-4`) — berisiko pada fresh env; tidak mengubah status DONE karena evidence runtime batch sudah ada.

### Task 2 — Kerangka FastAPI & Database `[DONE — VERIFIED]`

- **Status:** `[DONE — VERIFIED]`
- **Objective:** Menyediakan serving skeleton + DB pool + kontrak API fondasi.
- **Why This Exists:** Semua route (Task 4–6, 8) membutuhkan lifecycle pool, tracing, dan envelope error yang sama.
- **Preconditions / Dependencies:** Task 0 DONE; `DB_URL` eksternal; `docs/06 Api Design.md §2–§5`.
- **Scope:** Struktur modular (`routers/`, `services/`, `models/`, `core/`, `db/`); pool `asyncpg` dengan `SET search_path = public` + `statement_timeout = 10s` per checkout; middleware `X-Request-ID` (UUIDv4); IP rate limiter; logging terstruktur; `GET /api/v1/health`; validasi Pydantic v2 `AskRequest`/`AskResponse`; error envelope `{request_id, error{error_type, message, status_code}}`.
- **Out of Scope:** Logika retriever/synthesizer (Task 4–9). Tidak menambah endpoint di luar `/api/v1/ask` + `/api/v1/health` (`/api/query` tetap SUPERSEDED).
- **Implementation Surface:** `backend/app/main.py`; `backend/app/core/{config,errors,logging,middleware}.py`; `backend/app/db/pool.py` (`statement_timeout 10000ms` + `command_timeout`); `backend/app/routers/{ask,health}.py` (skeleton); `backend/app/models/{ask,errors,health}.py`; `docker-compose.yml` (`backend` + `ollama`; PG eksternal); `.env.example` (`DB_URL`, timeouts).
- **Data / Database Dependency:** Pool ke 9 tabel Silver via `app_readonly` (SELECT-only); `SET search_path=public`; `statement_timeout='10s'` ganda (server-side pool + `asyncio.wait_for` di retriever).
- **API / Contract Dependency:** `AskRequest{question[3..1000], filters{year/year_from/year_to[1900..2026], country, author_name, institution_name, topic_name, document_type, keyword}, developer_mode}`; `AskResponse{request_id, status: ok|not_found|needs_clarification|error, route: SQLRoute|VectorRoute|GraphRoute|HybridRoute, answer, evidence_objects, sources, candidates?, filters_ignored, answered_via_fallback, unverified_citations, debug?}`; `EvidenceObject{claim, metric, value, period, sources[], confidence[0..1]}` (frozen); `EvidenceSourceRef{publication_id, doi?, eid?, title?, year?}`. Lihat `docs/06 §5`.
- **Security / Guardrails:** `app_readonly` (tanpa INSERT/UPDATE/DELETE/TRUNCATE/CREATE/ALTER); parameterized queries (tidak ada konkatenasi string user); `search_path=public`; timeout 10s; sanitized error (`error_type` saja); log `request_id` tanpa secrets (`docs/08 §1–§4`).
- **Test & Verification:** Unit/integration: `test_middleware_and_errors.py` (3) + `integration/test_health_endpoint.py` (2) + `integration/test_db_pool.py` (3, permission + timeout) dalam koleksi 200; runtime: `GET /api/v1/health` melaporkan `database{connected, app_readonly, silver/gold/pgvector_ready}`.
- **Acceptance Criteria:** Health + pool + tracing + rate-limit + envelope error bekerja; `POST /api/v1/ask` skeleton tervalidasi Pydantic.
- **Evidence of Completion:** File di atas ada; tests pool/health/middleware hijau.
- **Downstream Impact:** Unlock Task 3 (Ollama health) dan Task 4–6 (wiring retriever).
- **Known Gaps:** Ketidakkonsistenan dokumentasi rate-limit (`60 rpm` di versi lama Plan vs `20/min/IP` di `docs/08 §3`/`README`) — implementasi mengikuti kode aktual; Plan tidak menciptakan angka baru. `TBD-1 asyncpg vs psycopg3` (`docs/09`) tetap TBD; kode memakai `asyncpg`.

### Task 3 — Setup Ollama & Model Qwen2.5-Coder-7B `[DONE — VERIFIED]`

- **Status:** `[DONE — VERIFIED]`
- **Objective:** Menyediakan LLM client + health probe + jalur fallback embedding.
- **Why This Exists:** Synthesis (Task 9) dan fallback query-embedding (Task 6) membutuhkan Ollama yang terisolasi dengan timeout eksplisit.
- **Preconditions / Dependencies:** Task 2 DONE; binary Ollama + model `qwen2.5-coder:7b-instruct` (GGUF Q4_K_M, CPU) dapat di-pull; `docs/09 §2`.
- **Scope:** Async Ollama client (health probe model + embedding service); integrasi pada `GET /api/v1/health` (`llm{Qwen2.5-Coder-7B-Instruct/Ollama}`, `embedding{BAAI/bge-m3/1024}`); timeout generasi 8s (`OLLAMA_TIMEOUT_S=8`).
- **Out of Scope:** Prompt grounding itu sendiri (Task 9). Tidak menambah model AI baru.
- **Implementation Surface:** `backend/app/services/ollama.py`; `backend/app/services/embedding.py:_embed_via_ollama` (`/api/embed` → `/api/embeddings`); `backend/app/core/config.py:40-46` (`OLLAMA_HOST`, `LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`); `.env.example`.
- **Data / Database Dependency:** Tidak ada.
- **API / Contract Dependency:** `GET /api/v1/health` menampilkan status LLM + embedding (lihat `docs/06 §6`).
- **Security / Guardrails:** Timeout eksplisit; tidak membocorkan raw error Ollama ke klien (envelope saja).
- **Test & Verification:** Bagian koleksi 200 (health + embedding fallback dual-path); runtime: health melaporkan model siap.
- **Acceptance Criteria:** Health check mendeteksi Ollama + model; fallback embedding path teruji.
- **Evidence of Completion:** File client + config + health fields ada; test fallback hijau.
- **Downstream Impact:** Unlock Task 6 (fallback path) dan Task 9 (synthesis path).
- **Known Gaps:** Pin versi Ollama/model belum di `requirements.txt` (`docs/09 TBD-4`); `TBD-5 local vs Ollama query-embed` DECIDED di Task 6 (local primer, Ollama fallback; backend pelayan terekspos via `embedding_backend` di debug `VectorRoute`) dan dikunci formal di `docs/09 §9` — tersisa parity check distribusi fallback vs gate 0.65 di Task 12.

### Task 4 — QuestionRouter & EntityResolutionGate `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit + integration hijau; E2E sign-off di Task 12 pending)
- **Objective:** Mengklasifikasikan intent ke 1 dari 4 route dan mengikat entitas ke ID kanonikal.
- **Why This Exists:** Tanpa routing deterministik + gate entitas, retriever salah sasaran dan sitasi tidak ter-grounding.
- **Preconditions / Dependencies:** Task 2 DONE; 9 tabel Silver + kolom `*_normalized` tersedia; `docs/05 §3`, `docs/02 FR2`, `docs/08 §2.2`.
- **Scope:** `QuestionRouter.classify_route` (aturan regex/keyword deterministik <50ms + LLM fallback ringan ~1.5s bila uncertain; emit Pydantic `RouteDecision{route, reasoning, answered_via_fallback, extracted_entities}` + `YearFilter{op: eq|gt|gte|lt|lte|between}`); `EntityResolutionGate.resolve_entities` (`lower+trim → exact → ILIKE`; 0 hits → `not_found`, >1 → `needs_clarification` + candidates, 1 → bind ID; tidak ada raw-string binding).
- **Out of Scope:** SQL generation (Task 5), vector search (Task 6), graph traversal (Task 8). Tidak menambah route baru.
- **Implementation Surface:** `backend/app/services/router.py` (618 baris: `RouteType`, `RouteDecision`, `EntityResolutionResult`, `YearFilter`); wiring di `backend/app/routers/ask.py:54-80` (`routing_ms`, `entity_resolution_ms`).
- **Data / Database Dependency:** Lookup `authors(author_name_normalized)`, `institutions(institution_name_normalized)`, `funding(funding_agency_normalized)` via `app_readonly` + timeout 10s.
- **API / Contract Dependency:** Mempengaruhi `AskResponse{route, candidates?, answered_via_fallback, debug{route_reasoning}}`; `FilterParams` sebagai `Literal`-constrained ops (lihat `docs/06 §5`).
- **Security / Guardrails:** Operator year sebagai `Literal` (tidak pernah dikonkatenasi ke SQL — mapping ke bound-param di `SqlRetriever`); `ILIKE` parameterized; `needs_clarification` mencegah binding ambigu.
- **Test & Verification:** Unit: `unit/test_router.py` (29 + 1 parametrize ≈ 20 kasus) dalam 177; integration via `test_ask_endpoint.py` (18); runtime pending: matriks 4-route di live DB; E2E: Task 12.
- **Acceptance Criteria:** 4-route terklasifikasi deterministik; entitas 0/1/>1 berperilaku `not_found/bind/needs_clarification`; `answered_via_fallback` jujur saat fallback dipakai.
- **Evidence of Completion:** `router.py` + wiring latensi ada; test router hijau.
- **Downstream Impact:** Unlock Task 5, 6, 8-retriever (semua membutuhkan `RouteDecision` + resolved IDs).
- **Known Gaps:** Aturan fallback terperinci (`uncertain + structured filter → Hybrid, else Vector + answered_via_fallback=true`, `docs/05 §3`) perlu verifikasi E2E lintas route.

### Task 5 — SqlRetriever (Relasional Silver) `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit + integration hijau; E2E pending)
- **Objective:** Menjawab agregasi/filter/Top-N faktual via Text-to-SQL tervalidasi AST.
- **Why This Exists:** Jalur terstruktur adalah satu-satunya sumber angka terverifikasi (counts, rankings, distributions) untuk `EvidenceObject`.
- **Preconditions / Dependencies:** Task 4 DONE-parsial (route + resolved IDs); Task 0 DONE (whitelist kolom stabil); `docs/05 §5.1`, `docs/02 FR3`, `docs/08 §2`.
- **Scope:** Text-to-SQL generator + multi-tier `sqlglot` AST gate (root harus `Select`; table/column whitelist 9 tabel; destructive-keyword blacklist; Aggregate-Shape Check `COUNT/SUM/AVG/GROUP BY`; Double-Count Check `COUNT(DISTINCT publication_id)` pada junction joins; `LIMIT 50` untuk non-aggregate); 1x retry dengan konteks error AST; gagal persisten → `HTTP 422 {error_type: sql_generation_failed}` tanpa bocor raw DB error.
- **Out of Scope:** Vector/Graph/Hybrid. Tidak ada DDL/DML.
- **Implementation Surface:** `backend/app/services/retrievers/sql_retriever.py`; `backend/app/services/retrievers/sql_security.py` (termasuk komentar Phase 6 Graph); `backend/app/services/synthesizer/answer.py:SqlAnswerSynthesizer`; wiring `ask.py` (`SQLRoute`).
- **Data / Database Dependency:** 9 tabel Silver; `COUNT(DISTINCT publication_id)` wajib pada join `pub_author`/`pub_institution`; `LIMIT 50`; timeout 10s; `app_readonly`.
- **API / Contract Dependency:** Berkontribusi ke `evidence_objects[]` + `sources[]{source_type: sql}` + `debug{sql_executed}` (developer_mode).
- **Security / Guardrails:** AST whitelist + blacklist + `search_path=public` + parameterized + timeout + `app_readonly` (`docs/08 §1–§2`).
- **Test & Verification:** Unit: `unit/test_sql_retriever.py` (34) + `unit/test_sql_security.py` (23 + 3 parametrize: 7+5+4 kasus injeksi) dalam 177; integration: `test_ask_endpoint.py`; E2E: Task 12 (Top-N/aggrechips harus cocok dengan SQL langsung).
- **Acceptance Criteria:** 100% lolos AST validator + read-only enforcement; tidak ada destructive SQL lolos; agregasi junction memakai `COUNT(DISTINCT)`.
- **Evidence of Completion:** File retriever + security + tests injeksi hijau.
- **Downstream Impact:** Unlock Task 7 (SQL rows → `EvidenceSet`) dan Task 10-parsial (`SQLRoute` wired).
- **Known Gaps:** Verifikasi E2E angka-vs-SQL langsung belum (Task 12).

### Task 6 — VectorRetriever (Silver Semantic `chunks`) `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` — inti unit + integration `[DONE — VERIFIED]`; checklist runtime-DB pending (lihat Evidence).
- **Objective:** Menjawab discovery semantik ID/EN via embedding + pgvector HNSW dengan dedup + gate deterministik.
- **Why This Exists:** Satu-satunya jalur konseptual (topik/konsep, bukan keyword eksak) di atas `chunks.embedding`.
- **Preconditions / Dependencies:** Task 1 DONE (40 vektor + HNSW); Task 3 DONE (fallback Ollama); Task 4 DONE-parsial (route); `docs/05 §5.2`, `docs/02 FR4`.
- **Scope:** Online query embedding (`SentenceTransformer` lokal via threadpool + `safetensors`, fallback Ollama `/api/embed` → `/api/embeddings`, dimension integrity check = 1024, empty guard); retrieval pgvector `<=>` dengan `DISTINCT ON (p.publication_id)` (best chunk per publikasi) + outer `ORDER BY similarity_score DESC` + `LIMIT 8` (= 8 publikasi unik); cosine gate `>= 0.65` (di bawah → `not_found`); structured filters bila didukung + tracking `filters_ignored`; parameterized SQL + `statement_timeout 10s`; zero-match short-circuit deterministik (`status: not_found`, 0 evidence, 0 LLM call, <200ms).
- **Out of Scope:** `EvidenceUnifier` generik (Task 7); Gold/reranker baru (tidak ada); perubahan threshold/indeks (terkunci).
- **Implementation Surface:** `backend/app/services/embedding.py` (152 baris: `_load_sentence_transformer`, `_embed_via_ollama`, `generate_query_embedding`, dim-check); `backend/app/services/retrievers/vector_retriever.py` (244 baris: `DEFAULT_THRESHOLD=0.65`, `DEFAULT_LIMIT=8`, `filters_ignored`, `vec_literal`, `DISTINCT ON`, timeout); `backend/app/routers/ask.py:195-245` (wiring + `vector_retrieval_ms/synthesis_ms/total_ms` + `developer_mode` diagnostics `scored_chunks`).
- **Data / Database Dependency:** `chunks.embedding vector(1024)` + `idx_chunks_embedding_hnsw (m=16, ef_construction=64, vector_cosine_ops)` + `idx_chunks_pub_id`; kueri kanonikal `SELECT DISTINCT ON (p.publication_id) … 1-(embedding <=> $1) AS similarity … WHERE similarity >= $1 ORDER BY p.publication_id, distance ASC … ORDER BY similarity_score DESC LIMIT 8` (`docs/05 §5.2`); cosine gate `>= 0.65` (`BAAI/bge-m3`).
- **API / Contract Dependency:** `AskResponse{route: VectorRoute, sources[]{source_type: vector, relevance_score, provenance}, filters_ignored[], debug{latency_breakdown}}`; filter `FilterParams` yang tak teresolusi wajib muncul di `filters_ignored` (jujur, bukan diam-diam diabaikan).
- **Security / Guardrails:** `app_readonly` + parameterized (threshold/filters/IDs/limit sebagai `$N`); `vec_literal` hanya float terformat (`f"{v:.8f}"`, debug diredaksi `[vector_1024d]`); timeout ganda (server-side pool + `asyncio.wait_for` → `DBTimeoutError`); teks retrieval = `UNTRUSTED DATA` (tidak pernah dieksekusi).
- **Test & Verification:** Unit: `unit/test_embedding.py` (5: lokal 1024-d, mismatch, Ollama fallback, dual-fail) + `unit/test_vector_retriever.py` (5: CTE, zero-match, filter binding, synthesis ok/not_found); integration: `integration/test_vector_ask_endpoint.py` (4: ok/not_found/diagnostics/filters) dalam 177; runtime pending: `SELECT COUNT(*) WHERE embedding IS NULL = 0` + `<=>` live + latensi `vector_retrieval_ms`; E2E: Task 12 (Top-8 unik, threshold behavior).
- **Acceptance Criteria:** `LIMIT 8` = 8 publikasi unik (dedup terbukti); skor `< 0.65` → `not_found` deterministik; `filters_ignored` akurat; tidak ada query tak-parameterized dari teks user.
- **Evidence of Completion:** File embedding + retriever + wiring latensi ada; 177-test collection mencakup vector suites hijau. Sisa runtime checklist dicatat di Known Gaps (bukan klaim DONE penuh).
- **Downstream Impact:** Unlock Task 9a (sintesis Vector) dan Task 10-parsial (`VectorRoute` wired). Tidak unlock Graph/Hybrid/Gold.
- **Known Gaps:** (1) Checklist runtime live-DB belum dilampirkan sebagai artefak; (2) `vec_literal` diinterpolasi (float-only, aman de facto) — tetap dicatat untuk audit keamanan berikutnya; (3) `requirements.txt` belum mem-pin `sentence-transformers/torch` (TBD `docs/09`).
- **Rollback / Failure Consideration:** Bila model lokal gagal load → fallback Ollama; bila keduanya gagal → error envelope (bukan jawaban halusinasi); bila HNSW hilang → kueri tetap benar tapi lambat ( dramatis di >100K; prototipe 40 chunk tidak kritis).

### Task 7 — EvidenceUnifier & EvidenceRanker `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (kode + unit hijau; live E2E 12-query di Task 12 pending)
- **Objective:** Menormalisasi heterogen rows/chunks/edges menjadi `EvidenceSet` kanonikal + ranking deterministik sebelum LLM.
- **Why This Exists:** Invariant arsitektur: tidak ada row/chunk/edge mentah yang mencapai LLM (`docs/03 §0.3`, `docs/05 §4`). Tanpa ini, Task 9-full tidak boleh selesai.
- **Preconditions / Dependencies:** Task 4 + 5 + 6 ada (sumber SQL rows + vector chunks); `docs/05 §4` (`EvidenceObject` 6-field + `EvidenceSourceRef`) sebagai kontrak.
- **Scope:** `EvidenceUnifier` (`from_sql` 5-case + `from_vector` + `from_graph` + `from_analytics` + `unify()` multi-source dedup); dedup `publication_id` + merge provenance; `EvidenceRanker` deterministik (objects by `-confidence,-value,metric,claim`; sources by `-relevance,-year,title,id`; items by `-score,-confidence,-year,title,source_id`); confidence: SQL/Graph/Analytics = 1.0, Vector = `round(similarity,4)`; serialisasi `to_metrics_json/to_chunks_text/to_prompt_context/to_untrusted_evidence_block`; preservasi `filters_ignored`.
- **Out of Scope:** Synthesis narasi (Task 9); perubahan skema `EvidenceObject` (terkunci `docs/06 §5`).
- **Implementation Surface:** `backend/app/services/evidence/unifier.py`; `backend/app/services/evidence/ranker.py`; `backend/app/services/evidence/models.py` (`EvidenceItem`, `EvidenceSet`); `backend/app/services/evidence/formatting.py` (`format_citation` re-export + `format_period`); wiring di `backend/app/services/synthesizer/answer.py` (`SqlAnswerSynthesizer`/`VectorAnswerSynthesizer` consume `EvidenceSet`, tidak menyentuh raw rows, `evidence_set` param + `is_empty` gate) + `backend/app/routers/ask.py` (explicit `EvidenceUnifier.from_sql/from_vector` gate + `evidence_unify_ms` + `is_empty` short-circuit + `_debug_evidence_set()` untuk developer_mode).
- **Data / Database Dependency:** Tidak ada DDL baru; konsumsi hasil Task 5/6 (+ kelak Task 8/8.5).
- **API / Contract Dependency:** Menegakkan `EvidenceObject{claim,metric,value,period,sources,confidence}` + `EvidenceSourceRef`; `AskResponse.debug.evidence_set` (baru) mengekspos `EvidenceSet` ternormalisasi saat `developer_mode=true`.
- **Security / Guardrails:** Output unifier diframing `UNTRUSTED DATA` (`=== BEGIN/END RETRIEVED EVIDENCE ===`); 0 evidence → short-circuit tanpa LLM; tidak ada path raw-row-to-LLM di production (`answer.py` hanya membaca `ev_set.evidence_objects/sources/items`).
- **Test & Verification:** Unit: `tests/unit/test_evidence.py` (21 tests: unifier 4-source + unify() dedup + ranker determinism + serialization + injection defense + format_citation_tag); Integration: `tests/integration/test_evidence_ask_endpoint.py` (9 tests: pool mock, SQLRoute/VectorRoute → EvidenceSet → AskResponse, zero-match short-circuit, needs_clarification, filters_ignored, developer_mode evidence_set + evidence_unify_ms, empty-set debug, determinisme 2x); E2E: `tests/e2e/test_e2e_12_queries.py` (mock 12 query + live gate di-skip tanpa `DB_URL`/`E2E_LIVE=1`).
- **Acceptance Criteria:** Semua retriever melewati unifier; tidak ada path yang bypass ke LLM; ranking deterministik (same input → same order); `EvidenceSet` terserialisasi sesuai `docs/05 §4`.
- **Evidence of Completion:** File unifier + ranker + models + tests 21+9 hijau + full suite 228 passed; live E2E gate tersedia via `E2E_LIVE=1 pytest -m e2e_live tests/e2e/`.
- **Downstream Impact:** Unlock Task 9-full + Task 10-full + Task 12 sign-off.
- **Known Gaps:** (1) `from_graph` `sources=[]` kosong (provenance hanya di `items`) — ditunda ke Fase 6 saat `GraphRetriever` hadir; (2) confidence vector = passthrough `round(similarity,4)` (bukan rescale 0.70-1.0) sesuai `docs/05 §4.1` raw similarity; (3) nama file/kelas mengikuti `docs/03/05/11` persis.
- **Rollback / Failure Consideration:** Bila unifier gagal → `status: error` envelope (bukan fallback ke raw rows).

### Task 8 — Graph Edge Tables & Gold Analytics Materialization

#### Task 8-edge — Edge Materialization `[DONE — VERIFIED]`

- **Status:** `[DONE — VERIFIED]`
- **Objective:** Menyediakan derived edge tables idempoten sebagai fondasi `GraphRetriever`.
- **Why This Exists:** MVP graph tanpa graph-DB terpisah — hanya PostgreSQL edge + CTE (`docs/04 §6`).
- **Preconditions / Dependencies:** Task 0 DONE (`pub_author`, `pub_institution` stabil).
- **Scope:** Materialisasi idempoten `institution_collaboration(institution_a, institution_b, weight, via_publication_ids)` + `author_collaboration(author_a, author_b, weight, via_publication_ids)` via self-join `a<b` (`LEAST/GREATEST`), `COUNT` + `ARRAY_AGG`; `CHECK (a < b)`; indeks `weight DESC`; re-`GRANT SELECT` ke `app_readonly`.
- **Out of Scope:** T1–T4 templates (8-retriever); Gold (8.5).
- **Implementation Surface:** `scripts/build_edges.py` (173 baris); `database/migrations/002_collaboration_edges_schema.sql` (30 baris); `docs/04 §6`, `docs/12 §5`.
- **Data / Database Dependency:** Sumber `pub_institution`/`pub_author`; hasil 254 + 484 edges; `via_publication_ids TEXT[]` provenance.
- **API / Contract Dependency:** Tidak langsung (konsumsi via 8-retriever).
- **Security / Guardrails:** Job offline idempoten (`TRUNCATE+INSERT` terkontrol); runtime read via `app_readonly`.
- **Test & Verification:** Bagian koleksi 200 (edge validation) + runtime: `CHECK` + `via_publication_ids` valid + count 254/484.
- **Acceptance Criteria:** Idempoten rerun sama; `CHECK (a<b)` lolos; provenance valid.
- **Evidence of Completion:** Migrasi 002 + `build_edges.py` + counts terverifikasi.
- **Downstream Impact:** Unlock Task 8-retriever.
- **Known Gaps:** Tidak ada.

#### Task 8-retriever — GraphRetriever T1–T4 `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit 19 + integration 6 hijau; live E2E Task 12 pending)
- **Objective:** Menjawab kolaborasi/jaringan via 4 templat parameterized + recursive CTE berbatas.
- **Why This Exists:** `GraphRoute` untuk co-author, institusi kolaborator, komposisi topik→institusi, path search.
- **Preconditions / Dependencies:** Task 8-edge DONE; Task 4 DONE-parsial (route); `docs/05 §5.3`, `docs/02 FR7`, `docs/04 §6`.
- **Scope:** `GraphRetriever` tanpa LLM-generated graph SQL — hanya T1 institution collaborators, T2 co-authors, T3 topic→institution composition, T4 bounded recursive-CTE path search (`max_hops=3`, `LIMIT 50`; T1 contoh `ORDER BY weight DESC LIMIT 20` di `docs/05` dipertahankan sebagai spesifikasi templat, bukan angka baru); setiap edge membawa `via_publication_ids` dan memperkaya metadata publikasi pada `EvidenceSet.sources`.
- **Out of Scope:** Apache AGE (POST-MVP Phase 9); Gold analytics (8.5); perubahan hop/limit (terkunci).
- **Implementation Surface:** `backend/app/services/retrievers/graph_retriever.py` (290 baris); `backend/app/services/synthesizer/answer.py:GraphAnswerSynthesizer`; `backend/app/services/evidence/unifier.py:from_graph`; wiring `backend/app/routers/ask.py` (`GraphRoute`).
- **Data / Database Dependency:** `institution_collaboration` + `author_collaboration`; `max_hops=3`; timeout 10s.
- **API / Contract Dependency:** `AskResponse{route: GraphRoute, sources[]{source_type: graph, provenance: via_publication_ids}}`.
- **Security / Guardrails:** LLM-free templates + parameterized + `app_readonly` + `search_path=public` + timeout (`docs/08 §2.2`).
- **Test & Verification:** Unit: `tests/unit/test_graph_retriever.py` (19 tests); Integration: `tests/integration/test_graph_ask_endpoint.py` (6 tests); E2E: Task 12.
- **Acceptance Criteria:** 4 templat parameterized lolos; hop >3 ditolak/clamped; provenance selalu ada.
- **Evidence of Completion:** File retriever + synthesizer + unifier + wiring + 25 tests hijau.
- **Downstream Impact:** Unlock `GraphRoute` di Task 10-full + Hybrid composition.
- **Known Gaps:** Verifikasi E2E live-DB (Task 12).
#### Task 8.5 — Gold Analytics (`topics`, `topic_evolution`, `researcher_expertise`) `[PLANNED]`

- **Status:** `[PLANNED]` (`[BLOCKED]` sampai Task 7 DONE + Task 8-retriever `[IMPLEMENTED — VERIFICATION PENDING]` — lihat `docs/05 §5.4`, `docs/11 Phase 6`)
- **Objective:** Menyediakan derived analytics read-only untuk `HybridRoute` + policy synthesis.
- **Why This Exists:** Emerging-topic detection + expertise ranking membutuhkan agregat prakomputasi, bukan komputasi on-the-fly.
- **Preconditions / Dependencies:** Silver kanonikal DONE; Task 7 (konsumsi); `docs/04 §7`, `docs/12 §6`.
- **Scope:** `topics(topic_name, cluster_keywords[10], representation_vector vector(1024)+HNSW, total_publications, total_citations)`; `topic_evolution(publication_count, citation_count, growth_score YoY, citation_acceleration d²C/dt², recency_weight, is_emerging)`; `researcher_expertise(ExpertiseScore = w1·Relevance + w2·Productivity + w3·Impact + w4·Recency, w=0.30/0.25/0.25/0.20, range 0–100 + h_index_topic + counts + coauthor_network_size)`; formula growth `(Nt−Nt−1)/max(1,Nt−1)`, `is_emerging ≥ 0.20 & N ≥ 10` (ikuti `docs/12 §6`, bukan angka baru).
- **Out of Scope:** Online training loop; perubahan bobot (terkunci defaults).
- **Implementation Surface (rencana):** `scripts/build_topics.py` + `scripts/score_expertise.py` (dirujuk `README §7.3`, belum ada file — `TBD / Needs Confirmation` untuk keberadaan runtime); DDL Gold di `docs/04 §7` (belum ada migrasi runtime — jangan dikarang).
- **Data / Database Dependency:** Gold read-only turunan Silver; `UNIQUE(topic,year)`, `UNIQUE(author,topic)` (lihat `docs/04`).
- **API / Contract Dependency:** Konsumsi via `HybridRetriever` → `sources[]{source_type: analytics}` + metric `growth_score|citation_acceleration|expertise_score`.
- **Security / Guardrails:** Gold read-only; re-`GRANT` setelah materialisasi (`docs/08 §1.1`).
- **Test & Verification:** Validasi formula + YoY + flag `is_emerging` pada dataset prototipe (direncanakan; belum ada suite).
- **Acceptance Criteria:** 3 tabel Gold terisi dari Silver kanonikal dengan formula terkunci; query Hybrid contoh (`docs/05 §5.4`) mengembalikan ranking deterministik.
- **Evidence of Completion (definisi):** Job + DDL + validasi counts terverifikasi.
- **Downstream Impact:** Unlock `HybridRoute` penuh + Task 9-full policy synthesis.
- **Known Gaps:** File job + migrasi Gold belum ada di repo — status PLANNED jujur; jangan diklaim DONE dari DDL dokumen saja.

### Task 9 — Answer Synthesizer & CitationVerifier

#### Task 9a — Vector-scoped Synthesizer + CitationVerifier `[IMPLEMENTED — VERIFICATION PENDING]`

- **Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (shared early component yang hidup di dalam Task 6 slice; bukan penyelesaian Phase 7)
- **Objective:** Sintesis ter-grounding untuk `SQLRoute`/`VectorRoute` + verifikasi sitasi post-hoc.
- **Why This Exists:** Membuktikan zero-hallucination loop pada 2 route tanpa menunggu Evidence/Gold.
- **Preconditions / Dependencies:** Task 5 + 6 DONE-parsial; `docs/05 §6–§7`, `docs/02 FR5`.
- **Scope:** `SqlAnswerSynthesizer` + `VectorAnswerSynthesizer.synthesize` (konversi retrieval → `EvidenceObject` dengan `metric=similarity_score`, `value/confidence=round(similarity,4)`, `sources=[EvidenceSourceRef]`, `format_citation` kanonikal, excerpt abstract); `CitationVerifier.verify` (regex `[Title, Year, DOI]` / `[Title, Year, no-doi]` + DOI match + Title-fallback, strip ke `unverified_citations` + `re.sub` cleanup); short-circuit deterministik 0 evidence → `status: not_found` (`"Data tidak ditemukan..."`, empty evidence/sources, 0 LLM call); konteks LLM terisolasi (`=== BEGIN/END RETRIEVED EVIDENCE ===`, `UNTRUSTED DATA`); kontradiksi disurfaced.
- **Out of Scope:** Unifikasi semua-route (9-full); `EvidenceSet` generik (Task 7).
- **Implementation Surface:** `backend/app/services/synthesizer/answer.py` (319 baris); `backend/app/services/synthesizer/citation.py` (125 baris: `CITATION_PATTERN`, `valid_dois`, `unverified_citations`); wiring `ask.py:195-245`.
- **Data / Database Dependency:** Tidak ada DDL; konsumsi hasil Task 5/6.
- **API / Contract Dependency:** Menghasilkan `answer + evidence_objects + sources + unverified_citations[]`; format sitasi terkunci `[Title, Year, DOI]` / `[Title, Year, no-doi]` (regex `docs/05 §7`: `\[([^,]+),\s*(\d{4}|n.d.),\s*(10\.\d{4,9}/…|no-doi)\]`).
- **Security / Guardrails:** Prompt-injection defense (retrieved text = `UNTRUSTED DATA`, `SYSTEM≠USER≠EVIDENCE`); short-circuit <200ms tanpa synthesis call; hallucinations diprune (0 unverified citations di `answer` final).
- **Test & Verification:** Unit: `unit/test_citation.py` (5) + vector synthesis cases dalam `test_vector_retriever.py` + `test_vector_ask_endpoint.py` dalam 177; E2E pending Task 12 (0 unverified citations + `not_found` saat kosong).
- **Acceptance Criteria:** Setiap sitasi inline cocok dengan `EvidenceSet` (DOI atau Title); sitasi fiktif terstrip ke `unverified_citations`; 0 evidence → `not_found` tanpa teks halusinasi.
- **Evidence of Completion:** File synthesizer + verifier + wiring + tests hijau untuk SQL/Vector.
- **Downstream Impact:** Membuktikan pola untuk 9-full; tidak unlock Graph/Hybrid synthesis.
- **Known Gaps:** (1) Regex `[^,]+` rapuh untuk judul berkomma (`docs/05` drift) — jangan diubah sepihak di Plan; (2) `cite_year` sekarang dipergunakan dengan strict match (tahun numerik wajib sama; `n.d.` tunduk pada pencocokan judul saja) — gap lama sudah dihapus, hanya catatan regex rapuh tersisa; (3) nama blok prompt `05:239-245` vs konvensi `RETRIEVED EVIDENCE` — ikuti `AGENTS.md` saat implementasi 9-full.

#### Task 9-full — Unified AnswerSynthesizer (semua route) `[PLANNED]`

- **Status:** `[PLANNED]` (`[BLOCKED]` sampai Task 7 DONE)
- **Objective:** Satu synthesizer yang mengonsumsi `EvidenceSet` terverifikasi dari semua retriever.
- **Why This Exists:** Tanpa unifikasi, Graph/Hybrid/Gold tidak memiliki jalur grounding yang sama.
- **Preconditions / Dependencies:** Task 7 DONE (unifier + ranker); Task 8-retriever + 8.5 (sumber tambahan); Task 9a sebagai pola.
- **Scope:** Konsumsi `EvidenceSet`; `evidence_objects` untuk semua route; `CitationVerifier` yang sama; `not_found`/`insufficient_evidence` deterministik.
- **Out of Scope:** Perubahan format sitasi/kontrak API (terkunci).
- **Implementation Surface (rencana):** Perluasan `synthesizer/answer.py` + unifier adapter; tanpa layer/framework baru (LangChain/LlamaIndex tetap dilarang, `docs/09`).
- **Data / Database Dependency:** Tidak ada DDL baru.
- **API / Contract Dependency:** `AskResponse` final untuk 4 route (lihat `docs/06 §5`).
- **Security / Guardrails:** Sama dengan 9a + `Evidence`-only synthesis.
- **Test & Verification:** Unit/integration 4-route + E2E Task 12 (direncanakan).
- **Acceptance Criteria:** Semua route melewati `EvidenceSet` → synthesis → verifier dengan 0 unverified citations.
- **Evidence of Completion (definisi):** Suite 4-route hijau.
- **Downstream Impact:** Unlock Task 10-full + Task 12.
- **Known Gaps:** Menunggu Task 7; jangan diklaim selesai dari 9a.

### Task 10 — Endpoint API `POST /api/v1/ask` `[IN PROGRESS]`

- **Status:** `[IN PROGRESS]` (parsial: `SQLRoute`/`VectorRoute`/`GraphRoute` wired; `HybridRoute` stub)
- **Objective:** Mengintegrasikan pipeline Task 4–9 ke handler FastAPI dengan kontrak Pydantic v2 penuh.
- **Why This Exists:** Satu endpoint terverifikasi adalah kontrak demo + frontend + E2E.
- **Preconditions / Dependencies:** Task 4/5/6/9a ada; Task 7/8/9-full untuk penyelesaian.
- **Scope (selesai parsial):** Wiring `VectorRoute → VectorRetriever → VectorAnswerSynthesizer` (+ `SQLRoute` analog) dengan metrik `vector_retrieval_ms/synthesis_ms/total_ms` (+ `routing_ms/entity_resolution_ms`) + `developer_mode` diagnostics (`scored_chunks`, `latency_breakdown`, `sql_executed`, `route_reasoning`); propagasi `filters_ignored`, `answered_via_fallback`, `unverified_citations`, `request_id`; stub jujur `GraphRoute`/`HybridRoute` → `status: not_found` ("belum tersedia … Fase 6/7").
- **Out of Scope:** Endpoint/streaming baru (`/ask/stream`, `/papers|authors|topics` adalah POST-MVP Phase 10, `docs/06` — tidak diimplementasikan); perubahan envelope.
- **Implementation Surface:** `backend/app/routers/ask.py` (288 baris: `:195-245` Vector wiring, `:247-288` stub, `:226-231` debug); `backend/app/models/ask.py` (kontrak penuh).
- **Data / Database Dependency:** Pool + timeout yang sama (Task 2); tidak ada query baru di luar retriever.
- **API / Contract Dependency:** Penuh `docs/06 §5`: `AskRequest` → `AskResponse` + `DebugInfo{sql_executed, route_reasoning, latency_breakdown_ms}` + error envelope. Hanya `/api/v1/ask` + `/api/v1/health`; `/api/query` SUPERSEDED.
- **Security / Guardrails:** Validasi Pydantic + envelope + tracing + rate-limit (Task 2); tidak ada raw DB error bocor.
- **Test & Verification:** Integration: `integration/test_ask_endpoint.py` (18) + `test_vector_ask_endpoint.py` (4) dalam 177; E2E: Task 12 untuk 4-route.
- **Acceptance Criteria (parsial terpenuhi):** `SQLRoute`/`VectorRoute` mengembalikan `answer + evidence_objects + sources + latensi`; `GraphRoute`/`HybridRoute` mengembalikan `not_found` jujur (bukan halusinasi). Full selesai saat 9-full wired.
- **Evidence of Completion (parsial):** File wiring + diagnostics + tests hijau untuk 2 route.
- **Downstream Impact:** Unlock Task 11 (frontend dapat dibangun di atas 2 route) + Task 12 parsial.
- **Known Gaps:** Full wiring menunggu Task 7/8/9-full; latensi budget (`docs/03`: SQL/Graph ≤500ms, Vector ≤1.5s, Hybrid ≤1.0s, LLM 5–10s, total ≤15s) baru tervalidasi parsial. `GraphRoute` T4 menjalankan ego-BFS (target_entity selalu NULL); pairwise path A↔B direncanakan Fase 9.
- **Rollback / Failure Consideration:** Stub Graph/Hybrid harus tetap `not_found` (bukan error 500) agar E2E 2-route tetap hijau selama pengembangan.

### Task 11 — Frontend Next.js (Gaya Padat Notion/Linear) `[PLANNED]`

- **Status:** `[PLANNED]`
- **Objective:** Chat UI 2-panel dense untuk demo + Dev-Mode inspection.
- **Why This Exists:** Demo MVP membutuhkan permukaan jujur (status, sitasi, sumber, latensi) di atas Task 10.
- **Preconditions / Dependencies:** Task 10-parsial cukup untuk mulai; Task 10-full untuk 4-route; `docs/07 UI Spec.md` sebagai spec kanonikal.
- **Scope:** Layout 2-panel; tabel numerik monospace; `Route Badge [SQL|Vector|Graph|HybridRoute]`; `evidence_objects` chips; `Collapsible Sources (N)`; sitasi `[Title, Year, DOI/no-doi]`; 6 states; Dev-Mode inspector (`validation_ms…total_ms`); sanitized error (tanpa raw SQL leak).
- **Out of Scope:** Perubahan kontrak API; visualisasi Gold kustom di luar `docs/07`.
- **Implementation Surface (rencana):** `frontend/` (dirujuk `README §6.1`, `docs/07 §4–§5`); klien API + citation parser + formatting utils. Tidak ada file diubah pada task Plan ini.
- **Data / Database Dependency:** Tidak langsung (via Task 10).
- **API / Contract Dependency:** Konsumsi `AskResponse` penuh (termasuk `debug` saat `developer_mode=true`).
- **Security / Guardrails:** Tidak menampilkan `sql_executed` mentah ke user biasa (Dev-Mode saja); sanitized errors.
- **Test & Verification:** `AC-UI-1..7` (`docs/07`, saat ini `[ ]` unchecked) + E2E Task 12 melalui UI (direncanakan).
- **Acceptance Criteria:** Semua state + badge + Dev-Mode sesuai `docs/07`; tidak ada halusinasi presentasional (angka selalu dari `evidence_objects`).
- **Evidence of Completion (definisi):** App + `AC-UI` hijau.
- **Downstream Impact:** Unlock demo + UAT.
- **Known Gaps:** `frontend/` belum ada implementasi; token desain (`bg #FFFFFF`, `accent #2563EB`, `Inter 14px`, `JetBrains Mono 13px`) mengikuti `docs/07 §3`.

### Task 12 — Verifikasi End-to-End & Baseline Latensi `[PLANNED]`

- **Status:** `[PLANNED]` (`[BLOCKED]` sampai Task 7/8/9-full/10-full; vertical slice SQL+Vector dapat di-pre-run)
- **Objective:** Gerbang sign-off MVP: 12 kueri kanonikal lintas 4 route di atas data nyata + baseline latensi CPU.
- **Why This Exists:** Satu-satunya bukti E2E bahwa grounding + sitasi + latensi terpenuhi bersamaan.
- **Preconditions / Dependencies:** Dataset prototipe (~20 publikasi, 40 chunk, 138 authors, 107 institusi — lihat §2); Task 4–10-full; `docs/02` (FR0–FR7, NFR1–NFR6) + `docs/01 §5` (sukses MVP: Top-N benar, Top-8 unik, `needs_clarification` untuk `j. wang`, 0 unverified, no destructive SQL, `not_found` saat kosong, e2e ≤15s CPU).
- **Scope:** 12-query gate (`tests/e2e/test_e2e_12_queries.py` — direncanakan, belum ada); latensi breakdown per route; `pytest tests/e2e/` + `pytest -v --cov=backend/app` (target ≥80% pada router/retriever/security).
- **Out of Scope:** Dataset >100K (FUTURE); tuning performa skala produksi.
- **Implementation Surface (rencana):** `tests/e2e/test_e2e_12_queries.py`; `tests/unit|integration` yang ada sebagai prasyarat (200). Perintah: `pytest`, `pytest -v --cov=backend/app --cov-report=term-missing tests/`, `pytest tests/unit/test_sql_security.py`, `pytest tests/unit/test_router.py`, `pytest tests/e2e/test_e2e_12_queries.py`.
- **Data / Database Dependency:** Live DB prototipe + HNSW + edges (+ Gold saat tersedia).
- **API / Contract Dependency:** Verifikasi `AskResponse` penuh per route (termasuk `filters_ignored`, `unverified_citations`, `answered_via_fallback`, `debug`).
- **Security / Guardrails:** 100% lolos AST + read-only; 0 evidence → `<200ms not_found` tanpa LLM; 0 unverified citations di jawaban final.
- **Test & Verification:** E2E 12 queries + baseline (`routing/sql/vector/graph/hybrid/LLM/total`); coverage ≥80%.
- **Acceptance Criteria:** 12/12 hijau; tidak ada destructive SQL; tidak ada sitasi fiktif; latensi total ≤15s CPU (8vCPU/16GB, 1–2 konkurensi).
- **Evidence of Completion (definisi):** Laporan pytest + baseline latensi + checklist `AC-DB/AC-RAG/AC-API/AC-PIPE` (saat ini `[ ]` di `docs/04/05/06/12`).
- **Downstream Impact:** MVP sign-off; unlock Phase 9–11 (POST-MVP/FUTURE).
- **Known Gaps:** Suite E2E belum ada file; vertical slice Task 4/5/6 di `develop` belum sign-off E2E.

---

## 2. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status | Representasi di Plan Ini |
|---|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED | Task 0 DONE — VERIFIED |
| **Vector Storage** | `pgvector` HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) pada `chunks.embedding vector(1024)` (DONE, Task 1) | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED (stale `09:51,101` masih PENDING/BLOCKED — tidak diikuti) | Task 1 DONE — VERIFIED |
| **Konvensi penamaan** | 9 tabel relasional kanonikal standar: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` | `01`, `02`, `03`, `04`, `05`, `06`, `10`, `11`, `12` | ALIGNED | Dipakai persis di semua Task; tanpa akhiran `_cleaned` |
| **Pembersihan data (cleaning)** | Bronze → Silver via script Python — **DONE** (`data/*_cleaned.csv`, 9 file; sudah ter-load Silver) | `01`, `04`, `10`, `12` | ALIGNED | Pre-task DONE |
| **Normalisasi lowercase** | Naratif & kategorikal disimpan full lowercase; tampilan & ID asli dipertahankan; `*_normalized` (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`) lowercase+trim+strip-punct untuk agregasi/pencarian | `01`, `02`, `04`, `05`, `12` | ALIGNED | Task 4 Gate |
| **Chunking** | Granularitas abstrak per publikasi pada `chunks`, field `chunk_text`, `section = 'title_abstract'` | `03`, `04`, `05`, `12` | ALIGNED | Task 1a |
| **Embedding** | `BAAI/bge-m3` (1024-dim, Float32) via `sentence-transformers`, batch 32–64, CPU-optimized, input `Title: {title}\nAbstract: {abstract}` (DONE, Task 1) | `01`, `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED | Task 1 + 6 |
| **Retrieval** | Dynamic 4-Route: `SQLRoute` (Silver), `VectorRoute` (`chunks.embedding`), `GraphRoute` (Derived Edge T1–T4), `HybridRoute` (Gold Analytics + Silver) | `01`, `02`, `03`, `05`, `06`, `10`, `11` | ALIGNED | Task 4/5/6/8 |
| **Vector Similarity Gate** | Cosine similarity threshold dikunci deterministik `>= 0.65` untuk `BAAI/bge-m3`; di bawah ambang → `status: not_found` | `02`, `03`, `05`, `06` | ALIGNED | Task 6 + 9a |
| **Dedup + LIMIT Vector** | `DISTINCT ON (p.publication_id)` + `LIMIT 8` = 8 publikasi unik | `02`, `03`, `04`, `05` | ALIGNED | Task 6 |
| **LIMIT SQL / Graph** | SQL non-aggregate `LIMIT 50`; Graph `LIMIT 50` (`docs/02 FR7`), T1 contoh `LIMIT 20` (`docs/05 §5.3` — ikuti per-templat) | `02`, `03`, `05` | ALIGNED dengan catatan | Task 5 / 8-retriever Known Gaps |
| **Format sitasi** | Standar deterministik 3-elemen: `[Title, Year, DOI]` jika ada DOI, dan `[Title, Year, no-doi]` jika naskah tanpa DOI | `01`, `05`, `06`, `07` | ALIGNED | Task 9a/9-full |
| **Graph Engine Strategy** | MVP parameterized PostgreSQL Recursive CTE (T1–T4, `max_hops=3`); pasca-MVP Apache AGE Phase 9 | `03`, `04`, `09`, `11` | ALIGNED | Task 8 |
| **Konteks RAG** | Framing `UNTRUSTED DATA` (`=== BEGIN/END RETRIEVED EVIDENCE ===`), LLM hanya sintesis + validasi `EvidenceObject`, short-circuit 0 bukti, `CitationVerifier` post-hoc | `02`, `03`, `05`, `06`, `07`, `08` | ALIGNED (drift nama blok `05:239-245` dicatat, ikuti `AGENTS.md`) | Task 7/9 |
| **Kontrak API** | `POST /api/v1/ask` (`AskRequest` & `AskResponse` dengan `evidence_objects`) + `GET /api/v1/health`. `/api/query` SUPERSEDED | `02`, `03`, `05`, `06`, `07`, `10`, `11` | ALIGNED | Task 2/10 |
| **Dataset prototipe** | Kecil (~20 publikasi, 40 chunk, 138 authors, 107 institusi, 22 kolom naskah) untuk validasi E2E | `01`, `02`, `03`, `04`, `10`, `11`, `12` | ALIGNED (rinci `03:48` tambah 344 keywords/33 funding/4120 refs — dipakai apa adanya, tidak diseragamkan sepihak) | Task 12 |
| **Dataset skala produksi** | Target masa depan (>100K publikasi, batch otomatis, dedup multi-tier, async workers) | `01`, `02`, `03`, `04`, `11`, `12` | ALIGNED | FUTURE, bukan MVP |

---

## 3. Keputusan Arsitektur Kanonikal (referensi — tidak diubah)

1. **No-DOI Citation Decision:** Format inline `[Title, Year, DOI]` bila DOI ada, `[Title, Year, no-doi]` bila tidak. Menjamin parser regex `CitationVerifier` + parser frontend deterministik. Lihat `docs/05 §7`, `docs/06 §5`, Task 9a.
2. **Cosine Similarity Threshold Decision (`VectorRoute`):** Terkunci `>= 0.65` untuk `BAAI/bge-m3`. Di bawahnya → `status: not_found`. Lihat `docs/05 §5.2`, Task 6.
3. **Post-MVP Graph Engine Decision:** MVP Recursive CTE terparameterisasi (T1–T4) di atas `institution_collaboration` + `author_collaboration`. Phase 9 evaluasi **Apache AGE** (ekstensi PostgreSQL, tanpa graph-DB terpisah). Lihat `docs/04 §6`, Task 8.

---

## 4. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/10 Implementation Plan.md` v3.6.4 | Sinkronisasi Fase 6: `GraphRoute` dari stub ke wired (`Task 10-parsial` mencakup `SQLRoute`/`VectorRoute`/`GraphRoute`); `Task 8-retriever` T2/T3/T4 spesifikasi di `docs/05 §5.3`; `from_graph` fail-closed; `CitationVerifier` Jaccard ≥0.8 + DOI-year strict (gap lama `cite_year` dihapus); T4 ego-BFS berbatas direncanakan pairwise path Fase 9 | Review Phase 6 2026-10-02: kode + 261 tests + 2 E2E mock baru membuktikan graph route penuh |
| `docs/10 Implementation Plan.md` v3.6.3 | Rekonsiliasi Phase 4 berbasis evidence Level 1: Task 4/5/6 + 9a menjadi `[IMPLEMENTED — VERIFICATION PENDING]` (inti unit+integration DONE — VERIFIED, 177 tests); pecah dokumentatif `8-edge` DONE vs `8-retriever` NEXT vs `8.5` PLANNED dan `9a` Vector-scoped vs `9-full` unified; tulis ulang tiap Task ke template executable 14-field (Status/Objective/Why/Preconditions/Scope/Out-of-Scope/Surface/Data/API/Security/Test/Acceptance/Evidence/Downstream/Gaps); selaraskan threshold, `DISTINCT ON LIMIT 8`, `LIMIT 50`, timeout 10s, `app_readonly`, `filters_ignored`, `unverified_citations`, latensi; tanpa renumbering dan tanpa keputusan arsitektur baru | Audit 2026-09-29: kode + 177 tests membuktikan slice SQL+Vector maju melampaui `README`/`docs/11` yang masih PLANNED; overlap 6 vs 5/7 harus dijelaskan dokumentatif agar tidak terjadi "kode ada tapi plan bilang belum" atau "plan bilang selesai tapi integrasi belum" |
| `docs/10 Implementation Plan.md` v3.6.2 | Aturan bahasa: narasi Indonesia, teknis Inggris | Tanpa duplikasi bilingual; perbaiki terjemahan literal |
| `docs/10 Implementation Plan.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/10 Implementation Plan.md` v3.5.0 | Menandai DB setup + cleaning + cleaned export DONE; memecah Task 1 menjadi 1a–1d; menambah Progress Tracker | Sinkronisasi progress aktual 2026-09-27 |
| `docs/10 Implementation Plan.md` v3.4.0 | Kembalikan target build ke nama tabel kanonikal tanpa `_cleaned`; kunci sitasi (`no-doi`), threshold `>= 0.65`, strategi AGE; perbarui matriks + changelog | Penyelarasan penamaan + tutup open decisions |
