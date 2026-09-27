# AI-Bibliometrics — Research Intelligence Assistant

Sistem tanya-jawab bahasa natural di atas database 9 tabel publikasi ilmiah (PostgreSQL / Supabase + pgvector). Pengguna non-teknis cukup bertanya dalam bahasa natural — sistem secara otomatis menentukan jalur retrieval yang tepat (**terstruktur/agregat**, **semantik**, **hybrid**, atau **relasional**), mengeksekusi query yang aman, dan menyusun jawaban yang **ter-grounding ke data asli** beserta sitasi ilmiah presisi (`[judul, tahun, doi]`) — bukan halusinasi model.

> **Status Documentation**: **Planning Draft v2** (Spesifikasi Teknis Final: 4-Route Hybrid Retrieval, Relational Surface, Security & Guardrails) · *Last updated: 2026-09-23* · *Architecture-review alignment: 2026-09-27 (docs-only; no code in repo — all pipeline components PLANNED/NOT IMPLEMENTED, see `docs/00 Readme.md` status legend)*

> **Kontrak API target: `/api/v1`** — primer `POST /api/v1/ask`, health `GET
> /api/v1/health` (detail di `docs/06 Api Design.md` §1). Endpoint `POST
> /api/query` dari dokumen v2 berstatus **superseded**. Rute `relational` =
> rute `graph` pada target arsitektur. Router diutamakan rule-based +
> deterministik (LLM router bukan mandatory). Graph backend decision pending
> (kandidat: Apache AGE, Kùzu).

---

## Masalah yang Diselesaikan

1. **Query Terstruktur / Agregat** (*"Siapa 5 author paling produktif tahun 2023?"*, *"Berapa total sitasi institusi X?"*) — sebelumnya memerlukan penulisan query SQL manual setiap kali.
2. **Query Semantik / Eksploratif** (*"Paper apa saja yang membahas stres oksidatif pada Wharton's jelly?"*) — memerlukan pemahaman konteks/makna yang tidak bisa ditangani oleh pencarian kata kunci persis (exact match).
3. **Query Relasional / Jaringan Kolaborasi** (*"Institusi mana yang berkolaborasi dengan peneliti AI?"*, *"Siapa co-author dari Penulis X?"*) — memerlukan traversal relasi multi-hop antar entitas (paper → author → institusi partner) yang rawan gagal jika dipaksa ke SQL generik atau vector search biasa.
4. **Ketiadaan Pintu Masuk Tunggal** — tidak ada satu UI chat terpadu yang dapat mengombinasikan ketiga kebutuhan retrieval tersebut dengan jaminan jawaban grounded (tanpa mengarang data).

---

## Keputusan Kunci (Hasil Planning v2)

| Area | Keputusan Utama | Detail & Rationale |
|---|---|---|
| **Retrieval Engine** | **4-Route Multi-Hybrid** | `structured` (Text-to-SQL), `semantic` (pgvector RAG), `hybrid` (SQL + Vector), dan `relational` (Knowledge-Graph minimum surface via edge tables). |
| **Model Generation** | **Qwen2.5-Coder-7B-Instruct** (GGUF Q4_K_M via Ollama) | Self-hosted, **CPU-only** (tanpa budget GPU). Model 7B terbaik di kelasnya untuk penulisan SQL terstruktur & JSON output formatting. |
| **Model Embedding** | **BAAI/bge-m3** | Self-hosted (1024 dimensi), mendukung multilingual (ID/EN) untuk pencarian dokumen & query user. |
| **Backend API** | **Python + FastAPI** | Terpisah dari Next.js. Ekosistem RAG, embedding, validasi SQL AST (`sqlglot`), dan validasi Pydantic paling matang. |
| **Frontend UI** | **Next.js (React)** | Desain *Clean White, Dense, Notion/Linear-style*. Mengutamakan kerapian data tabular, monospace formatting untuk angka, collapsible sources, dan state visual yang jujur. |
| **Database** | **PostgreSQL (Supabase) + pgvector** | 9 tabel relasional asli + 2 edge table derivatif (`institution_collaboration`, `author_collaboration`) + kolom `vector(1024)` pada `chunks`. |
| **Keamanan DB** | **Role `app_readonly` + Multi-Layer AST Validation** | Backend hanya mengakses DB via role read-only (SELECT only), `SET search_path = public`, statement timeout 10s, dan validasi SQL di level kode. |
| **Integritas Sitasi** | **Post-hoc Citation Verifier** | Menghapus sitasi tak terverifikasi dari LLM synthesis ke `unverified_citations` metadata; empty results ditangani secara deterministik (`status: not_found`). |
| **Scope & Akses** | **Internal-Only MVP** | Digunakan oleh tim kecil terpercaya; fokus utama pada keberhasilan end-to-end pipeline. |

---

## Arsitektur Sistem & Alur Data

### Diagram Komponen High-Level

```
┌─────────────────┐       HTTPS       ┌─────────────────────────────┐
│  Next.js UI     │ ────────────────> │  Backend API (FastAPI)      │
│  (Notion/Linear │ <──────────────── │  Orchestration & Validation │
│   Dense Style)  │    JSON Response  └─────────────────────────────┘
└─────────────────┘                                 │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                            ┌───────────────┐ ┌───────────┐ ┌───────────────┐
                            │ Router        │ │ Ollama    │ │ Embedding Svc │
                            │ (Typed Contract│ │ (Qwen2.5- │ │ (bge-m3,      │
                            │  & Gate)      │ │  Coder-7B)│ │  1024 dim)    │
                            └───────────────┘ └───────────┘ └───────────────┘
                                    │               │               │
                                    ▼               ▼               ▼
                            ┌───────────────────────────────────────────────┐
                            │           PostgreSQL (Supabase)               │
                            │  • 9 Tabel Relasional Publikasi               │
                            │  • 2 Edge Tables (Graph Surface)              │
                            │  • pgvector (HNSW Index di `chunks`)          │
                            │  • Access via Role `app_readonly`             │
                            └───────────────────────────────────────────────┘
```

### 4 Jalur Retrieval (Retrieval Pipeline)

1. **Router / Query Planner**:
   - Memakai prompt *schema-light* untuk mengklasifikasi pertanyaan ke salah satu dari 4 rute.
   - Mengeluarkan **Typed Entity Contract** (Pydantic model) berisi filter terstruktur (`YearFilter`, `country`, `author_name`, `institution_name`, `keyword`, `topic`, dll).
   - **Entity Resolution Gate**: Memvalidasi entitas (`lower + trim` → exact → fallback `ILIKE`). Jika 0 match → `not_found`, jika >1 match → `needs_clarification` dengan list kandidat entitas, jika 1 match → bind ID canonical.
   - Jika router JSON gagal → fallback aman ke route `semantic` dengan flag `answered_via_fallback: true`.

2. **Jalur 1 — Structured (`structured`)**:
   - Menghasilkan SQL `SELECT` read-only untuk query agregasi/ranking.
   - **Validasi Multi-Lapis**: Parse AST via `sqlglot` → Cek Root `SELECT` → Whitelist Tabel/Kolom → Blacklist Keyword Destruktif → **Aggregate-Shape Check** (memastikan intent agregat menghasilkan `COUNT/SUM/GROUP BY`, bukan listing mentah) → **Double-Count Check** (`COUNT(DISTINCT publication_id)` pada join junction) → Enforce `LIMIT 50` pada non-agregat.

3. **Jalur 2 — Semantic (`semantic`)**:
   - Embed pertanyaan dengan `bge-m3`.
   - Query similarity cosine (`<=>`) pada `chunks` dengan **`DISTINCT ON (publication_id)`** untuk menjamin `LIMIT 8` menghasilkan 8 publikasi unik, bukan chunk yang tumpang tindih.

4. **Jalur 3 — Hybrid (`hybrid`)**:
   - Menggabungkan vector search dan filter terstruktur dalam **satu query SQL terparameterisasi**.
   - Filter numerik (`year_filter`) memakai operator yang di-whitelist (`eq`, `gt`, `gte`, `lt`, `lte`, `between`). Field di luar slot binding dikumpulkan di `filters_ignored`.

5. **Jalur 4 — Relational (`relational`)**:
   - Mengeksekusi **4 templat traversal terparameterisasi** pada edge table `institution_collaboration` dan `author_collaboration`.
   - Tidak ada SQL buatan LLM di jalur ini. Menggunakan depth-bound (1–3 hop) dan mengembalikan provenance `via_publication_ids` untuk keterlacakan sitasi.

6. **Answer Synthesizer & Citation Verifier**:
   - Menyusun jawaban berbasis data hasil retrieval dengan sitasi eksplisit `[judul, tahun, doi]`.
   - **Post-hoc Verifier**: Memeriksa seluruh sitasi terhadap evidence asli. Sitasi yang tidak cocok di-strip ke `unverified_citations`.
   - **Deterministic Empty Result**: Jika hasil query = 0 baris, sistem langsung mengembalikan `status: not_found` tanpa memanggil LLM untuk mencegah halusinasi.

---

## Tech Stack & Rationale

| Layer | Pilihan | Rationale & Trade-off |
|---|---|---|
| **Database** | PostgreSQL (Supabase) + pgvector | Database 9 tabel publikasi yang sudah bersih & live. Tambahan extension `pgvector` untuk similarity search. |
| **Backend** | Python + FastAPI | Ekosistem RAG, pydantic validation, parser SQL (`sqlglot`), dan embedding paling matang. |
| **LLM (Local)** | Qwen2.5-Coder-7B-Instruct (GGUF Q4_K_M) via Ollama | Model 7B paling andal untuk Text-to-SQL & JSON terstruktur. Berjalan di CPU dengan latency ~5–15 detik per request. |
| **Embedding** | BAAI/bge-m3 (1024 dim) | Model embedding multilingual (ID/EN) terbaik di kelasnya untuk indexing `chunks` dan query online. |
| **SQL Validator** | `sqlglot` | Parser AST Python untuk memvalidasi query, memeriksa whitelist schema, dan mencegah SQL injection/malformed query. |
| **Frontend** | Next.js (React) | Interface *Notion/Linear style* (Clean White, Dense, Monospace Table Numbers, Dev Mode SQL Viewer). |
| **Deployment** | Docker Compose (Backend/Ollama) + Vercel (Frontend) | Single VM/VPS CPU (min. 8 vCPU, 16GB RAM) untuk backend; Vercel untuk hosting frontend Next.js. |

---

## Keamanan & Integritas Data

- **Role DB Read-Only (`app_readonly`)**: Backend terhubung menggunakan role DB khusus tanpa izin `INSERT/UPDATE/DELETE/DROP/ALTER`. Dilengkapi `SET search_path = public` per session dan `statement_timeout = '10s'`.
- **Validasi Guardrail di Kode (bukan Prompt Semata)**: SQL generator tidak pernah dipercaya secara mentah. Setiap query wajib lolos pemeriksaan AST, whitelist tabel/kolom, dan aturan agregasi.
- **Operator & Traversal Whitelist**: Template hybrid & relational hanya menerima operator filter dan depth yang di-whitelist di kode Pydantic (`Literal`).
- **Citation Verification**: Menyaring sitasi karangan LLM sebelum sampai ke pengguna.
- **Credential Storage**: Kredensial database hanya tersimpan dalam environment variable backend (`.env`), tidak pernah terekspos ke frontend atau repository.

---

## Status Implementasi & Blocker Kritis

- [x] Database **sudah live** di Supabase, 9 tabel relasional sudah dimuat & di-clean.
- [x] Dokumen perencanaan teknis v2 lengkap di folder `docs/`.
- [ ] **Blocker Task 0**: Verifikasi skema database real Supabase terhadap `information_schema.columns` (menyesuaikan dokumen `04 Database Schema.md`).
- [ ] **Blocker Task 1**: Kolom vector `chunks.embedding` **masih kosong**. Diperlukan eksekusi script embedding batch (`bge-m3`) dan pembuatan index HNSW sebelum jalur semantic/hybrid/relational dapat dijalankan.

---

## Urutan Build (Implementation Plan Tasks 0–12)

```
[Task 0: Schema Check] ──> [Task 1: Embedding Pipeline] ──> [Task 2: FastAPI & DB Skeleton]
                                                                       │
┌──────────────────────────────────────────────────────────────────────┘
▼
[Task 3: Ollama Setup] ──> [Task 4: Router & Gate] ──> [Task 5 & 6: SQL & Vector Engine]
                                                                 │
┌────────────────────────────────────────────────────────────────┘
▼
[Task 7: Hybrid Path] ──> [Task 8: Relational Path] ──> [Task 9: Answer Synthesizer]
                                                                 │
┌────────────────────────────────────────────────────────────────┘
▼
[Task 10: API Endpoints] ──> [Task 11: Next.js UI] ──> [Task 12: E2E Verification]
```

---

## Struktur Repositori & Dokumentasi

```
AI-Bibliometrics/
├── README.md                     ← Dokumen utama ini
├── docs/                         ← Seluruh spesifikasi teknis dan planning v2
│   ├── 00 Readme.md              # Ringkasan planning & urutan baca yang disarankan
│   ├── 01 Prd.md                 # Product Requirement Document (Goal MVP, Scope In/Out)
│   ├── 02 Srd.md                 # System Requirement Document (Functional & Non-Functional)
│   ├── 03 System Architecture.md # Arsitektur komponen, data flow, & deployment topology
│   ├── 04 Database Schema.md     # Skema 9 tabel, cleaning rules, & definisi edge tables
│   ├── 05 Retrieval Rag Design.md# Spesifikasi teknis 4-Route Retrieval, Router, & Guardrails
│   ├── 06 Api Design.md          # Kontrak REST API target /api/v1 (desain v2 /api/query superseded)
│   ├── 07 Ui Spec.md             # Spesifikasi UI Notion/Linear style, Design Tokens, & States
│   ├── 08 Security.md            # Keamanan DB, read-only role, & mitigasi Prompt/SQL Injection
│   ├── 09 Tech Stack.md          # Pilihan stack final, rationale Qwen2.5-Coder-7B & bge-m3
│   ├── 10 Implementation Plan.md # Panduan langkah build linear Task 0 sampai Task 12
│   └── 11 Roadmap.md             # Plans pasca-MVP (Fuzzy Entity, Eval Harness, GPU upgrade)
└── (src/)                        # Kode sumber aplikasi backend & frontend (akan dibangun)
```

---

## Roadmap Pasca-MVP

- **Fase 2 (Kualitas & Skala)**: Integrated Fuzzy Entity Resolution (`rapidfuzz`), Automated Evaluation Harness (Golden query regression set), Hybrid Postgres Full-Text Search (FTS), & Citation Network Expansion.
- **Fase 3 (Upgrade Infra)**: Migrasi Ollama ke GPU VPS untuk menjalankan model 32B/70B dengan latency <3 detik & streaming token response.
- **Fase 4 (Multi-User & Security)**: Supabase Auth, Row-Level Security (RLS), Rate limiting per user, & Multi-tenant audit logs.
- **Fase 5 (Data Freshness)**: Automated Ingestion Pipeline untuk publikasi baru.
- **Fase 6 (Multi-Turn Context)**: Pengelolaan state percakapan multi-turn.

---

## Urutan Baca Dokumen

Disarankan membaca dokumentasi teknis di folder `docs/` secara berurutan mulai dari [`docs/00 Readme.md`](docs/00%20Readme.md) hingga [`docs/11 Roadmap.md`](docs/11%20Roadmap.md).