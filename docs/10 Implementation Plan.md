# Rencana Implementasi — Urutan Build Menuju End-to-End (Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.0 (Consolidated Hybrid Master Blueprint)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `10 Implementation Plan.md` Draft v2 s.d. v3.5.0  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/00` hingga `docs/12`  

> **Status Implementasi & Kesiapan Basis Data (Sinkronisasi Progress Phase 2):**  
> 1. **Database PostgreSQL & Vector Storage — DONE:** Basis data PostgreSQL aktif memuat 9 tabel relasional kanonikal, 40 chunk ber-embedding vector(1024) `BAAI/bge-m3` dengan indeks HNSW aktif, serta tabel edge `institution_collaboration` dan `author_collaboration`.  
> 2. **Cleaning & Cleaned Export — DONE:** Data Scopus sudah dibersihkan dan berhasil di-export sebagai 9 file `data/*_cleaned.csv`.  
> 3. **Kerangka FastAPI & DB Pool (Task 2 & 3 / Phase 2) — DONE:** Backend FastAPI (`backend/app/`), pool async `asyncpg`, endpoint `GET /api/v1/health`, kontrak `POST /api/v1/ask`, middleware `X-Request-ID`, rate limiting, logging terstruktur, dan client Ollama terverifikasi dengan 23 passing tests.  
> 4. **NEXT (Phase 3):** Implementasi `QuestionRouter` (Task 4) dan `SqlRetriever` tervalidasi AST `sqlglot` (Task 5) sebagai irisan vertikal pertama.
>
> ### Progress Tracker (Sinkronisasi Phase 2 Selesai)
> **DONE:** Database setup · Prototype data preparation · Cleaning · Cleaned data export · Task 0 (Schema Audit) · Task 1a-1d (Prepare, Generate, Store pgvector, Validate HNSW) · Task 8 (Edge Materialization) · Task 2 (FastAPI Framework & DB Pool) · Task 3 (Ollama Client & Health).  
> **NEXT (Phase 3):** (1) QuestionRouter (Task 4) → (2) EntityResolutionGate → (3) SqlRetriever with sqlglot AST gate (Task 5) → (4) Vertical Slice Integration.
---

## 0. Matriks Status Implementasi

| Komponen | Status Saat Ini | Status Target | Gap & Aksi |
|---|---|---|---|
| **Baseline Database** | `READY` (9 Tabel Kanonikal) | Baseline Prototipe | PostgreSQL sudah aktif memuat 9 tabel prototipe; tidak perlu membuat DB dari nol |
| **Pembersihan Data (Cleaning) Scopus** | `DONE` | Baseline Prototipe | Data Scopus sudah dibersihkan sesuai aturan `docs/12 §3`; bukan pending |
| **Kerangka FastAPI** | `DONE` | MVP | Kerangka FastAPI, `/api/v1/health`, `POST /api/v1/ask` contract, middleware `X-Request-ID`, rate limiting, error handling (Task 2) |
| **Kontrak API v1** | `FOUNDATION DONE` | MVP | Kontrak `POST /api/v1/ask` dengan skema Pydantic v2 `EvidenceObject`, `AskResponse`, dan error envelope terstandarisasi (Task 2 & 10) |
| **Router Pertanyaan** | `NOT IMPLEMENTED` | MVP | Router 4-rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`) + Gerbang Resolusi Entitas (Task 4) |
| **SqlRetriever** | `NOT IMPLEMENTED` | MVP | Text-to-SQL + validasi AST `sqlglot` pada 9 tabel kanonikal + peran `app_readonly` (Task 5) |
| **VectorRetriever** | `READY FOR RETRIEVER DEV` | MVP | `chunks.embedding` terisi 40/40 (1024-dim) + indeks HNSW aktif; siap untuk implementasi `VectorRetriever` (Task 6) |
| **Lapisan Bukti (Evidence Layer)** | `NOT IMPLEMENTED` | MVP | `EvidenceUnifier` + `EvidenceRanker` + penegakan (enforcement) `EvidenceObject` (Task 7 & 9) |
| **Materialisasi Graf**| `DONE` (Tabel Edge) | MVP | `institution_collaboration` (254 edge) dan `author_collaboration` (484 edge) termaterialisasi idempoten (Task 8) |
| **GraphRetriever** | `NOT IMPLEMENTED` | MVP | GraphRetriever + 4 templat Recursive CTE terparameterisasi T1–T4 (Task 8) |
| **Mesin Analitik Gold**| `NOT IMPLEMENTED` | MVP | Komputasi `topics`, `topic_evolution`, dan `researcher_expertise` dari Silver kanonikal (Task 8.5) |
| **Sintesiser Jawaban** | `NOT IMPLEMENTED` | MVP | Sintesiser LLM Analitik + `CitationVerifier` (`[Title, Year, DOI/no-doi]`) + not_found deterministik (Task 9) |
| **UI Next.js** | `NOT IMPLEMENTED` | MVP | Tata letak padat Notion/Linear + sumber collapsible + inspektor Dev Mode (Task 11) |
| **Verifikasi E2E** | `NOT IMPLEMENTED` | MVP | Pengujian 12 kueri end-to-end pada dataset prototipe + baseline latensi (Task 12) |

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

### Task 0 — Verifikasi Skema Basis Data Prototipe (DONE)
- Eksekusi kueri `information_schema.columns` pada basis data PostgreSQL yang sudah ada via `scripts/verify_schema.py`.
- Rekonsiliasi nama kolom dan tipe data 9 tabel kanonikal terhadap `04 Database Schema.md` — status MATCH (0 errors).

### Task 1 — Pipeline Batch Embedding (`chunks.embedding`) (DONE)
- **Task 1a — Prepare embedding input (DONE):** seleksi dan validasi 40 chunk dari tabel `chunks`; konstruksi teks input per format terkunci `Title: {title}\nAbstract: {abstract}` (`docs/12 §4`).
- **Task 1b — Generate embeddings (DONE):** eksekusi script embedding `BAAI/bge-m3` (1024 dimensi, Float32) pada tabel `chunks`, batch 32, idempotent resume.
- **Task 1c — Store embeddings into PostgreSQL using pgvector (DONE):** `ALTER TABLE chunks ADD embedding vector(1024)` + kolom metadata + insert seluruh 40 vektor hasil Task 1b.
- **Task 1d — Validate vector records (DONE):** `SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;` = `0`; 100% terhubung ke artikel asal; indeks HNSW `idx_chunks_embedding_hnsw` (`m=16, ef_construction=64, vector_cosine_ops`) dan `idx_chunks_pub_id` aktif dan teruji dengan kueri `<=>`.
### Task 2 — Kerangka FastAPI & Database (DONE)
- Setup struktur modular FastAPI (`routers/`, `services/`, `models/`, `core/`, `db/`).
- Pool koneksi `asyncpg` dengan `search_path = public` dan `statement_timeout = 10s`.
- Middleware `X-Request-ID` (UUIDv4) dan IP rate limiter (60 rpm).
- Implementasi endpoint `GET /api/v1/health` dan `POST /api/v1/ask` dengan validasi Pydantic v2.

### Task 3 — Setup Ollama & Model Qwen2.5-Coder-7B (DONE)
- Client async Ollama untuk health probe model dan embedding service.
- Model `bge-m3` dan `qwen2.5-coder:7b-instruct` terintegrasi pada endpoint health check.
### Task 4 — Router Pertanyaan & Gerbang Resolusi Entitas
- Implementasi `QuestionRouter` untuk klasifikasi 4 rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`).
- Implementasi `EntityResolutionGate` untuk validasi nama penulis/institusi (`needs_clarification` jika >1 kandidat).

### Task 5 — SqlRetriever (Relasional Silver)
- Pembuat (generator) Text-to-SQL + validasi AST multi-lapis via `sqlglot` + penegakan `LIMIT 50` pada 9 tabel kanonikal.

### Task 6 — VectorRetriever (Silver Semantic `chunks`)
- Embed kueri + pencarian kemiripan kosinus dengan klausa `DISTINCT ON (p.publication_id) LIMIT 8` pada `chunks` dengan threshold similarity $\ge 0.65$.

### Task 7 — EvidenceUnifier & EvidenceRanker
- Normalisasi seluruh output retriever menjadi `EvidenceSet`.
- Ekstraksi fakta numerik dan pembuatan objek bukti dasar.

### Task 8 — Graph Edge Tables & Gold Analytics Materialization
- Materialisasi idempoten tabel `institution_collaboration` dan `author_collaboration` dari `pub_institution` dan `pub_author`.
- Implementasi 4 templat recursive CTE (T1–T4).
- Materialisasi tabel Gold Layer: `topics`, `topic_evolution`, dan `researcher_expertise` ($\text{ExpertiseScore} = w_1 \cdot \text{Relevance} + w_2 \cdot \text{Productivity} + w_3 \cdot \text{Impact} + w_4 \cdot \text{Recency}$).

### Task 9 — Sintesiser Jawaban & CitationVerifier
- Sintesiser LLM analitik yang mengonsumsi nilai terverifikasi dan menghasilkan `evidence_objects`.
- `CitationVerifier` post-hoc berbasis regex untuk format `[Judul, Tahun, DOI]` dan `[Judul, Tahun, no-doi]`, memangkas sitasi fiktif ke `unverified_citations`.
- Short-circuit deterministik pada 0 item bukti (`status: not_found`, < 200ms).

### Task 10 — Endpoint API `POST /api/v1/ask`
- Integrasi pipeline Task 4–9 ke dalam handler FastAPI `POST /api/v1/ask` dengan validasi Pydantic v2.

### Task 11 — Frontend Next.js (Gaya Padat Notion/Linear)
- Antarmuka web dua panel, tabel numerik monospace, sumber collapsible, visualisasi `evidence_objects`, dan inspektor Dev Mode.

### Task 12 — Verifikasi End-to-End & Baseline Latensi
- Pengujian 12 kueri end-to-end lintas 4 rute pada dataset prototipe dan pencatatan baseline latensi server CPU.

---

## 2. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status |
|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED |
| **Penyimpanan vector** | `pgvector` HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) pada `chunks.embedding vector(1024)` (PLANNED, Task 1) | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Konvensi penamaan** | 9 tabel relasional kanonikal standar: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` | `01`, `02`, `03`, `04`, `05`, `06`, `10`, `11`, `12` | ALIGNED |
| **Pembersihan data (cleaning)** | Bronze → Silver via script Python — **DONE** (hasil pembersihan ter-export di `data/*_cleaned.csv`, 9 file; sudah ter-load di 9 tabel Silver) | `01`, `04`, `10`, `12` | ALIGNED |
| **Normalisasi lowercase** | Naratif & kategorikal (`abstract`, `keyword`, `country`, dll.) disimpan full lowercase; tampilan & ID asli dipertahankan; kolom `*_normalized` (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`) disimpan lowercase+trim+strip-punct untuk agregasi/pencarian | `01`, `02`, `04`, `05`, `12` | ALIGNED |
| **Chunking** | Granularitas abstrak per publikasi pada tabel `chunks`, field `chunk_text`, `section = 'title_abstract'` | `03`, `04`, `05`, `12` | ALIGNED |
| **Embedding** | `BAAI/bge-m3` (1024-dim, Float32) via `sentence-transformers`, batch 32–64, dioptimalkan CPU, input `Title: {title}\nAbstract: {abstract}` (PLANNED, Task 1) | `01`, `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Retrieval** | 4-Rute Dinamis: `SQLRoute` (Silver), `VectorRoute` (`chunks.embedding`), `GraphRoute` (Edge Turunan T1–T4), `HybridRoute` (Analitik Gold + Silver) | `01`, `02`, `03`, `05`, `06`, `10`, `11` | ALIGNED |
| **Gerbang similaritas vector** | Ambang kesamaan kosinus dikunci deterministik $\ge 0.65$ untuk model `BAAI/bge-m3`; kueri di bawah ambang batas short-circuit ke `status: not_found` | `02`, `03`, `05`, `06` | ALIGNED |
| **Format sitasi** | Standar deterministik 3-elemen: `[Judul, Tahun, DOI]` jika ada DOI, dan `[Judul, Tahun, no-doi]` jika naskah tanpa DOI | `01`, `05`, `06`, `07` | ALIGNED |
| **Strategi mesin graf** | MVP dikunci menggunakan Recursive CTE Terparameterisasi PostgreSQL (T1–T4); rekomendasi evaluasi pasca-MVP menggunakan Apache AGE pada Fase 9 | `03`, `04`, `09`, `11` | ALIGNED |
| **Konteks RAG** | Pembingkaian `UNTRUSTED DATA`, LLM murni menyintesis narasi & memvalidasi `EvidenceObject`, short-circuit deterministik pada 0 bukti, `CitationVerifier` post-hoc | `02`, `03`, `05`, `06`, `07`, `08` | ALIGNED |
| **Kontrak API** | `POST /api/v1/ask` (`AskRequest` & `AskResponse` dengan `evidence_objects`) + `GET /api/v1/health`. Endpoint `/api/query` resmi SUPERSEDED | `02`, `03`, `05`, `06`, `07`, `10`, `11` | ALIGNED |
| **Dataset prototipe** | Dataset prototipe kecil (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) untuk validasi end-to-end lengkap | `01`, `02`, `03`, `04`, `10`, `11`, `12` | ALIGNED |
| **Dataset skala produksi** | Target masa depan untuk ingestion Scopus skala besar (>100K publikasi) dengan pipeline batch otomatis, deduplikasi multi-tier, dan worker async | `01`, `02`, `03`, `04`, `11`, `12` | ALIGNED |

---

## 3. Keputusan Arsitektur Kanonikal

1. **Keputusan Sitasi Tanpa DOI:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Keputusan Ambang Batas Kesamaan Kosinus (`VectorRoute`):**
   - *Keputusan:* Nilai ambang batas kesamaan kosinus dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri yang menghasilkan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Keputusan Mesin Graf Pasca-MVP:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 4. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/10 Implementation Plan.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/10 Implementation Plan.md` v3.5.0 | Menandai DB setup + cleaning + cleaned export sebagai DONE; memecah Task 1 menjadi 1a–1d (prepare input → generate → store pgvector → validate); menambah Progress Tracker NEXT 1–7 | Sinkronisasi progress aktual 2026-09-27 |
| `docs/10 Implementation Plan.md` v3.4.0 | Mengembalikan seluruh target build task (Task 0–12) ke nama tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/10 Implementation Plan.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan strategi graf Apache AGE | Menutup open decisions menjadi keputusan kanonikal |
| `docs/10 Implementation Plan.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumen di seluruh repository |
