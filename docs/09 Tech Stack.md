# Tumpukan Teknologi — Rekomendasi & Rasional (Consolidated Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.2 (Consolidated Hybrid Master Blueprint — update status ekstensi pgvector, tanpa perubahan keputusan teknis)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `09 Tech Stack.md` Draft v2 s.d. v3.6.1  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  

> **Status Implementasi & Realitas Stack (Sinkronisasi Progress 2026-09-27):**  
> 1. **Database PostgreSQL:** Basis data PostgreSQL **sudah dibuat dan siap pakai**, memuat **dataset prototipe kecil** (~20 publikasi, 40 chunk, 138 author, 107 institusi) pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) untuk validasi end-to-end. Kredensial diamankan secara internal.  
> 2. **Implementasi Aplikasi:** Direktori aplikasi (`backend/`, `frontend/`, `docker/`, `scripts/`, `tests/`) berstatus **PLANNED / NOT YET IMPLEMENTED** dan dikunci oleh keputusan arsitektur di bawah ini.
> 3. **Sinkronisasi Progress 2026-09-27:** Cleaning Scopus dan cleaned export (`data/*_cleaned.csv`) **DONE**; ekstensi pgvector (`CREATE EXTENSION vector`) **DONE — sudah terpasang di PostgreSQL DB + Supabase**; kolom `chunks.embedding` + data vektor + indeks HNSW (Task 1) **PENDING**.

---

## 1. Ringkasan Stack

| Lapisan | Pilihan | Alasan singkat |
|---|---|---|
| **Database relasional** | PostgreSQL 15+ (DONE, eksternal, siap pakai) | Sudah memuat 9 tabel kanonikal, tidak perlu migrasi atau setup DB baru |
| **Vector storage** | pgvector — ekstensi `vector` DONE (terpasang di PostgreSQL DB + Supabase); kolom `chunks.embedding vector(1024)` + data + HNSW PENDING Task 1; turunan kedua `topics.representation_vector vector(1024)` (PLANNED Task 8.5) | Indeks turunan read-only dari Silver; `VectorRoute` tetap BLOCKED sampai kolom + data + HNSW Task 1 selesai (`docs/05 §3`) |
| **Backend** | Python 3.11+ & FastAPI | Ekosistem RAG, validasi AST SQL (`sqlglot`), embedding, dan I/O async paling matang |
| **LLM (mandiri)** | Qwen2.5-Coder-7B-Instruct (GGUF Q4_K_M) via Ollama | Model terbaik di kelas 7B untuk text-to-SQL dan sintesis terstruktur di CPU |
| **Embedding** | BAAI/bge-m3 (1024-dim, Float32) | Multibahasa (EN/ID), representasi semantik berkualitas tinggi, layak di CPU |
| **Validasi SQL** | `sqlglot` | Parser AST Python yang kokoh untuk pemeriksaan daftar putih tabel kanonikal dan keamanan |
| **Frontend** | Next.js (React) | Tata letak padat (dense) Notion/Linear, tabular monospace, visualisasi `evidence_objects` |
| **Deployment** | Docker Compose (Single Host VPS: `backend` + `ollama`; PostgreSQL eksternal, bukan service Compose MVP) | Backend FastAPI + Ollama dalam container, frontend di Vercel |

---

## 2. LLM: Qwen2.5-Coder-7B-Instruct — Mengapa Ini, Bukan yang Lain

Batasan keras: khusus CPU, tanpa budget GPU untuk fase MVP. Ini membatasi pilihan model secara realistis:
- **Qwen2.5-Coder-7B-Instruct** dipilih karena dioptimalkan khusus untuk pembuatan kode/SQL — jauh lebih presisi dari model general-purpose ukuran serupa untuk Text-to-SQL pada 9 tabel kanonikal.
- Alternatif yang ditolak:
  - **Llama 3.1 8B**: Kemampuan Text-to-SQL lebih rendah.
  - **Mistral 7B**: Format JSON kurang konsisten.
  - **Phi-3/Phi-4 mini**: Sering melanggar skema JSON/SQL.

**Serving via Ollama**: Satu biner terpadu, manajemen model otomatis, antarmuka HTTP baku, dan performa kuantisasi GGUF Q4 optimal di CPU.

---

## 3. Embedding: `BAAI/bge-m3` — Rasional & Pinning Parameter

- **Multibahasa**: Data publikasi Scopus dan pertanyaan pengguna sering mencampur bahasa Indonesia dan Inggris. Model khusus Inggris (`all-MiniLM-L6-v2`) ditolak.
- **Dimensi**: 1024 dimensi representasi padat (dense), cocok untuk indeks `pgvector` HNSW (`m=16, ef_construction=64`).
- **Pinning & Reproduksibilitas**: Commit model `BAAI/bge-m3`, versi `sentence-transformers`, dan batch size (32–64) di-pin pada lockfile untuk menjamin hasil retrieval deterministik.

### 3.1 Spesifikasi Vector Storage pgvector (Sinkronisasi `docs/04 §5/§7` + `docs/05 §5.2` + `docs/12 §4` — tanpa menduplikasi DDL penuh)

> Status: **SEBAGIAN DONE.** Ekstensi `vector` (`CREATE EXTENSION IF NOT EXISTS vector`) **DONE — sudah terpasang di PostgreSQL DB + Supabase** (tanpa verifikasi ulang per instruksi). Kolom `chunks.embedding` + kolom metadata, data vektor, dan indeks HNSW **PENDING** (Task 1 / Task 8.5). DDL otoritatif tetap di `docs/04`; bagian ini hanya ringkasan stack agar `09` mencerminkan cakupan vector storage dengan benar.

- **(a) Kolom vektor + metadata pada `chunks` (Task 1):** `embedding vector(1024)` + `embedding_model DEFAULT 'BAAI/bge-m3'` + `embedding_version DEFAULT 'v1.0'` + `embedding_dimension DEFAULT 1024`. Kaitan kembali ke artikel asal via `chunks.publication_id FK → publications.publication_id` (tidak ada kolom baru).
- **(b) Format input embedding (terkunci):** teks terkonstruksi `Title: {title}\nAbstract: {abstract}` dari `publications.title` + `chunks.chunk_text` (`section = 'title_abstract'`), batch 32–64, idempotent resume (`docs/12 §4`, `docs/10 Task 1a–1d`).
- **(c) Indeks HNSW:** `idx_chunks_embedding_hnsw USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)` + `idx_chunks_pub_id ON chunks (publication_id)` + `ANALYZE chunks` setelah Task 1c.
- **(d) Kontrak retrieval `VectorRoute`:** operator `<=>` (cosine), gerbang `>= 0.65` (di bawah ambang → `status: not_found` deterministik tanpa LLM), `DISTINCT ON (p.publication_id) LIMIT 8` = 8 publikasi unik + join metadata `title/year/doi/eid` untuk sitasi `[Judul, Tahun, DOI]` / `[Judul, Tahun, no-doi]` (`docs/05 §5.2`).
- **(e) Indeks vektor turunan kedua (Task 8.5, PLANNED):** `topics.representation_vector vector(1024)` (centroid `BAAI/bge-m3` rata-rata anggota klaster) + `idx_topics_rep_vector_hnsw USING hnsw (representation_vector vector_cosine_ops) WITH (m = 16, ef_construction = 64)` untuk routing semantik topik pada `HybridRoute` (`docs/04 §7.1`). Bukan pengganti `chunks.embedding`.

---

## 4. Backend: Python/FastAPI — Pemisahan dari Next.js

Ekosistem parsing AST (`sqlglot`), embedding (`sentence-transformers`), dan orkestrasi bukti jauh lebih alami (native) dan aman di Python. FastAPI dipilih karena mendukung I/O async penuh untuk koneksi database PostgreSQL dan Ollama, serta menyediakan validasi skema ketat via Pydantic v2.

> **Catatan TBD (tidak dikunci di dokumen ini):** pilihan driver async **`asyncpg` vs `psycopg3`** berstatus **TBD — diputuskan di Task 2** (`docs/11 Fase 0` menulis `asyncpg/psycopg3` sebagai alternatif). Lihat §9. Detail koneksi wajib tetap: role `app_readonly` (hanya SELECT) + `SET search_path = public` + `statement_timeout = '10s'` per checkout pool (`docs/08 §1`).

---

## 5. Rasional: Mengapa Tanpa Framework RAG Berat (LangChain/LlamaIndex) — Dipertahankan

Framework seperti LangChain/LlamaIndex dihindari untuk MVP karena lapisan abstraksi yang berlebih menyulitkan debugging pada model 7B CPU. Memanggil Ollama via klien HTTP standar dan PostgreSQL via SQL mentah (raw) terparameterisasi jauh lebih transparan, mudah diaudit, dan aman terhadap kegagalan sintaks.

> Keputusan ini **dipertahankan pada v3.6.1** sesuai constraint CPU-only (`docs/02 C1`) dan cakupan MVP (`docs/01 §6`). Tidak ada framework RAG berat yang ditambahkan.

---

## 6. Pengembangan Lokal & Deployment

- **Dev lokal**: Docker Compose dengan service `backend` dan `ollama`, terhubung langsung ke database PostgreSQL prototipe.
- **MVP Produksi**: Single VPS (8 vCPU, 16GB RAM) menjalankan Docker Compose yang sama, melayani konkurensi 1–2 request dalam batas latensi 15 detik.
- **Frontend**: Next.js di-deploy via Vercel dengan environment variable menunjuk ke URL backend FastAPI.
- **Klarifikasi prasyarat (C4, selaras `docs/11 Fase 0`):** PostgreSQL adalah prasyarat eksternal yang sudah ada; Compose MVP hanya berisi `backend` + `ollama`, tidak mem-build image Postgres baru.

---

## 7. Strategi Teknologi Graf — Dipertahankan (MVP vs Pasca-MVP)

- **Fase MVP**: Penelusuran jaringan kolaborasi dijalankan via **Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4)** pada tabel edge `institution_collaboration` dan `author_collaboration`.
- **Fase Pasca-MVP (Fase 9)**: Evaluasi graf lanjutan menetapkan **Apache AGE** sebagai ekstensi native PostgreSQL pilihan untuk mengeksekusi kueri openCypher langsung di dalam database PostgreSQL tanpa memerlukan runtime database graf terpisah.

> Penundaan Apache AGE / SSE streaming (`/api/v1/ask/stream`) / resource endpoints / Celery+Redis / GPU-32B / Supabase-RLS ke Fase 9–11 **dipertahankan pada v3.6.1** sesuai `docs/01 §6` dan `docs/11 Fase 9–11`. Tidak ada infrastruktur graf mandiri di MVP.

---

## 8. Dependency Antar Technology (Selaras `docs/10` Task 0→12)

| Dari → Ke | Dependency | Task pemblokir |
|---|---|---|
| `data/*_cleaned.csv` (DONE) → 9 tabel Silver PostgreSQL (DONE) | Load + cleaning Python; kredensial internal | Baseline — tidak memblokir, sudah terisi |
| 9 tabel Silver → `scripts/verify_schema.py` | `information_schema.columns` vs `docs/04 §4` | Task 0 memblokir semuanya |
| `chunks` + `publications.title` → `scripts/embed_chunks.py` (`BAAI/bge-m3`, batch 32–64) → `chunks.embedding` + HNSW | Ekstensi `vector` DONE (DB + Supabase); Python 3.11 + `sentence-transformers` + `pgvector`; format `Title+Abstract` (§3.1) | Task 1 (1a NEXT, 1b–1d PENDING; kolom + data + HNSW belum ada) memblokir `VectorRoute` (Task 6) |
| FastAPI + Pydantic v2 + driver async (TBD §9) + Ollama (`Qwen2.5-Coder-7B`) | Docker Compose (`backend` + `ollama`); Postgres eksternal; `app_readonly` + timeout 10s | Task 2–3 memblokir Task 4–10 |
| `QuestionRouter` + `EntityResolutionGate` → `SqlRetriever` (`sqlglot` AST, `LIMIT 50`) | Skema 9 tabel sebagai system prompt; whitelist/blacklist | Task 4–5 → irisan terstruktur |
| `chunks.embedding` HNSW → `VectorRetriever` (`<=>`, gate `>= 0.65`, `DISTINCT ON LIMIT 8`) | Model query-embedding sama dengan indexing (path TBD §9) | Task 6 (butuh Task 1) |
| `pub_author`/`pub_institution` → `scripts/build_edges.py` → 2 edge tables → `GraphRetriever` T1–T4 (`max_hops=3`) | Self-join `a < b`, `COUNT` + `ARRAY_AGG`, `via_publication_ids` | Task 8 (butuh Task 0+4) |
| Silver + `chunks.embedding` → BERTopic/co-word + Pandas/sklearn/NetworkX → `topics` → `topic_evolution` + `researcher_expertise` → `HybridRetriever` | Centroid `representation_vector vector(1024)` + HNSW; bobot `w1=0.30, w2=0.25, w3=0.25, w4=0.20` | Task 8.5 (butuh Task 1+8) |
| `EvidenceUnifier` + `EvidenceRanker` → `AnswerSynthesizer` (Qwen via Ollama) → `CitationVerifier` → `POST /api/v1/ask` → Next.js (Vercel) | `EvidenceSet`, framing `UNTRUSTED DATA`, short-circuit 0-bukti tanpa LLM | Task 7+9 → Task 10 → Task 11 → Task 12 E2E |

---

## 9. TBD Eksplisit (Jangan Dikarang — Diputuskan di Task 0/2/4)

| # | Item TBD | Konteks | Diputuskan di |
|---|---|---|---|
| TBD-1 | Driver async: `asyncpg` **vs** `psycopg3` | `docs/11 Fase 0` menulis alternatif; `09 §4` tidak mengunci | Task 2 |
| TBD-2 | Server ASGI (`uvicorn` + versi) | Dibutuhkan `Task 2` tapi belum ada dasar dokumen | Task 2 |
| TBD-3 | Test tooling (`pytest` / `httpx` + versi) untuk suite router/SQL/vector/graph/evidence/API + E2E 12-kueri | Dibutuhkan `Task 12` (`docs/10`, `docs/11 Fase 8`) | Task 2 / Task 12 |
| TBD-4 | Versi pin: `pgvector`, `Ollama`, `sqlglot`, `Pydantic v2` minor, `sentence-transformers`, commit `BAAI/bge-m3` + lockfile | `09 §3` menjanjikan pin di lockfile; lockfile belum ada (belum ada `backend/`) | Task 0/2 |
| TBD-5 | Path online query-embedding: `sentence-transformers` lokal **vs** endpoint embedding Ollama | `docs/11 Fase 4` menulis alternatif; `09` hanya mengunci batch offline | Task 4 / Task 6 |
| TBD-6 | Frontend CSS/component lib + versi Node | `docs/07` hanya mengunci token warna/font, bukan lib | Task 11 |
| TBD-7 | Kriteria evaluasi Apache AGE Fase 9 (versi + benchmark) | `09 §7` hanya menetapkan sebagai target evaluasi | Fase 9 (pasca-MVP) |

---

## 10. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status |
|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED |
| **Penyimpanan vector** | `pgvector` — ekstensi `vector` DONE (terpasang di PostgreSQL DB + Supabase); kolom `chunks.embedding vector(1024)` + data + HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) PENDING Task 1 | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
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

## 11. Keputusan Arsitektur Kanonikal

1. **Keputusan Sitasi Tanpa DOI:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Keputusan Ambang Batas Kesamaan Kosinus (`VectorRoute`):**
   - *Keputusan:* Nilai ambang batas kesamaan kosinus dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri yang menghasilkan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Keputusan Mesin Graf Pasca-MVP:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 12. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/09 Tech Stack.md` v3.6.2 | Update status: ekstensi pgvector `vector` DONE (terpasang di PostgreSQL DB + Supabase, tanpa verifikasi ulang); kolom + data + HNSW tetap PENDING Task 1; `VectorRoute` tetap BLOCKED | Update status ekstensi per info user; tanpa perubahan keputusan teknis |
| `docs/09 Tech Stack.md` v3.6.1 | Sync-only tanpa perubahan keputusan teknis: (1) baris Database §1 dipecah DONE vs PLANNED + tegaskan VectorRoute BLOCKED; (2) tambah §3.1 vector storage (metadata, input Title+Abstract, HNSW, gate + DISTINCT ON, topics.representation_vector); (3) klarifikasi Compose §6 (Postgres eksternal); (4) tambah §8 dependency + §9 TBD-1–TBD-7; (5) tegaskan penolakan LangChain/LlamaIndex + penundaan AGE/SSE/Redis/GPU dipertahankan; (6) renumber §8–§10 menjadi §10–§12 | Sinkronisasi redaksi C1–C4 + TBD C5 sesuai review lintas-dokumen 2026-09-27; cegah asumsi vector ready |
| `docs/09 Tech Stack.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/09 Tech Stack.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; embedding pgvector PENDING | Sinkronisasi progress aktual 2026-09-27 |
| `docs/09 Tech Stack.md` v3.4.0 | Menyelaraskan referensi stack basis data dengan 9 tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/09 Tech Stack.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan strategi graf Apache AGE | Menutup open decisions menjadi keputusan kanonikal |
| `docs/09 Tech Stack.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumentasi di seluruh repository |
