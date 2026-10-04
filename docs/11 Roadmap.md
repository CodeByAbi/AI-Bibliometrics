# Roadmap Teknis — Prototipe Scopus menuju Riset Intelijen

**Versi Dokumen:** 3.8.0 (Fase 8 IN PROGRESS — rencana eksekusi gerbang sign-off MVP + Task 11)  
**Tanggal Status:** 2026-10-03  
**Menggantikan:** `11 Roadmap.md` v3.7.2 (2026-10-03)
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  
---

## 1. Ringkasan Eksekutif & Penilaian Status Proyek

### 1.1 Posisi Saat Ini (Realitas Audit Repositori)
Inspeksi repositori per **2026-09-29** menetapkan status tersinkronisasi berikut:
- **DONE — Database:** Database PostgreSQL **sudah dibuat dan siap pakai**, memuat **dataset prototipe kecil** (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) yang disiapkan khusus untuk validasi end-to-end. Kredensial telah diamankan secara internal.
- **DONE — Pembersihan & Export:** Data Scopus **sudah melalui pembersihan (cleaning) dan berhasil di-export** sebagai 9 file `data/*_cleaned.csv`.
- **DONE — Vector Storage & Indexing (Fase 1):** Kolom `chunks.embedding` **sudah dibuat dan terisi 100% (40/40 chunk)** dengan representasi 1024-dimensi `BAAI/bge-m3`. Indeks HNSW (`idx_chunks_embedding_hnsw`) dan `idx_chunks_pub_id` sudah aktif dan terverifikasi.
- **DONE — Materialisasi Tabel Edge (Fase 1):** Tabel derived edge `institution_collaboration` (254 edge) dan `author_collaboration` (484 edge) **sudah dimaterialisasi secara idempoten** dan diverifikasi (`CHECK (a < b)`, `via_publication_ids` valid).
- **DONE — Kerangka Gateway API & DB Pool (Fase 2):** Backend FastAPI (`backend/app/`), pool async `asyncpg`, endpoint `GET /api/v1/health`, kontrak `POST /api/v1/ask`, middleware `X-Request-ID`, rate limiting, logging terstruktur, dan client Ollama terverifikasi.
- **DONE — VERIFIED — Fase 3:** Vertical Slice QueryRouter & Retrieval Terstruktur / SQL (`QuestionRouter` & `SqlRetriever` tervalidasi `sqlglot`) — implementasi, uji hijau, dan sign-off E2E Task 12 (14/14, 2026-10-03).
- **IMPLEMENTED — VERIFICATION PENDING — Fase 4:** Mesin Retrieval Semantik / Vector (`VectorRetriever` + online embedding `BAAI/bge-m3` + `VectorAnswerSynthesizer` + `CitationVerifier` Vector-scoped) — unit + integration hijau; checklist runtime live-DB + sign-off E2E (Task 12) masih pending.
- **IMPLEMENTED — VERIFICATION PENDING — Fase 5:** Evidence Layer generik (`EvidenceUnifier` + `EvidenceRanker` + `EvidenceSet` + `EvidenceItem`) — unit 21 + integration 9 + E2E mock 12 query hijau; live E2E (Task 12) pending; `from_graph sources` resolved di Fase 6.
- **DONE — VERIFIED — Fase 6:** Mesin Retrieval Graf (`GraphRetriever` T1–T4 + `GraphAnswerSynthesizer` + `CitationVerifier` + wiring `GraphRoute` di `POST /api/v1/ask`) — terverifikasi.
- **DONE — VERIFIED — Fase 7:** Retrieval Hybrid Multi-Rute & Sintesis Jawaban Ter-grounding (`HybridRetriever` + Gold Analytics `topics`, `topic_evolution`, `researcher_expertise` + `EvidenceUnifier.from_hybrid` + `HybridAnswerSynthesizer` + unified `AnswerSynthesizer` + sintesis LLM opt-in Qwen2.5-Coder via Ollama dengan fallback deterministik + full wiring 4-route di `POST /api/v1/ask`) — terverifikasi: 333 tests terkumpul (319 unit+integration hijau, 12 E2E mock hijau + 2 live-only) + 14/14 live E2E queries passed (laporan: `reports/fase7_closeout.md`).
- **IN PROGRESS — Fase 8:** Gerbang Sign-Off MVP. Task 12 (benchmark 12 kueri) sudah 14/14 live, sehingga Fase 8 adalah formalisasi: coverage gate, baseline latensi, audit UI Task 11, dan pengecekan 24 item checklist AC yang masih `[ ]`. Rencana eksekusi 7 workstream (A-G) di `reports/fase8_execution_plan.md`; normative di §Fase 8 dokumen ini.
- **NEXT — Task 11:** UI Next.js Chat 2-panel dense — implementasi sudah ada di working tree (`frontend/components/Workspace/`, 22 file untracked) tetapi belum diaudit terhadap `AC-UI-1..7`, belum diverifikasi `npm run verify`, dan belum di-commit.

### 1.1b Pelacak Progres (Sinkronisasi 2026-10-03)

| Status | Item |
|---|---|
| DONE — VERIFIED | Database PostgreSQL · Dataset prototipe (9 tabel kanonikal) · Pembersihan data (cleaning) · Export data bersih (`data/*_cleaned.csv`) · Task 0 (Audit Skema) · Task 1 (Batch Embedding & Indeks HNSW) · Task 8-edge (Materialisasi Edge Graf) · Task 2 (FastAPI Framework & DB Pool) · Task 3 (Ollama Setup & Health) · Task 4 (QueryRouter & EntityResolutionGate) · Task 5 (SqlRetriever & AST Validator) · Task 6 (VectorRetriever + Online Embedding) · Task 7 (Evidence Layer generik: EvidenceUnifier + EvidenceRanker + EvidenceSet + EvidenceItem) · Task 8-retriever (GraphRetriever T1–T4 + GraphAnswerSynthesizer) · Task 8.5 (Gold Analytics Materialization: topics, topic_evolution, researcher_expertise) · Task 9-full (Unified AnswerSynthesizer & CitationVerifier across 4 routes) · Task 10-full (API POST /api/v1/ask full wiring across SQL, Vector, Graph, Hybrid) · Task 12 (E2E 12-query benchmark 14/14 passed) |
| IN PROGRESS | Fase 8 (Gerbang Sign-Off MVP: coverage gate, baseline latensi, R2a gate `<200ms` nol-bukti, audit `AC-UI-1..7`, pencentangan 24 AC, rekonsiliasi `docs/01`+`docs/02`) |
| NEXT | Task 11 (Frontend Next.js UI Chat 2-panel — audit `AC-UI-1..7` + `npm run verify` + commit) |
| PLANNED / POST-MVP | Fase 9 (Apache AGE & Algoritma Graf Kompleks) · Fase 10 (Streaming & Async Workers) |
### 1.2 Apa yang Harus Dibangun Terlebih Dahulu?
Pengembangan tidak boleh dimulai dari UI atau orkestrasi kompleks. Urutan prasyarat absolut adalah:
1. **Baseline Repositori & Infrastruktur (Fase 0)**: Bangun struktur direktori proyek, Docker Compose (FastAPI + Ollama), kontrak environment, dan jalankan skrip verifikasi skema database Task 0 terhadap 9 tabel kanonikal yang sudah ada.
2. **Fondasi Data Offline & Indexing (Fase 1)**: Sediakan (provision) `chunks.embedding vector(1024)` pgvector, eksekusi pipeline batch embedding (`BAAI/bge-m3`), bangun indeks HNSW, dan materialisasi relasi graf kanonikal.
3. **Inti API & Orkestrasi (Fase 2)**: Kerangka aplikasi FastAPI, rute dasar `/api/v1`, validasi input Pydantic, pembuatan `request_id`, pencatatan log terstruktur, dan pooling database read-only.
4. **Vertical Slice Terstruktur (Fase 3)**: Buktikan rantai end-to-end terlebih dahulu pada kueri nyata menggunakan routing deterministik dan Text-to-SQL tervalidasi pada tabel kanonikal.

### 1.3 Apa Definisi MVP?
**MVP** didefinisikan secara ketat sebagai keberhasilan eksekusi alur kerja tanya-jawab end-to-end yang beroperasi di atas **data nyata**, mengembalikan **jawaban ter-grounding** dengan **sitasi terverifikasi**:
- **Backend FastAPI** dengan endpoint berversi (`POST /api/v1/ask`, `GET /api/v1/health`).
- **Validasi Batas Ketat** (validasi skema Pydantic, penelusuran `request_id`, kategorisasi error).
- **QueryRouter Multi-Rute** yang memprioritaskan aturan pola deterministik dan ekstraksi entitas (fallback router LLM).
- **Empat Retriever Inti**:
  - `SqlRetriever` (tervalidasi AST pada 9 tabel kanonikal, peran read-only, pemeriksaan agregasi eksak, `LIMIT 50`).
  - `VectorRetriever` (`bge-m3` 1024d, similaritas kosinus HNSW pgvector, jendela ANN ber-*overfetch* lalu `DISTINCT ON` di luar jendela, ambang $\ge 0.48$).
  - `GraphRetriever` (derived edge tables `institution_collaboration` dan `author_collaboration`; traversal terbatas maks 3 hop; pelacakan provenance).
  - `HybridRetriever` (Lapisan Gold `topics`, `topic_evolution`, `researcher_expertise` + Silver & `chunks`).
- **Lapisan Bukti (Evidence Layer)**: skema `Evidence`, `EvidenceSet`, dan `EvidenceUnifier` yang memastikan tidak ada baris/chunk mentah yang melewati normalisasi.
- **Peringkat Bukti Deterministik**: skoring eksplisit berbasis relevansi dan provenance.
- **Sintesiser Jawaban**: pembuatan (generation) ter-grounding hanya dari bukti terverifikasi, sitasi `[Title, Year, DOI]` / `[Title, Year, no-doi]`, penanganan `not_found` deterministik, dan verifikasi sitasi post-hoc.
- **Invariant Keamanan**: peran DB `app_readonly`, `SET search_path = public`, timeout statement (10 detik), pertahanan injeksi prompt (bukti diperlakukan sebagai data tidak tepercaya).

---

## 2. Cetak Biru Arsitektur & Invariant

```
                              PIPELINE OFFLINE
   Data Mentah Scopus [DONE — sumber pembersihan]
          ↓
     Validasi [DONE]
          ↓
      Pembersihan (Cleaning) [DONE]
          ↓
   Data Bersih / Export [DONE — data/*_cleaned.csv + 9 tabel Silver]
          ↓
    Normalisasi [DONE pada data tersimpan; persiapan batch NEXT]
          ↓
  ┌────────────────────────────────────────────────────────┐
  │   9 Tabel Kanonikal PostgreSQL (Prototipe Saat Ini)    │
  │   [DONE — ter-load & siap pakai]                       │
  └────────────────────────────────────────────────────────┘
           ├───────────────────────────────┐
           ↓                               ↓
     Persiapan Chunk                  Ekstraksi Graf
     [DONE — Task 1a]                [DONE — Task 8-edge]
            ↓                               ↓
     Embedding (BAAI/bge-m3)         Materialisasi Tabel Edge
     [DONE — Task 1b]                [DONE — Task 8-edge]
            ↓                               ↓
     pgvector (chunks.embedding)     institution/author_collab
     [DONE — Task 1c, 40/40]         [DONE — 254 + 484 edge]
     [DONE — Task 1d HNSW]           [Indeks Relasi Turunan]


                              PIPELINE ONLINE
  Pertanyaan Pengguna
       ↓
  Gateway FastAPI (`POST /api/v1/ask`)
       ↓
  Validasi Input & Pembuatan ID Request
       ↓
  QueryRouter (Deterministik / Berbasis Aturan + Pengklasifikasi)
       ↓
  Mesin Retrieval (Fan-out)
       ├── Terstruktur (SqlRetriever)   ──> 9 Tabel Silver
       ├── Semantik (VectorRetriever)  ──> chunks pgvector HNSW
       ├── Graf (GraphRetriever)      ──> Derived Edge Tables T1-T4
       └── Hybrid (HybridRetriever)    ──> Gold Analytics + Silver
               ↓                   ↓                 ↓
       ┌───────────────────────────────────────────────┐
        │          Evidence Normalization Layer            │
       │        (Skema Evidence / EvidenceSet)         │
       └───────────────────────────────────────────────┘
                               ↓
                       EvidenceUnifier
                               ↓
                       EvidenceRanker
                  (Skoring Deterministik)
                               ↓
                      AnswerSynthesizer
                (Isolasi Prompt & Pertahanan)
                               ↓
                    CitationVerifier (Post-Hoc)
                               ↓
               Jawaban Ter-grounding (`status: ok / not_found`)
```

### Invariant Inti
1. **Source-of-Truth Invariant**: PostgreSQL Silver adalah satu-satunya canonical source of truth. pgvector, derived edge tables, dan Gold analytics adalah struktur turunan read-only.
2. **Evidence Normalization Invariant**: Tidak ada baris database, chunk vector, atau graph edge mentah yang boleh diteruskan langsung ke LLM. Semua data retrieval WAJIB melewati `EvidenceUnifier` menjadi `EvidenceSet` ternormalisasi.
3. **Security Invariant**: Koneksi database WAJIB menggunakan peran `app_readonly` dengan `SET search_path = public` dan `statement_timeout = 10s`. Teks hasil retrieval adalah **DATA TIDAK TERPERCAYA** dan tidak dapat mengesampingkan instruksi sistem.
4. **Zero-Hallucination Invariant**: Jika retrieval mengembalikan 0 item bukti, sistem WAJIB mengembalikan `status: not_found` secara deterministik tanpa mengeksekusi pemanggilan sintesis LLM. (Kosakata lama `insufficient_evidence` resmi dipetakan ke `not_found`; skema `AskResponse.status` hanya mengenal `ok | not_found | needs_clarification | error`.)

---

## 3. Label Status

| Label | Makna |
|---|---|
| `[CURRENT]` | Fase aktif yang sedang diinspeksi atau baseline operasional |
| `[NEXT]` | Prioritas rekayasa terdekat yang dieksekusi berikutnya |
| `[BLOCKED]` | Tidak dapat lanjut hingga tugas/infrastruktur dependensi eksplisit selesai |
| `[PLANNED]` | Tonggak (milestone) yang didefinisikan secara arsitektural dan terjadwal dalam batas MVP |
| `[POST-MVP]` | Peningkatan pasca-MVP terverifikasi (membutuhkan baseline MVP yang berjalan) |
| `[FUTURE]` | Pengerasan produksi, skala, atau tonggak riset jangka panjang |

---

## 4. Roadmap Bertahap Komprehensif

```
Fase 0 ──> Fase 1 ──> Fase 2 ──> Fase 3 (Vertical Slice) ──> Fase 4
                                                                    │
┌──────────────────────────────────────────────────────────────────┘
▼
Fase 5 ──> Fase 6 ──> Fase 7 ──> Fase 8 (Gerbang MVP)
                                        │
┌──────────────────────────────────────┘
▼
Fase 9 ──> Fase 10 ──> Fase 11 (Produksi)
```

### Fase 0 — Baseline Repositori, Lingkungan & Arsitektur
**Status:** `[DONE]`  
**Tujuan:** Menyiapkan ruang kerja rekayasa konkret, definisi container Docker, kontrak environment, dan memverifikasi konsistensi skema database live terhadap 9 tabel kanonikal.

- **Prasyarat:** Kredensial akses database PostgreSQL yang sudah ada.
- **Cakupan & Deliverable:**
   - Buat directory layout: `backend/app/`, `database/`, `scripts/`, `tests/`, `docker/`.
  - Buat `.env.example` yang merinci string koneksi database, host Ollama, identifier model, dan timeout.
  - Siapkan definisi Docker Compose berisi:
    - `backend` (FastAPI, Python 3.11+, Pydantic v2, sqlglot, asyncpg/psycopg3).
    - `ollama` (container serving model lokal).
  - Buat skrip validasi database `scripts/verify_schema.py` untuk introspeksi `information_schema.columns` (Task 0).
  - Rekonsiliasi perbedaan antara kolom database live dan `04 Database Schema.md` §4.
- **Output:** Dokumen skema database terverifikasi, environment Docker Compose operasional, lockfile dependensi terpadu.
- **Memblokir:** Seluruh fase implementasi berikutnya (Fase 1 hingga Fase 11).
- **Kriteria Penerimaan:**
  - `docker compose build` sukses dengan bersih.
  - `python scripts/verify_schema.py` terhubung ke PostgreSQL, membuang (dump) seluruh kolom, dan mengonfirmasi kecocokan eksak dengan `04 Database Schema.md` §4.

---

### Fase 1 — Verifikasi Data Kanonikal & Pipeline Indexing Offline
**Status:** `[DONE]`  
**Tujuan:** Menyediakan pgvector, menghitung embedding vector untuk seluruh chunk dokumen pada `chunks`, dan mematerialisasi edge relasi awal dari tabel kanonikal.

- **Prasyarat:** Penyelesaian Fase 0; koneksi PostgreSQL dengan hak migrasi.
- **Cakupan & Deliverable:**
  - Eksekusi migrasi DDL: `CREATE EXTENSION IF NOT EXISTS vector;`.
  - Tambah kolom vector: `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(1024);`.
  - Inspeksi granularitas chunk: eksekusi `SELECT COUNT(*), COUNT(DISTINCT publication_id) FROM chunks;` dan catat rasionya.
  - Implementasikan skrip batch embedding offline `scripts/embed_chunks.py`:
    - Model: `BAAI/bge-m3` (versi/commit terkunci, 1024 dimensi).
    - Ukuran batch: 32–64 item per batch, dioptimalkan CPU.
    - Toleransi kesalahan (fault tolerance): lanjutan idempoten (resumption), pelacakan record terproses, gagal, dan belum terindeks.
    - Pencatatan metadata: catat `embedding_model`, `embedding_version`, dan `embedding_dimension`.
  - Bangun indeks HNSW: `CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);`.
  - Materialisasi tabel edge kolaborasi awal:
    - Buat `institution_collaboration` dan `author_collaboration` dengan pengurutan kanonikal (`a < b`) dan array provenance `via_publication_ids`.
    - Jalankan skrip ekstraksi idempoten `scripts/build_edges.py`.
- **Output:** Kolom `chunks.embedding` terisi beserta indeks HNSW; tabel `institution_collaboration` dan `author_collaboration` terisi.
- **Memblokir:** Fase 4 (Retrieval Semantik), Fase 6 (Retrieval Knowledge Graph), Fase 7 (Retrieval Hybrid).
- **Kriteria Penerimaan:**
  - `SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;` mengembalikan `0`.
  - Kueri uji `SELECT publication_id, embedding <=> :test_vec FROM chunks LIMIT 5;` tereksekusi dalam < 50ms memakai pemindaian indeks (index scan).
  - Kedua tabel edge berisi baris tervalidasi dengan `via_publication_ids` tak-kosong.

---

### Fase 2 — Fondasi Gateway API & Orkestrasi
**Status:** `[DONE]`  
**Tujuan:** Mengimplementasikan gateway aplikasi FastAPI yang diperkeras (hardened), routing dasar, validasi request/respons Pydantic, manajemen lifecycle, dan batas keamanan.

- **Prasyarat:** Penyelesaian Fase 0.
- **Cakupan & Deliverable:**
  - Inisialisasi backend FastAPI terstruktur per domain (`routers/`, `services/`, `models/`, `db/`).
  - Implementasikan kontrak target normatif `POST /api/v1/ask` dan `GET /api/v1/health` (secara formal menggantikan `/api/query`).
  - Implementasikan middleware validasi request:
    - `question`: string, `min_length = 3`, `max_length = 1000`, whitespace di-trim.
    - `filters`: tervalidasi skema; filter malformed mengembalikan HTTP 422 segera.
  - Implementasikan middleware `request_id` yang membuat UUIDv4 unik per request, diinjeksikan ke konteks, header, dan log.
  - Siapkan pool koneksi database async dengan parameter keamanan ketat:
    - Terhubung via peran `app_readonly`.
    - Tegakkan `SET search_path = public` pada checkout koneksi pool.
    - Tegakkan `SET statement_timeout = '10s'`.
  - Implementasikan pencatatan log JSON terstruktur yang menangkap `timestamp`, `request_id`, `route`, `status`, `latency_ms`, dan `error_code`.
- **Output:** Server backend fungsional yang menerima request, menegakkan batas input, dan memvalidasi konektivitas database read-only.
- **Memblokir:** Fase 3 (Irisan Retrieval Terstruktur), Fase 8 (Verifikasi E2E).
- **Kriteria Penerimaan:**
  - `GET /api/v1/health` mengembalikan HTTP 200 dengan pemeriksaan database lolos tanpa mengekspos rahasia internal.
  - Payload invalid (`{"question": "a"}`) mengembalikan HTTP 422 dengan error validasi yang dapat ditindaklanjuti.
  - Setiap upaya tulis simulasi (`INSERT`/`UPDATE`) melalui pool koneksi segera memicu pengecualian izin database.

---

### Fase 3 — Vertical Slice QueryRouter & Retrieval Terstruktur / SQL
**Status:** `[CURRENT]`  
**Tujuan:** Membuktikan vertical slice pertama sistem yang berjalan: rute pertanyaan masuk, buat dan validasi SQL read-only, eksekusi terhadap data nyata di 9 tabel kanonikal, dan kembalikan jawaban ter-grounding.

- **Prasyarat:** Fase 1 (Skema Terverifikasi) dan Fase 2 (Basis FastAPI).
- **Cakupan & Deliverable:**
  - Implementasikan layanan `QueryRouter`:
    - Klasifikasi primer: parsing intent berbasis aturan (kata kunci, pola regex, pemicu intent terstruktur).
    - Ekstraksi kontrak-entitas bertipe: `YearFilter`, `country`, `author_name`, `institution_name`, `keyword`.
    - Fallback: pengklasifikasi ringan atau prompt LLM ringan-skema yang mengembalikan JSON terstruktur.
  - Implementasikan layanan `SqlRetriever`:
    - Prompt sistem berisi skema 9 tabel kanonikal dan aturan agregasi ketat.
    - Validasi AST memakai `sqlglot`:
      - Larang statement non-`SELECT`.
      - Tegakkan whitelist (whitelist) nama tabel dan kolom.
      - Tegakkan aggregate-shape check (pertanyaan bermaksud agregat wajib memakai `COUNT`, `SUM`, atau `GROUP BY`).
      - Tegakkan double-count prevention: `COUNT(DISTINCT publication_id)` pada join junction.
      - Tegakkan `LIMIT 50` non-agregat.
  - Implementasikan irisan minimal `AnswerSynthesizer`: format baris SQL menjadi teks tabular/berpoin tanpa halusinasi.
- **Output:** Vertical slice fungsional untuk pertanyaan terstruktur (misal: "Siapa 5 penulis paling produktif tahun 2023?").
- **Memblokir:** Fase 5 (Lapisan Bukti), Fase 7 (Retrieval Hybrid).
- **Kriteria Penerimaan:**
  - Pertanyaan pengguna nyata `Who are the top 5 authors in 2023?` mengenai `POST /api/v1/ask`, terute ke `structured`, mengeksekusi SQL terverifikasi, dan mengembalikan hitungan akurat yang terverifikasi terhadap kueri DB langsung.
  - Input destruktif ("DROP TABLE publications") ditolak validator AST dengan HTTP 422 sebelum mencapai database.

---

### Fase 4 — Mesin Retrieval Semantik / Vector
**Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit + integration hijau; checklist runtime live-DB + sign-off E2E Task 12 pending)
**Tujuan:** Menghadirkan pencarian dokumen berbasis kemiripan pada abstrak publikasi riset dengan deduplikasi ketat dan penegakan ambang ($\ge 0.48$, dikalibrasi P1 dari 0.65 via benchmark berlabel).

- **Prasyarat:** Fase 1 (Embedding Terisi & Terindeks) dan Fase 2 (Basis FastAPI).
- **Cakupan & Deliverable:**
  - Implementasikan klien embedding online memakai `BAAI/bge-m3` (sentence-transformers lokal atau endpoint embedding Ollama).
  - Implementasikan `VectorRetriever`:
    - Embed pertanyaan pengguna menjadi vector float 1024-dimensi.
    - Eksekusi kueri jarak kosinus (`<=>`) terhadap `chunks` dalam **jendela ANN** yang diurutkan murni oleh operator jarak (satu-satunya urutan yang dapat dilayani indeks HNSW), dengan *overfetch* 25× limit.
    - Tegakkan deduplikasi **setelah** jendela ANN: `DISTINCT ON (ac.publication_id)` pada hasil ANN, baru `LIMIT 8`. Deduplikasi tidak boleh berada di dalam jendela ANN — `ORDER BY publication_id` di depan operator jarak membuat plansyenya memindai penuh dan indeks HNSW tidak terpakai.
    - Gabung (join) dengan tabel `publications` untuk mengambil metadata kanonikal (`title`, `year`, `doi`, `eid`).
  - Implementasikan gerbang ambang skor similaritas: saring chunk di bawah ambang dasar similaritas ($\ge 0.48$, dikalibrasi P1 dari 0.65 via benchmark berlabel).
  - Tangani skenario nol-kecocokan: kembalikan daftar kosong segera jika tidak ada chunk lolos ambang.
- **Output:** `VectorRetriever` teruji yang mengembalikan chunk publikasi topikal terdedup menurut naskah (paper). Implementasi: `backend/app/services/embedding.py` (dual-path lokal + fallback Ollama, dim-check 1024, guard non-finite) + `backend/app/services/retrievers/vector_retriever.py` (`build_query` dua tahap ANN→dedup, `LIMIT 8`, gate `>= 0.48`, `filters_ignored`, vektor ter-*bind* sebagai `$1`) + `VectorAnswerSynthesizer`/`CitationVerifier` (Task 9a, Vector-scoped) + wiring `POST /api/v1/ask` (`VectorRoute`).
- **Catatan verifikasi:** vektor precomputed divalidasi dimensi + finite sebelum di-*bind*; literal vektor dikirim sebagai parameter `$1` (bukan diinterpolasi) melalui codec `vector` di `pool._init_connection`, sedangkan threshold/filter/ID/limit juga `$N` parameterized; operator memakai kualifikasi `extensions.<=>` (asumsi ekstensi `vector` di skema `extensions`, layout Supabase). `hnsw.ef_search` dinaikkan ke `100` per koneksi karena deduksi ditumpuk di atas hasil ANN. Bentuk kueri diverifikasi terhadap **pernyataan produksi itu sendiri** melalui `VectorRetriever.build_query` pada `tests/integration/test_vector_live_hnsw.py`, bukan terhadap paraphrase tangan.
- **Memblokir:** Fase 5 (Lapisan Bukti generik), Fase 7 (Retrieval Hybrid).
- **Kriteria Penerimaan:**
  - Pertanyaan "papers about oxidative stress in Wharton's jelly" mengembalikan 8 publikasi topikal teratas dengan DOI dan abstrak valid.
  - Tidak ada satu publikasi pun muncul berulang dalam daftar chunk yang dikembalikan.
  - Kueri yang sama sekali tak-terkait ("how to bake bread") mengembalikan nol hasil tanpa memicu error database.

---

### Fase 5 — Unifikasi Lapisan Bukti & Peringkat Deterministik
**Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit + integration hijau; live E2E Task 12 pending)
**Tujuan:** Menegakkan kontrak bukti perantara kanonikal dan unifier, memisahkan (decouple) sumber retrieval dari sintesis jawaban.

- **Prasyarat:** Fase 3 (`SqlRetriever`) dan Fase 4 (`VectorRetriever`).
- **Cakupan & Deliverable:**
  - Model kanonikal `EvidenceObject{claim,metric,value,period,sources[],confidence}` + `EvidenceSourceRef{publication_id,doi,eid,title,year}` + `EvidenceItem{source_id,source_type,snippet→content,score,confidence,publication_id,title,year,doi,eid,provenance_ids,metadata}` + `EvidenceSet{query,evidence_objects,sources,items,filters_ignored,sql_executed,is_empty,count}` (`docs/05 §4`, `docs/06 §5`) — bukan skema lama `source_id/source_type/snippet/score`.
  - `EvidenceUnifier.from_sql` (5 kasus bentuk rows), `from_vector`, `from_graph`, `from_analytics`, `unify()` multi-source dedup by `publication_id` + merge provenance.
  - `EvidenceRanker` deterministik: objects by `(-confidence, -value, metric, claim)`; sources by `(-relevance, -year, title, id)`; items by `(-score, -confidence, -year, title, source_id)`. Isolasi eksplisit dari reranker masa depan.
  - Serialisasi: `to_metrics_json`, `to_chunks_text`, `to_prompt_context`, `to_untrusted_evidence_block` (framing `=== BEGIN/END RETRIEVED EVIDENCE (UNTRUSTED DATA) ===`).
  - Wiring production: `SqlAnswerSynthesizer`/`VectorAnswerSynthesizer` consume `EvidenceSet` (tidak ada raw-row-to-LLM); `AskResponse.debug.evidence_set` untuk `developer_mode`.
- **Output:** Lapisan data `EvidenceSet` terpadu + routing deterministik + test suite unit (21) + integration (8) + E2E mock (12).
- **Memblokir:** Fase 6 (Integrasi Graf), Fase 7 (Sintesis Jawaban), Fase 8 (Verifikasi E2E).
- **Kriteria Penerimaan:**
  - Hasil retrieval multi-sumber ternormalisasi ke skema `EvidenceSet` identik.
  - `unify()` menduplikasi record `publication_id` yang sama sambil menggabungkan provenance sumbernya.
  - Ranking deterministik: input yang sama selalu menghasilkan urutan yang sama.
  - Tidak ada path raw-row-to-LLM tersisa di production (`grep audit` lulus).
- **Known Gaps:** `from_graph` mengembalikan `sources=[]` (provenance hanya di `items.provenance_ids`) — fill saat `GraphRetriever` (Fase 6) hadir. Confidence vector = passthrough `round(similarity,4)` (bukan rescale 0.70-1.0), sesuai `docs/05 §4.1`.

---

### Fase 6 — Konstruksi Knowledge Graph & Retrieval Graf (Wajib MVP)
**Status:** `[IMPLEMENTED — VERIFICATION PENDING]` (unit 19 + integration 6 hijau; live E2E sign-off Task 12 pending)  
**Tujuan:** Mengimplementasikan kapabilitas Knowledge Graph yang wajib, menyelesaikan relasi entitas akademik inti dengan traversal multi-hop terbatas dan pelacakan provenance.

- **Prasyarat:** Fase 1 (Materialisasi Data) dan Fase 5 (Lapisan Bukti).
- **Cakupan & Deliverable:**
  - Permukaan bawaan (default) MVP: tabel edge PostgreSQL termaterialisasi (`institution_collaboration`, `author_collaboration`) sebagai indeks graf relasional.
  - **Implementasi GraphRetriever**:
    - Eksekusi traversal terparameterisasi (Templat T1: Kolaborator Institusi, T2: Co-author, T3: pemetaan Topik-Institusi, T4: Pencarian Jalur Terbatas).
    - Rel traversal: `max_hops = 3` dan `LIMIT 50` ditegakkan ketat.
    - Retensi provenance: setiap relasi graf wajib mengembalikan `via_publication_ids`.
    - Normalisasi bukti graf: konversi jalur/edge graf menjadi objek `Evidence` kanonikal dengan `source_type: "graph"` dan memperkaya metadata publikasi pada `EvidenceSet.sources`.
    - Implementasi `GraphAnswerSynthesizer` terintegrasi dengan `CitationVerifier`.
    - Wiring penuh `GraphRoute` pada `POST /api/v1/ask`.
- **Output:** `GraphRetriever` + `GraphAnswerSynthesizer` operasional yang mampu menjawab pertanyaan konektivitas relasional dengan provenance publikasi terverifikasi.
- **Memblokir:** Fase 7 (Retrieval Multi-Rute Hybrid), Fase 8 (Verifikasi E2E).
- **Kriteria Penerimaan:**
  - Kueri "Which institutions collaborated with AI researchers?" menelusuri `institution_collaboration` dan mengembalikan institusi tertaut disertai `via_publication_ids` konkret.
  - Traversal rekursif dijepit keras (hard-clamp) pada 3 hop; referensi sirkular berhenti (terminate) bersih tanpa pembengkakan memori atau timeout kueri.
---

### Fase 7 — Retrieval Hybrid Multi-Rute & Sintesis Jawaban Ter-grounding
**Status:** `[DONE — VERIFIED]`  
**Tujuan:** Menyatukan keempat mesin retrieval di bawah QueryRouter dan menghadirkan sintesis bahasa natural ter-grounding dengan verifikasi sitasi otomatis dan perlindungan injeksi prompt.

- **Prasyarat:** Fase 3 (SQL), Fase 4 (Vector), Fase 5 (Bukti), Fase 6 (Graf).
- **Cakupan & Deliverable:**
  - Implementasikan `HybridRetriever`:
    - Empat templat terparameterisasi sekuensial (tren topik Gold, kepakaran peneliti Gold, resolusi topik ILIKE + fallback centroid vector bergate similaritas ≥ 0.50, publikasi pendukung) dengan batasan relasional — bukan satu kueri gabungan tunggal (keputusan desain: lebih aman dan lebih sederhana; dinormalisasi via `EvidenceUnifier.from_hybrid`).
    - Daftar putih (whitelist) operator ditegakkan di layer Pydantic (`YearOp` Literal `eq|gt|gte|lt|lte|between` + `FilterParams` bound) sehingga string operator tidak pernah mencapai SQL.
  - Lengkapi orkestrasi QueryRouter 4-rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`) dengan prioritas deterministik Graph > Hybrid > SQL > Vector fallback (kolaborasi menang atas kata agregat — disengaja, dikunci via `test_router_multi_intent_graph_wins_over_sql_counting`).
  - Implementasikan `AnswerSynthesizer` dengan LLM Lokal (Qwen2.5-Coder-7B via Ollama) sebagai **opt-in** (`llm_synthesis: true` pada `AskRequest`; default deterministik):
    - Prompt sistem menegakkan grounding ketat: jawab eksklusif dari bukti yang diberikan.
    - Isolasi konteks eksplisit: `SYSTEM INSTRUCTIONS ≠ USER QUESTION ≠ RETRIEVED EVIDENCE`.
    - Wajibkan sitasi inline memakai format `[Title, Year, DOI]` atau `[Title, Year, no-doi]`.
    - Setiap kegagalan LLM (timeout `OLLAMA_TIMEOUT_S=8s`, model hilang, non-200, respons kosong, atau output yang habis dipangkas verifier) jatuh ke renderer deterministik dengan flag `synthesis_backend: deterministic-fallback` — request tidak pernah gagal karena sintesis.
  - Implementasikan `CitationVerifier` (Verifikasi Post-Hoc):
    - Ekstrak seluruh sitasi yang dibuat dari output LLM.
    - Cocokkan sitasi dengan `EvidenceSet` yang diberikan ke LLM.
    - Pangkas sitasi tak-terverifikasi/terhalusinasi dan catat pada `unverified_citations`.
  - Implementasikan penanganan nol-bukti deterministik: jika `EvidenceSet` kosong, segera kembalikan `status: not_found` tanpa memanggil LLM sintesis (kosakata `insufficient_evidence` dipetakan ke `not_found`; tidak ada varian status kelima).
- **Output:** Pipeline retrieval dan pembuatan 4-rute lengkap yang menghasilkan jawaban terverifikasi dan ter-grounding.
- **Memblokir:** Fase 8 (Gerbang MVP & Verifikasi).
- **Kriteria Penerimaan:**
  - Kueri hybrid "Papers on inflammation by Indonesian institutions after 2020" mengeksekusi pencarian vector yang dibatasi filter terstruktur, mengembalikan jawaban ter-grounding dengan sitasi terverifikasi.
  - Kueri tanpa dukungan database mengembalikan `status: not_found` dalam < 200ms dengan nol pemanggilan pembuatan LLM.
  - Instruksi tersuntik di dalam abstrak hasil retrieval diperlakukan sebagai teks tak-tepercaya dan tidak mengubah perilaku sintesiser.

---

### Fase 8 — Verifikasi MVP End-to-End, Suite Uji & Baseline Latensi
**Status:** `[IN PROGRESS]` (Gerbang Penyelesaian MVP)  
**Tujuan:** Eksekusi verifikasi formal di seluruh sistem pada dataset prototipe, validasi seluruh kebutuhan fungsional dan keamanan, dan tetapkan baseline latensi empiris.

- **Prasyarat:** Fase 7 — **terpenuhi**.
- **Rencana Eksekusi 7 Workstream (normatif; rinci di `reports/fase8_execution_plan.md`):**
  - **A — Baseline tooling (blocking):** pasang `pytest-cov` (ditunda eksplisit ke Fase 8 di `requirements.txt:53`), konfigurasi coverage gate ≥80% pada `router`/`retrievers`/`sql_security`/`synthesizer`, dan `.gitignore` untuk artefak `frontend/coverage/` serta `.coverage`.
  - **B — Verification run:** jalankan ulang `pytest tests/unit tests/integration`, `E2E_LIVE=1 pytest tests/e2e`, dan `pytest --cov=backend/app --cov-report=term-missing`; tutup gap dengan unit test terarah; tutup sisa `docs/09` TBD-5 (parity check distribusi embedding fallback vs gate 0.48).
  - **C — R2a: kejar `<200ms` nol-bukti:** cold path `VectorRoute` 246ms dengan 193ms `model.encode` bge-m3 CPU. Eksekusi **R2a.1** (tuning encoder: `torch.inference_mode()` + `set_num_threads`, dikunci parity test cosine ≈1.0). Bila masih gagal → **checkpoint owner** sebelum R2a.2 (re-scope `AC-RAG-4` per-route). **R2a.3** (pre-probe leksikal sebelum embedding) **ditolak di Fase 8** karena korpus prototipe hanya 40 chunk sehingga gate leksikal tidak dapat divalidasi; item FTS Hybrid di §5 tetap Fase 9.
  - **D — Baseline latensi formal:** catat spek environment, breakdown per-route dari `debug` (`validation_ms`…`total_ms`, warm dan cold), tulis `reports/fase8_latency_baseline.md` versus NFR1 (`<15 detik`).
  - **E — Task 11 Frontend:** audit `frontend/components/Workspace/` terhadap `AC-UI-1..7` (`docs/07` §4–§6), tutup gap, `npm run verify` + `npm run build`, smoke live 7 status, lalu commit bercabang.
  - **F — Checklist AC:** `AC-DB-1..9` (`docs/04:471`), `AC-RAG-1..5` (`docs/05:312`), `AC-API-1..4` (`docs/06:280`), `AC-PIPE-1..6` (`docs/12:240`), `AC-UI-1..7` (`docs/07:144`). **Aturan:** dicentang hanya dengan pointer bukti konkret; AC gagal dicentang dengan catatan deviasi tertulis, bukan senyap.
  - **G — Rekonsiliasi dokumen + sign-off:** tutup kontradiksi internal `docs/10` §0 dan status stale Fase 4/5/6 di `docs/11`; **sync `docs/01` + `docs/02` masuk scope Fase 8**; tulis `reports/fase8_signoff.md`; bump versi dokumen.
- **Keputusan owner yang dipatok (2026-10-03):**
  - **R1** — migrasi `.env DB_URL` dari role `postgres` ke `app_readonly` **tidak dieksekusi agent**; hanya prosedur + verification step di laporan sign-off sebagai tindakan pemilik. Guard primer tetap AST whitelist + templat terparameterisasi.
  - **R2a** — target `<200ms` nol-bukti **dikejar**, bukan lagi deviasi diterima (lihat workstream C).
  - **R2b** — sintesis LLM ±14,7 detik CPU vs NFR 5–10 detik tetap **deviasi diterima** (memerlukan GPU → Fase 10), dicatat di baseline, bukan gate.
- **Output:** laporan suite uji + coverage, `reports/fase8_latency_baseline.md`, `reports/fase8_signoff.md`, dan 24 item checklist AC terverifikasi dengan pointer bukti.
- **Memblokir:** Fase 9 (Kualitas & Skala Pasca-MVP).
- **Kriteria Penerimaan:**
  - 100% uji guardrail keamanan lolos.
  - `coverage ≥80%` pada router, retriever, dan security gate.
  - Nol sitasi terhalusinasi mencapai respons API final di seluruh pertanyaan benchmark.
  - `not_found` nol-bukti mengukur `<200ms` (R2a) atau menyertakan catatan deviasi tertulis yang disetujui owner.
  - `AC-UI-1..7` terverifikasi terhadap implementasi on-disk.
  - **Persetujuan (Sign-Off) Gerbang MVP Tercapai.**

---

### Fase 9 — Kualitas Retrieval, Resolusi Entitas Semantik, Apache AGE & Harness Eval Otomatis
**Status:** `[POST-MVP]`  
**Tujuan:** Menghapus error agregasi resolusi entitas, menyetel (tune) ambang retrieval terhadap data nyata, mengevaluasi **Apache AGE** sebagai ekstensi graf native PostgreSQL, dan menggelar evaluasi berkelanjutan otomatis.

---

### Fase 10 — Optimasi Kinerja, Worker Async, Streaming & Caching
**Status:** `[POST-MVP]` / `[FUTURE]`  
**Tujuan:** Mengatasi bottleneck latensi yang terukur empiris melalui worker tugas latar (background), caching kueri, respons streaming (`/api/v1/ask/stream`), dan akselerasi inferensi GPU.

---

### Fase 11 — Pengerasan Produksi, Keamanan Multi-Pengguna & Ingestion Data Berkelanjutan
**Status:** `[FUTURE]`  
**Tujuan:** Menskalakan prototipe menjadi platform kelas produksi multi-tenant dengan sinkronisasi data otomatis untuk arsip Scopus masif (>100K publikasi) dan keamanan enterprise.

---

## 5. Definisi Batas MVP

| Komponen / Kapabilitas | Cakupan MVP (Fase 0–8) | Cakupan Pasca-MVP (Fase 9–10) | Produksi Masa Depan (Fase 11) |
|---|---|---|---|
| **Antarmuka API** | `POST /api/v1/ask`, `GET /api/v1/health` | `POST /api/v1/ask/stream` (SSE), API Resource | Auth Multi-tenant, OAuth2/JWT |
| **Penanganan Request** | Sinkron, validasi Pydantic, `request_id` | Respons ter-cache | Worker async Celery/Redis |
| **Routing Kueri** | Deterministik / Berbasis Aturan (fallback rute VectorRoute/HybridRoute, bukan fallback klasifier LLM) | Penyetelan (tuning) intent via harness eval | Routing berbasis reinforcement-learning |
| **Pencarian Terstruktur** | `SqlRetriever`, whitelist AST, 9 tabel kanonikal | Estimator biaya kueri | Eksekusi proxy ter-sandbox |
| **Pencarian Semantik** | `VectorRetriever` (`bge-m3`, 1024d, HNSW pada `chunks`, $\ge 0.48$) | FTS Hybrid (`tsvector`), penyetelan ambang | Chunking multi-vector dinamis |
| **Knowledge Graph** | Tabel edge (`institution`, `author`), maks 3 hop | Ekstensi Graf (Apache AGE), resolusi edge `CITES` | Graf jaringan sitasi penuh |
| **Lapisan Bukti** | `EvidenceSet` ternormalisasi, peringkat deterministik | Regresi eval emas (golden) | Cross-encoder (`bge-reranker-large`) |
| **Sintesis & Sitasi** | Prompt ter-grounding §6 docs/05, `[Title, Year, DOI/no-doi]`, pemeriksaan post-hoc; LLM opt-in (`llm_synthesis`) dengan fallback deterministik | Skor confidence sitasi | Chat multi-turn interaktif |
| **Nol Hasil** | `not_found` deterministik, nol pemanggilan LLM | Saran disambiguasi | Relaksasi kueri otomatis |
| **Komputasi / Serving** | Khusus CPU (Ollama Lokal, Qwen2.5-Coder-7B) | Instance GPU, Qwen2.5-Coder-32B | Klaster model auto-scaling |
| **Ingestion Data** | Dataset prototipe (validasi alur E2E) — load + pembersihan (cleaning) + export DONE; indexing vector NEXT (Task 1) | Ingestion batch semi-otomatis | Pipeline CDC / Outbox berkelanjutan (>100K) |
| **Kontrol Akses** | Internal-saja, pembatasan laju (rate-limiting) IP, DB read-only | Pembatasan laju per-pengguna | Auth Supabase, RLS, log audit |

---

## 6. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status |
|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED |
| **Vector Storage** | `pgvector` HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) pada `chunks.embedding vector(1024)` (DONE, Task 1) | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Konvensi penamaan** | 9 tabel relasional kanonikal standar: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` | `01`, `02`, `03`, `04`, `05`, `06`, `10`, `11`, `12` | ALIGNED |
| **Pembersihan data (cleaning)** | Bronze → Silver via script Python — **DONE** (hasil pembersihan ter-export di `data/*_cleaned.csv`, 9 file; sudah ter-load di 9 tabel Silver) | `01`, `04`, `10`, `12` | ALIGNED |
| **Normalisasi lowercase** | Naratif & kategorikal (`abstract`, `keyword`, `country`, dll.) disimpan full lowercase; tampilan & ID asli dipertahankan; kolom `*_normalized` (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`) disimpan lowercase+trim+strip-punct untuk agregasi/pencarian | `01`, `02`, `04`, `05`, `12` | ALIGNED |
| **Chunking** | Granularitas abstrak per publikasi pada tabel `chunks`, field `chunk_text`, `section = 'title_abstract'` | `03`, `04`, `05`, `12` | ALIGNED |
| **Embedding** | `BAAI/bge-m3` (1024-dim, Float32) via `sentence-transformers`, batch 32–64, CPU-optimized, input `Title: {title}\nAbstract: {abstract}` (DONE, Task 1) | `01`, `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Retrieval** | Dynamic 4-Route: `SQLRoute` (Silver), `VectorRoute` (`chunks.embedding`), `GraphRoute` (Derived Edge T1–T4), `HybridRoute` (Gold Analytics + Silver) | `01`, `02`, `03`, `05`, `06`, `10`, `11` | ALIGNED |
| **Vector Similarity Gate** | Cosine similarity threshold $\ge 0.48$ untuk model `BAAI/bge-m3`, dipilih dari benchmark berlabel 94 kueri (P1 recalibration dari 0.65); kueri di bawah ambang → short-circuit ke `status: not_found` (**PROTOTIPE-KALIBRASI**: validasi ulang setelah ingestion skala produksi) | `02`, `03`, `05`, `06` | ALIGNED |
| **Format sitasi** | Standar deterministik 3-elemen: `[Judul, Tahun, DOI]` jika ada DOI, dan `[Judul, Tahun, no-doi]` jika naskah tanpa DOI | `01`, `05`, `06`, `07` | ALIGNED |
| **Graph Engine Strategy** | MVP dikunci menggunakan parameterized PostgreSQL Recursive CTE (T1–T4); evaluasi pasca-MVP menggunakan Apache AGE pada Fase 9 | `03`, `04`, `09`, `11` | ALIGNED |
| **Konteks RAG** | Pembingkaian `UNTRUSTED DATA`, LLM murni menyintesis narasi & memvalidasi `EvidenceObject`, short-circuit deterministik pada 0 bukti, `CitationVerifier` post-hoc | `02`, `03`, `05`, `06`, `07`, `08` | ALIGNED |
| **Kontrak API** | `POST /api/v1/ask` (`AskRequest` & `AskResponse` dengan `evidence_objects`) + `GET /api/v1/health`. Endpoint `/api/query` resmi SUPERSEDED | `02`, `03`, `05`, `06`, `07`, `10`, `11` | ALIGNED |
| **Dataset prototipe** | Dataset prototipe kecil (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) untuk validasi end-to-end lengkap | `01`, `02`, `03`, `04`, `10`, `11`, `12` | ALIGNED |
| **Dataset skala produksi** | Target masa depan untuk ingestion Scopus skala besar (>100K publikasi) dengan pipeline batch otomatis, deduplikasi multi-tier, dan worker async | `01`, `02`, `03`, `04`, `11`, `12` | ALIGNED |

---

## 7. Keputusan Arsitektur Kanonikal

1. **No-DOI Citation Decision:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Cosine Similarity Threshold Decision (`VectorRoute`):**
   - *Keputusan:* Nilai cosine similarity threshold dikunci pada $\ge 0.48$ untuk model `BAAI/bge-m3` (recalibrated P1 dari 0.65 berdasarkan benchmark berlabel; `reports/retrieval_calibration.md`). Kueri dengan nilai $< 0.48$ langsung diarahkan ke `status: not_found`.
3. **Post-MVP Graph Engine Decision:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 8. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/11 Roadmap.md` v3.8.0 | Fase 8 dari `[PLANNED]` ke `[IN PROGRESS]`: cakupan 7 workstream A–G (coverage gate, verification run, R2a gate `<200ms` nol-bukti, baseline latensi, audit Task 11 `AC-UI-1..7`, pencentangan 24 AC, rekonsiliasi dokumen); tetapkan 3 keputusan owner (R1 didokumentasikan saja, R2a dikejar, sync `docs/01`+`docs/02` masuk scope); perbaiki label keliru "Fase 8 / Fase 11" menjadi Task 11 | Gate Fase 7 dibuka (`READY WITH CONDITIONS`, `reports/fase7_closeout.md`); Task 12 sudah 14/14 sehingga Fase 8 berisi formalisasi, bukan fitur baru. Rencana rinci di `reports/fase8_execution_plan.md`. Catatan: baris riwayat v3.7.x belum pernah ditulis di dokumen ini |
| `docs/11 Roadmap.md` v3.7.0 | **P1 recalibration:** ambang `VectorRoute` 0.65 $\rightarrow$ 0.48 dari benchmark berlabel 94 kueri + harness sweep (`scripts/bench_retrieval.py`) + regression gate (12 pemeriksaan). Q04/Q05 E2E dibalik dari `not_found` ke bukti (keduanya match valid). PROTOTIPE-KALIBRASI | `reports/retrieval_calibration.md` |
| `docs/11 Roadmap.md` v3.6.2 | Aturan bahasa: narasi Indonesia, teknis Inggris (`Vertical Slice`, `Source-of-Truth Invariant`, `Evidence Normalization Invariant`, `Zero-Hallucination Invariant`, `Vector Similarity Gate`, dll) | Tanpa duplikasi bilingual; perbaiki terjemahan literal yang aneh |
| `docs/11 Roadmap.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/11 Roadmap.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; menambah Progress Tracker DONE/NEXT/PENDING; menandai vector storage sebagai PENDING eksplisit; memberi status-tag pada blueprint offline pipeline | Sinkronisasi progress aktual 2026-09-27 |
| `docs/11 Roadmap.md` v3.4.0 | Menyelaraskan seluruh fase implementasi (Phase 0–8) dengan 9 tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/11 Roadmap.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan penunjukan Apache AGE untuk Phase 9 | Menutup open decisions menjadi keputusan kanonikal |
| `docs/11 Roadmap.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumen di seluruh repository |
