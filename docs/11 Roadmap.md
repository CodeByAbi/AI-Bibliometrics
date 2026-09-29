# Roadmap Teknis — Prototipe Scopus menuju Riset Intelijen

**Versi Dokumen:** 3.6.0 (Roadmap Komprehensif Selaras Arsitektur)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `11 Roadmap.md` Draft v2 s.d. v3.5.0  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  

---

## 1. Ringkasan Eksekutif & Penilaian Status Proyek

### 1.1 Posisi Saat Ini (Realitas Audit Repositori)
Inspeksi repositori per **2026-09-28** menetapkan status tersinkronisasi berikut:
- **DONE — Database:** Database PostgreSQL **sudah dibuat dan siap pakai**, memuat **dataset prototipe kecil** (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) yang disiapkan khusus untuk validasi end-to-end. Kredensial telah diamankan secara internal.
- **DONE — Pembersihan & Export:** Data Scopus **sudah melalui pembersihan (cleaning) dan berhasil di-export** sebagai 9 file `data/*_cleaned.csv`.
- **DONE — Penyimpanan Vector & Indexing (Fase 1):** Kolom `chunks.embedding` **sudah dibuat dan terisi 100% (40/40 chunk)** dengan representasi 1024-dimensi `BAAI/bge-m3`. Indeks HNSW (`idx_chunks_embedding_hnsw`) dan `idx_chunks_pub_id` sudah aktif dan terverifikasi.
- **DONE — Materialisasi Tabel Edge (Fase 1):** Tabel edge turunan `institution_collaboration` (254 edge) dan `author_collaboration` (484 edge) **sudah dimaterialisasi secara idempoten** dan diverifikasi (`CHECK (a < b)`, `via_publication_ids` valid).
- **DONE — Kerangka Gateway API & DB Pool (Fase 2):** Backend FastAPI (`backend/app/`), pool async `asyncpg`, endpoint `GET /api/v1/health`, kontrak `POST /api/v1/ask`, middleware `X-Request-ID`, rate limiting, logging terstruktur, dan client Ollama terverifikasi.
- **IN PROGRESS — Fase 3:** Irisan Vertikal QueryRouter & Retrieval Terstruktur / SQL (`QuestionRouter` & `SqlRetriever` tervalidasi `sqlglot`) — implementasi dan uji hijau di `develop`, sign-off E2E menyusul Task 12.
- **PENDING — Pasca-Fase 3:** Retrieval Semantik (Fase 4), Evidence Layer (Fase 5), Mesin Graf & Analitik Gold (Fase 6), Sintesis Jawaban & E2E (Fase 7-8).

### 1.1b Pelacak Progres (Sinkronisasi 2026-09-28)

| Status | Item |
|---|---|
| DONE | Database PostgreSQL · Dataset prototipe (9 tabel kanonikal) · Pembersihan data (cleaning) · Export data bersih (`data/*_cleaned.csv`) · Task 0 (Audit Skema) · Task 1 (Batch Embedding & Indeks HNSW) · Task 8 (Materialisasi Edge Graf) · Task 2 (FastAPI Framework & DB Pool) · Task 3 (Ollama Setup & Health) |
| IN PROGRESS (Fase 3) | QueryRouter (Task 4) · EntityResolutionGate · SqlRetriever & AST Validator (Task 5) — implementasi + uji hijau di `develop` · Vertical Slice sign-off (Task 12) |
| PENDING (Fase 4+) | VectorRetriever (Task 6) · Evidence Layer (Task 7) · Synthesizer & CitationVerifier (Task 9) · E2E 12 Queries (Task 12) |
### 1.2 Apa yang Harus Dibangun Terlebih Dahulu?
Pengembangan tidak boleh dimulai dari UI atau orkestrasi kompleks. Urutan prasyarat absolut adalah:
1. **Baseline Repositori & Infrastruktur (Fase 0)**: Bangun struktur direktori proyek, Docker Compose (FastAPI + Ollama), kontrak environment, dan jalankan skrip verifikasi skema database Task 0 terhadap 9 tabel kanonikal yang sudah ada.
2. **Fondasi Data Offline & Indexing (Fase 1)**: Sediakan (provision) `chunks.embedding vector(1024)` pgvector, eksekusi pipeline batch embedding (`BAAI/bge-m3`), bangun indeks HNSW, dan materialisasi relasi graf kanonikal.
3. **Inti API & Orkestrasi (Fase 2)**: Kerangka aplikasi FastAPI, rute dasar `/api/v1`, validasi input Pydantic, pembuatan `request_id`, pencatatan log terstruktur, dan pooling database read-only.
4. **Irisan Vertikal Terstruktur (Fase 3)**: Buktikan rantai end-to-end terlebih dahulu pada kueri nyata menggunakan routing deterministik dan Text-to-SQL tervalidasi pada tabel kanonikal.

### 1.3 Apa Definisi MVP?
**MVP** didefinisikan secara ketat sebagai keberhasilan eksekusi alur kerja tanya-jawab end-to-end yang beroperasi di atas **data nyata**, mengembalikan **jawaban ter-grounding** dengan **sitasi terverifikasi**:
- **Backend FastAPI** dengan endpoint berversi (`POST /api/v1/ask`, `GET /api/v1/health`).
- **Validasi Batas Ketat** (validasi skema Pydantic, penelusuran `request_id`, kategorisasi error).
- **QueryRouter Multi-Rute** yang memprioritaskan aturan pola deterministik dan ekstraksi entitas (fallback router LLM).
- **Empat Retriever Inti**:
  - `SqlRetriever` (tervalidasi AST pada 9 tabel kanonikal, peran read-only, pemeriksaan agregasi eksak, `LIMIT 50`).
  - `VectorRetriever` (`bge-m3` 1024d, similaritas kosinus HNSW pgvector, `DISTINCT ON (p.publication_id)`, ambang $\ge 0.65$).
  - `GraphRetriever` (tabel edge turunan `institution_collaboration` dan `author_collaboration`; traversal terbatas maks 3 hop; pelacakan provenance).
  - `HybridRetriever` (Lapisan Gold `topics`, `topic_evolution`, `researcher_expertise` + Silver & `chunks`).
- **Lapisan Bukti (Evidence Layer)**: skema `Evidence`, `EvidenceSet`, dan `EvidenceUnifier` yang memastikan tidak ada baris/chunk mentah yang melewati normalisasi.
- **Peringkat Bukti Deterministik**: skoring eksplisit berbasis relevansi dan provenance.
- **Sintesiser Jawaban**: pembuatan (generation) ter-grounding hanya dari bukti terverifikasi, sitasi `[Title, Year, DOI]` / `[Title, Year, no-doi]`, penanganan `not_found` / `insufficient_evidence` deterministik, dan verifikasi sitasi post-hoc.
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
    [NEXT — Task 1a]                [PENDING — Task 8]
           ↓                               ↓
    Embedding (BAAI/bge-m3)         Materialisasi Tabel Edge
    [PENDING — Task 1b]             [PENDING — Task 8]
           ↓                               ↓
    pgvector (chunks.embedding)     institution/author_collab
    [PENGISIAN PENDING — Task 1c]   [Indeks Relasi Turunan]
    [Indeks Semantik Turunan]        [PENDING]


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
       ├── Graf (GraphRetriever)      ──> Tabel Edge Turunan T1-T4
       └── Hybrid (HybridRetriever)    ──> Analitik Gold + Silver
               ↓                   ↓                 ↓
       ┌───────────────────────────────────────────────┐
       │          Lapisan Normalisasi Bukti            │
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
1. **Invariant Sumber Kebenaran (Source of Truth Invariant)**: PostgreSQL Silver adalah satu-satunya sumber kebenaran kanonikal. pgvector, tabel edge turunan, dan analitik Gold adalah struktur turunan read-only.
2. **Invariant Normalisasi Bukti (Evidence Normalization Invariant)**: Tidak ada baris database, chunk vector, atau edge graf mentah yang boleh diteruskan langsung ke LLM. Semua data retrieval WAJIB melewati `EvidenceUnifier` menjadi `EvidenceSet` ternormalisasi.
3. **Invariant Keamanan (Security Invariant)**: Koneksi database WAJIB menggunakan peran `app_readonly` dengan `SET search_path = public` dan `statement_timeout = 10s`. Teks hasil retrieval adalah **DATA TIDAK TERPERCAYA** dan tidak dapat mengesampingkan instruksi sistem.
4. **Invariant Tanpa Halusinasi (Zero-Hallucination Invariant)**: Jika retrieval mengembalikan 0 item bukti, sistem WAJIB mengembalikan `status: not_found` / `insufficient_evidence` secara deterministik tanpa mengeksekusi pemanggilan sintesis LLM.

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
Fase 0 ──> Fase 1 ──> Fase 2 ──> Fase 3 (Irisan Vertikal) ──> Fase 4
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
  - Buat tata letak direktori: `backend/app/`, `database/`, `scripts/`, `tests/`, `docker/`.
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

### Fase 3 — Irisan Vertikal QueryRouter & Retrieval Terstruktur / SQL
**Status:** `[CURRENT]`  
**Tujuan:** Membuktikan irisan vertikal pertama sistem yang berjalan: rute pertanyaan masuk, buat dan validasi SQL read-only, eksekusi terhadap data nyata di 9 tabel kanonikal, dan kembalikan jawaban ter-grounding.

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
      - Tegakkan daftar putih (whitelist) nama tabel dan kolom.
      - Tegakkan pemeriksaan bentuk agregat (pertanyaan bermaksud agregat wajib memakai `COUNT`, `SUM`, atau `GROUP BY`).
      - Tegakkan pencegahan hitung ganda: `COUNT(DISTINCT publication_id)` pada join junction.
      - Tegakkan `LIMIT 50` non-agregat.
  - Implementasikan irisan minimal `AnswerSynthesizer`: format baris SQL menjadi teks tabular/berpoin tanpa halusinasi.
- **Output:** Irisan vertikal fungsional untuk pertanyaan terstruktur (misal: "Siapa 5 penulis paling produktif tahun 2023?").
- **Memblokir:** Fase 5 (Lapisan Bukti), Fase 7 (Retrieval Hybrid).
- **Kriteria Penerimaan:**
  - Pertanyaan pengguna nyata `Who are the top 5 authors in 2023?` mengenai `POST /api/v1/ask`, terute ke `structured`, mengeksekusi SQL terverifikasi, dan mengembalikan hitungan akurat yang terverifikasi terhadap kueri DB langsung.
  - Input destruktif ("DROP TABLE publications") ditolak validator AST dengan HTTP 422 sebelum mencapai database.

---

### Fase 4 — Mesin Retrieval Semantik / Vector
**Status:** `[PLANNED]`  
**Tujuan:** Menghadirkan pencarian dokumen berbasis kemiripan pada abstrak publikasi riset dengan deduplikasi ketat dan penegakan ambang ($\ge 0.65$).

- **Prasyarat:** Fase 1 (Embedding Terisi & Terindeks) dan Fase 2 (Basis FastAPI).
- **Cakupan & Deliverable:**
  - Implementasikan klien embedding online memakai `BAAI/bge-m3` (sentence-transformers lokal atau endpoint embedding Ollama).
  - Implementasikan `VectorRetriever`:
    - Embed pertanyaan pengguna menjadi vector float 1024-dimensi.
    - Eksekusi kueri jarak kosinus (`<=>`) terhadap `chunks`.
    - Tegakkan deduplikasi: `DISTINCT ON (p.publication_id)` sebelum menerapkan `LIMIT 8`.
    - Gabung (join) dengan tabel `publications` untuk mengambil metadata kanonikal (`title`, `year`, `doi`, `eid`).
  - Implementasikan gerbang ambang skor similaritas: saring chunk di bawah ambang dasar similaritas ($\ge 0.65$).
  - Tangani skenario nol-kecocokan: kembalikan daftar kosong segera jika tidak ada chunk lolos ambang.
- **Output:** `VectorRetriever` teruji yang mengembalikan chunk publikasi topikal terdedup menurut naskah (paper).
- **Memblokir:** Fase 5 (Lapisan Bukti), Fase 7 (Retrieval Hybrid).
- **Kriteria Penerimaan:**
  - Pertanyaan "papers about oxidative stress in Wharton's jelly" mengembalikan 8 publikasi topikal teratas dengan DOI dan abstrak valid.
  - Tidak ada satu publikasi pun muncul berulang dalam daftar chunk yang dikembalikan.
  - Kueri yang sama sekali tak-terkait ("how to bake bread") mengembalikan nol hasil tanpa memicu error database.

---

### Fase 5 — Unifikasi Lapisan Bukti & Peringkat Deterministik
**Status:** `[PLANNED]`  
**Tujuan:** Menegakkan kontrak bukti perantara kanonikal dan unifier, memisahkan (decouple) sumber retrieval dari sintesis jawaban.

- **Prasyarat:** Fase 3 (`SqlRetriever`) dan Fase 4 (`VectorRetriever`).
- **Cakupan & Deliverable:**
  - Definisikan model Pydantic kanonikal `Evidence` dan `EvidenceSet`:
    - Field wajib: `source_id`, `source_type` (`sql` | `vector` | `graph` | `analytics`), `snippet`, `score`.
    - Metadata sumber opsional: `publication_id`, `title`, `authors`, `year`, `doi`, `provenance_ids`.
  - Implementasikan `EvidenceUnifier`:
    - Telan (ingest) output heterogen dari baris SQL, chunk vector, edge graf, dan analitik Gold.
    - Transformasi dan normalisasi seluruh item menjadi struktur `Evidence` standar.
    - Deduplikasi publikasi yang tumpang tindih antar metode retrieval berbeda.
  - Implementasikan `EvidenceRanker`:
    - Algoritma peringkat deterministik yang menggabungkan skor kosinus vector, kecocokan kata kunci, dan kebaruan publikasi.
    - Isolasi eksplisit peringkat deterministik saat ini dari reranker cross-encoder masa depan.
- **Output:** Lapisan data `EvidenceSet` terpadu yang menjamin penyajian standar ke model pembuatan (generation).
- **Memblokir:** Fase 6 (Integrasi Graf), Fase 7 (Sintesis Jawaban), Fase 8 (Verifikasi E2E).
- **Kriteria Penerimaan:**
  - Hasil retrieval dari multi-sumber ternormalisasi ke skema JSON `EvidenceSet` yang identik.
  - Unifikasi mendeduplikasi record `publication_id` identik sambil menggabungkan provenance sumbernya.

---

### Fase 6 — Konstruksi Knowledge Graph & Retrieval Graf (Wajib MVP)
**Status:** `[PLANNED]`  
**Tujuan:** Mengimplementasikan kapabilitas Knowledge Graph yang wajib, menyelesaikan relasi entitas akademik inti dengan traversal multi-hop terbatas dan pelacakan provenance.

- **Prasyarat:** Fase 1 (Materialisasi Data) dan Fase 5 (Lapisan Bukti).
- **Cakupan & Deliverable:**
  - Permukaan bawaan (default) MVP: tabel edge PostgreSQL termaterialisasi (`institution_collaboration`, `author_collaboration`) sebagai indeks graf relasional.
  - **Implementasi GraphRetriever**:
    - Eksekusi traversal terparameterisasi (Templat T1: Kolaborator Institusi, T2: Co-author, T3: pemetaan Topik-Institusi, T4: Pencarian Jalur Terbatas).
    - Rel traversal: `max_hops = 3` dan `LIMIT 50` ditegakkan ketat.
    - Retensi provenance: setiap relasi graf wajib mengembalikan `via_publication_ids`.
    - Normalisasi bukti graf: konversi jalur/edge graf menjadi objek `Evidence` kanonikal dengan `source_type: "graph"`.
- **Output:** `GraphRetriever` operasional yang mampu menjawab pertanyaan konektivitas relasional dengan provenance publikasi terverifikasi.
- **Memblokir:** Fase 7 (Retrieval Multi-Rute Hybrid), Fase 8 (Verifikasi E2E).
- **Kriteria Penerimaan:**
  - Kueri "Which institutions collaborated with AI researchers?" menelusuri `institution_collaboration` dan mengembalikan institusi tertaut disertai `via_publication_ids` konkret.
  - Traversal rekursif dijepit keras (hard-clamp) pada 3 hop; referensi sirkular berhenti (terminate) bersih tanpa pembengkakan memori atau timeout kueri.

---

### Fase 7 — Retrieval Hybrid Multi-Rute & Sintesis Jawaban Ter-grounding
**Status:** `[PLANNED]`  
**Tujuan:** Menyatukan keempat mesin retrieval di bawah QueryRouter dan menghadirkan sintesis bahasa natural ter-grounding dengan verifikasi sitasi otomatis dan perlindungan injeksi prompt.

- **Prasyarat:** Fase 3 (SQL), Fase 4 (Vector), Fase 5 (Bukti), Fase 6 (Graf).
- **Cakupan & Deliverable:**
  - Implementasikan `HybridRetriever`:
    - Satu kueri terparameterisasi yang menggabungkan analitik Gold (`topics`, `topic_evolution`, `researcher_expertise`) dan jarak vector semantik dengan batasan relasional.
    - Daftar putih (whitelist) operator (`eq`, `gt`, `gte`, `lt`, `lte`, `between`).
  - Lengkapi orkestrasi QueryRouter 4-rute (`SQLRoute`, `VectorRoute`, `GraphRoute`, `HybridRoute`).
  - Implementasikan `AnswerSynthesizer` dengan LLM Lokal (Qwen2.5-Coder-7B via Ollama):
    - Prompt sistem menegakkan grounding ketat: jawab eksklusif dari bukti yang diberikan.
    - Isolasi konteks eksplisit: `SYSTEM INSTRUCTIONS ≠ USER QUESTION ≠ RETRIEVED EVIDENCE`.
    - Wajibkan sitasi inline memakai format `[Title, Year, DOI]` atau `[Title, Year, no-doi]`.
  - Implementasikan `CitationVerifier` (Verifikasi Post-Hoc):
    - Ekstrak seluruh sitasi yang dibuat dari output LLM.
    - Cocokkan sitasi dengan `EvidenceSet` yang diberikan ke LLM.
    - Pangkas sitasi tak-terverifikasi/terhalusinasi dan catat pada `unverified_citations`.
  - Implementasikan penanganan nol-bukti deterministik: jika `EvidenceSet` kosong, segera kembalikan `status: not_found` / `insufficient_evidence` tanpa memanggil LLM sintesis.
- **Output:** Pipeline retrieval dan pembuatan 4-rute lengkap yang menghasilkan jawaban terverifikasi dan ter-grounding.
- **Memblokir:** Fase 8 (Gerbang MVP & Verifikasi).
- **Kriteria Penerimaan:**
  - Kueri hybrid "Papers on inflammation by Indonesian institutions after 2020" mengeksekusi pencarian vector yang dibatasi filter terstruktur, mengembalikan jawaban ter-grounding dengan sitasi terverifikasi.
  - Kueri tanpa dukungan database mengembalikan `status: not_found` dalam < 200ms dengan nol pemanggilan pembuatan LLM.
  - Instruksi tersuntik di dalam abstrak hasil retrieval diperlakukan sebagai teks tak-tepercaya dan tidak mengubah perilaku sintesiser.

---

### Fase 8 — Verifikasi MVP End-to-End, Suite Uji & Baseline Latensi
**Status:** `[PLANNED]` (Gerbang Penyelesaian MVP)  
**Tujuan:** Eksekusi verifikasi formal di seluruh sistem pada dataset prototipe, validasi seluruh kebutuhan fungsional dan keamanan, dan tetapkan baseline latensi empiris.

- **Prasyarat:** Penyelesaian Fase 7.
- **Cakupan & Deliverable:**
  - Implementasikan suite uji komprehensif di `tests/`:
    - Uji unit router (kueri terstruktur, semantik, graf, hybrid, ambigu).
    - Uji keamanan SQL (penolakan DROP, DELETE, tabel non-whitelist, agregat malformed).
    - Uji vector (ambang kosinus, deduplikasi).
    - Uji graf (penegakan batas hop, penanganan relasi sirkular).
    - Uji unifier bukti (akurasi normalisasi dan deduplikasi).
    - Uji sintesiser (verifier sitasi, resistensi injeksi prompt, penanganan nol-bukti).
    - Uji integrasi API (`200 OK`, `422 Unprocessable Entity`, `503 Service Unavailable`).
  - Eksekusi daftar periksa verifikasi end-to-end 12-kueri (Task 12).
  - Pengukuran Latensi empiris pada environment CPU target.
- **Output:** Laporan suite uji, fungsionalitas E2E terverifikasi pada dataset prototipe, audit baseline latensi empiris.
- **Memblokir:** Fase 9 (Kualitas & Skala Pasca-MVP).
- **Kriteria Penerimaan:**
  - 100% uji guardrail keamanan lolos.
  - Nol sitasi terhalusinasi mencapai respons API final di seluruh pertanyaan benchmark.
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
| **Routing Kueri** | Deterministik / Berbasis Aturan (fallback LLM) | Penyetelan (tuning) intent via harness eval | Routing berbasis reinforcement-learning |
| **Pencarian Terstruktur** | `SqlRetriever`, whitelist AST, 9 tabel kanonikal | Estimator biaya kueri | Eksekusi proxy ter-sandbox |
| **Pencarian Semantik** | `VectorRetriever` (`bge-m3`, 1024d, HNSW pada `chunks`, $\ge 0.65$) | FTS Hybrid (`tsvector`), penyetelan ambang | Chunking multi-vector dinamis |
| **Knowledge Graph** | Tabel edge (`institution`, `author`), maks 3 hop | Ekstensi Graf (Apache AGE), resolusi edge `CITES` | Graf jaringan sitasi penuh |
| **Lapisan Bukti** | `EvidenceSet` ternormalisasi, peringkat deterministik | Regresi eval emas (golden) | Cross-encoder (`bge-reranker-large`) |
| **Sintesis & Sitasi** | Prompt ter-grounding, `[Title, Year, DOI/no-doi]`, pemeriksaan post-hoc | Skor confidence sitasi | Chat multi-turn interaktif |
| **Nol Hasil** | `not_found` deterministik, nol pemanggilan LLM | Saran disambiguasi | Relaksasi kueri otomatis |
| **Komputasi / Serving** | Khusus CPU (Ollama Lokal, Qwen2.5-Coder-7B) | Instance GPU, Qwen2.5-Coder-32B | Klaster model auto-scaling |
| **Ingestion Data** | Dataset prototipe (validasi alur E2E) — load + pembersihan (cleaning) + export DONE; indexing vector NEXT (Task 1) | Ingestion batch semi-otomatis | Pipeline CDC / Outbox berkelanjutan (>100K) |
| **Kontrol Akses** | Internal-saja, pembatasan laju (rate-limiting) IP, DB read-only | Pembatasan laju per-pengguna | Auth Supabase, RLS, log audit |

---

## 6. Matriks Konsistensi Keputusan (Lintas Dokumen)

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

## 7. Keputusan Arsitektur Kanonikal

1. **Keputusan Sitasi Tanpa DOI:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Keputusan Ambang Batas Kesamaan Kosinus (`VectorRoute`):**
   - *Keputusan:* Nilai ambang batas kesamaan kosinus dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri yang menghasilkan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Keputusan Mesin Graf Pasca-MVP:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 8. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/11 Roadmap.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/11 Roadmap.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; menambah Progress Tracker DONE/NEXT/PENDING; menandai vector storage sebagai PENDING eksplisit; memberi status-tag pada blueprint offline pipeline | Sinkronisasi progress aktual 2026-09-27 |
| `docs/11 Roadmap.md` v3.4.0 | Menyelaraskan seluruh fase implementasi (Phase 0–8) dengan 9 tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/11 Roadmap.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan penunjukan Apache AGE untuk Phase 9 | Menutup open decisions menjadi keputusan kanonikal |
| `docs/11 Roadmap.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumen di seluruh repository |
