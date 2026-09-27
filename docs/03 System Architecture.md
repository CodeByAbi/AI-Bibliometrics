# System Architecture — End to End

**Document Version:** 3.0.0 (Comprehensive Architecture-Aligned Specification)  
**Status Date:** 2026-09-27  
**Supersedes:** `03 System Architecture.md` Draft v2  
**Authoritative Context:** Aligned with `README.md` and `docs/00` through `docs/11`  

> **Status Implementasi (Verifikasi Repositori 2026-09-27):**  
> Repositori saat ini hanya berisi dokumentasi Markdown (`README.md` dan `docs/00–11`). Direktori `backend/`, `frontend/`, `database/`, `scripts/`, `docker/`, dan `tests/` belum ada. Seluruh arsitektur, modul, dan pipeline di bawah ini berstatus **PLANNED / NOT IMPLEMENTED** dan mendefinisikan target rekayasa sistem yang normatif, bukan klaim kemampuan operasional saat ini.

---

## 0. Target Vertical Slice & Core Architectural Invariants

### 0.1 Target End-to-End Pipeline
Arsitektur target mengalirkan pertanyaan pengguna secara linear dan deterministik dari antarmuka web hingga respons ter-grounding:

```text
User Question
      ↓
FastAPI Gateway (`POST /api/v1/ask`)
      ↓
Boundary Validation & Request ID Generation
      ↓
Intent / Query Router (Deterministic Rule-Based + Entity Resolution Gate)
      ↓
Retrieval Engine (Fan-Out Orchestration)
      ├── Structured (SqlRetriever)
      ├── Semantic (VectorRetriever)
      ├── Graph (GraphRetriever)
      └── Hybrid (HybridRetriever)
              ↓                 ↓               ↓
      ┌────────────────────────────────────────────────────────┐
      │         Evidence Normalization Layer                   │
      │        (Canonical Evidence & EvidenceSet)              │
      └────────────────────────────────────────────────────────┘
                                ↓
                        EvidenceUnifier
                                ↓
                        EvidenceRanker
                    (Deterministic Scoring)
                                ↓
                       AnswerSynthesizer
               (Prompt Isolation & Defense Rules)
                                ↓
                     CitationVerifier (Post-Hoc)
                                ↓
      Grounded Answer (`status: ok / not_found / needs_clarification`)
```

### 0.2 Lapisan Sistem, I/O, dan Tanggung Jawab

| Lapisan | Input | Output | Tanggung Jawab Utama |
|---|---|---|---|
| **FastAPI Gateway** | Raw HTTP JSON | Validated Request (`question`, `filters`) + `request_id` | Validasi skema Pydantic, injeksi `request_id` (UUIDv4), penanganan error global, pembatasan rate limit (06 §1). |
| **QueryRouter** | Validated Question + Filters | Route (`structured` \| `semantic` \| `graph` \| `hybrid`) + `EntityContract` | Klasifikasi berbasis aturan deterministik diutamakan; ekstraksi parameter slot; entity resolution gate (05 §2). |
| **RetrievalEngine** | Route + Typed Entities | Raw Rows / Chunks / Edges | Orkestrasi pengambilan data ke `SqlRetriever`, `VectorRetriever`, `GraphRetriever`, atau `HybridRetriever`. |
| **EvidenceUnifier** | Heterogeneous Raw Retrieval Outputs | `EvidenceSet` | Normalisasi seluruh baris SQL, chunk vector, dan edge graf ke skema kanonikal `Evidence`; deduplikasi publikasi (05 §7.0). |
| **EvidenceRanker** | `EvidenceSet` | Ranked `EvidenceSet` | Pemeringkatan bukti deterministik berbasis relevansi, kemiripan kosinus, dan bobot relasi untuk MVP (05 §7.0). |
| **AnswerSynthesizer** | Question + Ranked `EvidenceSet` | Synthesized Text + Inline Citations | Sintesis bahasa alami berbasis bukti eksklusif, isolasi instruksi vs data bukti, penanganan deterministik data kosong (05 §7.1). |
| **CitationVerifier** | Synthesized Text + Input `EvidenceSet` | Grounded Answer Payload | Parser sitasi post-hoc; validasi sitasi `[Judul, Tahun, DOI]` terhadap bukti; sitasi fiktif di-strip ke `unverified_citations` (05 §7.2). |

### 0.3 Invarian Arsitektur Wajib (Architectural Invariants)
1. **Source of Truth Invariant**: PostgreSQL adalah satu-satunya sumber kebenaran data kanonikal (*canonical source of truth*). Ekstensi `pgvector` dan Knowledge Graph adalah indeks turunan (*derived read-only indexes*) yang diekstrak dan disinkronkan dari PostgreSQL (04 §0).
2. **Evidence Normalization Invariant**: Tidak ada baris tabel mentah, chunk teks mentah, atau edge graf mentah yang boleh dikirim langsung ke LLM. Seluruh hasil retrieval WAJIB dinormalisasi melalui `EvidenceUnifier` menjadi objek terstruktur `EvidenceSet` sebelum masuk ke tahap sintesis.
3. **Security Invariant**: Akses database aplikasi wajib menggunakan role `app_readonly` dengan izin `SELECT` saja, `SET search_path = public` pada setiap inisialisasi koneksi, dan `statement_timeout = '10s'`. Seluruh teks publikasi yang ditarik diperlakukan sebagai **DATA TIDAK TERPERCAYA (UNTRUSTED DATA)** dan tidak boleh meng-override instruksi sistem.
4. **Zero-Hallucination Invariant**: Jika tahap retrieval menghasilkan 0 item bukti, sistem WAJIB mengembalikan `status: not_found` / `insufficient_evidence` secara deterministik dalam waktu < 200ms **tanpa mengeksekusi pemanggilan sintesis LLM**.

---

## 1. High-Level Component Topology

```
┌─────────────────────────────────┐           HTTPS            ┌──────────────────────────────────────────────┐
│        Next.js Frontend         │ ─────────────────────────> │             FastAPI Backend API              │
│   (Clean White, Dense Layout,   │ <───────────────────────── │       (Async Python Application Core)        │
│      Notion/Linear-Style)       │        JSON Response       └──────────────────────────────────────────────┘
└─────────────────────────────────┘                                                   │
                                                      ┌───────────────────────────────┼───────────────────────────────┐
                                                      ▼                               ▼                               ▼
                                              ┌───────────────┐               ┌───────────────┐               ┌───────────────┐
                                              │  QueryRouter  │               │ Local Ollama  │               │ Local Embed   │
                                              │(Deterministic │               │(Qwen2.5-Coder │               │ (BAAI/bge-m3, │
                                              │  Rules + Gate)│               │  7B-Instruct) │               │   1024 dims)  │
                                              └───────────────┘               └───────────────┘               └───────────────┘
                                                      │                               │                               │
                                                      └───────────────────────┬───────┴───────────────────────────────┘
                                                                              ▼
                                              ┌───────────────────────────────────────────────────────────────┐
                                              │               PostgreSQL Database (Supabase)                  │
                                              │  • 9 Canonical Relational Tables                              │
                                              │  • Derived Edge Tables (institution/author_collaboration)     │
                                              │  • pgvector Semantic Layer (HNSW Index on chunks.embedding)   │
                                              │  • Enforced Connection Role: app_readonly (SELECT only)       │
                                              └───────────────────────────────────────────────────────────────┘
```

Backend FastAPI bertindak sebagai orkestrator sentral tunggal. Frontend Next.js tidak memiliki akses langsung ke database atau ke model AI, dan tidak menyimpan kredensial apapun.

---

## 2. Komponen & Tanggung Jawab Sistem

### 2.1 Presentation Layer: Next.js Frontend
- **Karakter Desain**: Mengikuti prinsip *Clean White, Dense, Anti-Slop* dengan estetika Notion/Linear (07 §0). Memprioritaskan keterbacaan data teknis, tabel monospace, dan collapsible sources.
- **Komunikasi Backend**: Memanggil endpoint backend kanonikal `POST /api/v1/ask` melalui HTTP POST (kontrak v2 `/api/query` dinyatakan superseded).
- **Penanganan State Eksplisit**: Menyediakan visualisasi jujur untuk seluruh kemungkinan status backend: `ok`, `loading`, `not_found`, `needs_clarification`, dan `error`.
- **Zero-Logic Boundary**: Frontend tidak memproses Text-to-SQL, embedding, atau inferensi model; murni bertindak sebagai lapisan presentasi interaktif.

### 2.2 Backend Gateway & Orchestrator: FastAPI
Backend dibangun menggunakan Python 3.11+ dan FastAPI dengan pemisahan domain yang ketat:

```text
backend/app/
├── routers/         # Endpoint definitions (/api/v1/ask, /api/v1/health)
├── services/        # QueryRouter, Retrievers, EvidenceUnifier, Synthesizer
│   ├── router.py
│   ├── sql_retriever.py
│   ├── vector_retriever.py
│   ├── graph_retriever.py
│   ├── hybrid_retriever.py
│   ├── evidence.py
│   └── synthesizer.py
├── models/          # Pydantic schemas (Request, Response, Evidence, Entities)
├── db/              # Connection pooling, session guardrails, readonly lifecycle
└── core/            # Configuration, security checks, structured logger
```

#### Rincian Sub-komponen Backend:
1. **API Gateway & Boundary Validator**:
   - Memvalidasi skema payload masuk menggunakan Pydantic v2.
   - Menghasilkan dan menyuntikkan `request_id` (UUIDv4) ke konteks eksekusi, log terstruktur, dan header HTTP response (`X-Request-ID`).
   - Menerapkan rate limiting berbasis IP untuk proteksi compute CPU lokal.
2. **QueryRouter**:
   - Menentukan rute pertanyaan ke salah satu dari 4 jalur: `structured`, `semantic`, `graph`, atau `hybrid`.
   - **Strategi Routing**: Mengutamakan klasifikasi berbasis aturan deterministik (*deterministic pattern matching / intent rules*). Router berbasis LLM (*schema-light prompt*) bertindak sebagai opsi fallback jika aturan pola tidak cocok, mencegah penambahan latensi serial pada setiap request.
   - **Typed Entity Contract**: Mengekstrak parameter filter terstruktur (`YearFilter`, `country`, `author_name`, `institution_name`, `keyword`, `topic`) dengan validasi `Literal` ketat.
   - **Entity Resolution Gate**: Melakukan normalisasi entitas (`lower + trim` → exact match → fallback `ILIKE`). Jika 0 kandidat → `status: not_found`; jika > 1 kandidat ambigu → `status: needs_clarification`; jika 1 kandidat pasti → bind canonical ID. Field tanpa slot filter dikumpulkan ke `filters_ignored`.
3. **SqlRetriever (Structured Engine)**:
   - Menangani pertanyaan analitik/agregasi ("siapa 5 penulis paling produktif 2023?").
   - Menghasilkan SQL query read-only menggunakan prompt berskema 11-tabel kanonikal.
   - **Multi-Layer AST Validator (`sqlglot`)**:
     - Memastikan statement adalah `SELECT` murni (menolak keras statement manipulasi atau DDL).
     - Whitelist tabel dan kolom berdasarkan skema resmi `04 Database Schema.md`.
     - *Aggregate-Shape Check*: Pertanyaan agregat wajib memuat `COUNT`, `SUM`, `AVG`, atau `GROUP BY`.
     - *Double-Count Check*: Operasi `COUNT` yang melibatkan join tabel junction (`pub_author`, `pub_institution`) wajib menggunakan `COUNT(DISTINCT publication_id)`.
     - Enforce `LIMIT 50` untuk query non-agregat.
4. **VectorRetriever (Semantic Engine)**:
   - Menangani pertanyaan konseptual/eksploratif ("paper tentang stres oksidatif").
   - Meng-embed teks pertanyaan menggunakan model lokal `BAAI/bge-m3` (1024 dimensi).
   - Menjalankan pencarian kemiripan kosinus (`<=>`) pada `chunks.embedding`.
   - Enforce deduplikasi: `DISTINCT ON (p.publication_id)` sebelum klausul `LIMIT 8` untuk menjamin hasil berupa 8 publikasi unik, bukan pengulangan chunk dari paper yang sama.
   - Threshold gate: Memfilter chunk yang memiliki skor kemiripan di bawah batas minimum.
5. **GraphRetriever (Relational Engine)**:
   - Menangani pertanyaan jaringan kolaborasi dan konektivitas multi-hop ("institusi mana yang berkolaborasi dengan peneliti AI?").
   - Mengeksekusi templat traversal terparameterisasi (T1–T4) pada edge table `institution_collaboration` dan `author_collaboration`.
   - **Tidak menggunakan SQL buatan LLM** untuk mencegah risiko traversal rekursif tak terkendali.
   - Enforce batas traversal: `max_hops = 3`, `LIMIT 50`, dan statement timeout 10 detik.
   - Provenance tracking: Membawa array `via_publication_ids` pada setiap edge yang ditemukan.
6. **HybridRetriever (Multi-Modal Engine)**:
   - Menangani pertanyaan kombinasi ("paper tentang AI oleh institusi Indonesia setelah 2020").
   - Menjalankan query tunggal terparameterisasi yang memadukan vector distance search dengan filter relasional terindeks.
   - Operator filter dibatasi secara statis (`eq`, `gt`, `gte`, `lt`, `lte`, `between`).
7. **EvidenceUnifier**:
   - Bertindak sebagai lapisan normalisasi wajib (*mandatory evidence abstraction*).
   - Mentransformasi baris hasil SQL, chunk vector, dan edge relasional graf ke dalam skema seragam `Evidence` dan `EvidenceSet`.
   - Melakukan deduplikasi dan penggabungan metadata untuk publikasi yang muncul di lebih dari satu jalur retrieval.
8. **EvidenceRanker**:
   - Menerapkan algoritma pemeringkatan deterministik untuk mengurutkan bukti sebelum disajikan ke LLM.
   - Mengombinasikan skor kemiripan semantik, kecocokan kata kunci, dan kebaruan tahun publikasi (cross-encoder kompleks di-defer ke pasca-MVP).
9. **AnswerSynthesizer**:
   - Mengorkestrasi pembentukan jawaban berbasis LLM lokal (`Qwen2.5-Coder-7B-Instruct`).
   - Menerapkan prompt isolasi ketat: data hasil retrieval dibingkai sebagai bukti objektif, bukan instruksi yang dapat mengubah kepribadian/perilaku sistem.
   - Mewajibkan sitasi ilmiah eksplisit dengan format `[Judul, Tahun, DOI]`.
   - Jika `EvidenceSet` kosong, langsung menghasilkan respons deterministik tanpa pemanggilan LLM.
10. **CitationVerifier (Post-Hoc Verification)**:
    - Melakukan parsing sitasi dari teks jawaban yang dihasilkan LLM.
    - Mencocokkan setiap sitasi terhadap metadata dokumen yang ada di `EvidenceSet`.
    - Sitasi yang tidak cocok di-strip dari jawaban dan dicatat ke dalam array `unverified_citations`.
11. **DB Access Layer**:
    - Satu-satunya pintu gerbang akses backend ke PostgreSQL.
    - Menggunakan connection pool (asyncpg/psycopg3) dengan otentikasi role `app_readonly`.
    - Mengunci environment setiap koneksi dengan `SET search_path = public` dan `SET statement_timeout = '10s'`.

### 2.3 Model Layer: Local CPU Serving
- **LLM Generatif**: `Qwen2.5-Coder-7B-Instruct` (GGUF Q4_K_M) dijalankan secara lokal via **Ollama**. Dipilih karena performa terdepan di kelas 7B untuk Text-to-SQL dan format JSON terstruktur pada CPU biasa.
- **Embedding Model**: `BAAI/bge-m3` (1024 dimensi) dijalankan via local sentence-transformers atau Ollama embedding service. Mendukung multilingual (Inggris/Indonesia) untuk merepresentasikan abstrak dan judul Scopus.
- **Versi Model Terkunci**: Commit model embedding dan parameter kuantisasi LLM dikunci (*pinned*) untuk memastikan reproduktibilitas ranking retrieval.

### 2.4 Data Layer: PostgreSQL (Supabase) + pgvector
- **9 Tabel Relasional Sumber**: `publications`, `authors`, `institutions`, `keywords`, `funding`, `publication_references`, `chunks`, `pub_author`, `pub_institution`.
- **2 Edge Table Derivatif (Knowledge Graph Minimum Surface)**: `institution_collaboration` dan `author_collaboration` (materialized dari tabel junction dengan format kanonikal `a < b` dan array `via_publication_ids`).
- **Semantic Layer**: Kolom `chunks.embedding vector(1024)` dilengkapi index HNSW (`vector_cosine_ops`).
- **Keamanan Data**: Hak akses backend dibatasi secara fisik di level DBMS melalui role `app_readonly` (tanpa izin `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, atau `ALTER`).

---

## 3. Rationale Arsitektur: Backend Python Terpisah vs Next.js API Routes

Keputusan memisahkan backend Python (FastAPI) dari Next.js didasarkan pada pertimbangan rekayasa sistem yang konkret:
1. **Kematangan Ekosistem AI & Data**: Ekosistem manipulasi AST SQL (`sqlglot`), inferensi embedding lokal (`sentence-transformers`), pemrosesan numerik vektor, dan orkestrasi LLM jauh lebih teruji, stabil, dan efisien di Python dibandingkan runtime Node.js.
2. **Penolakan Subprocess Anti-Pattern**: Menjalankan skrip Python dari Next.js API Routes melalui `child_process.spawn()` adalah anti-pattern yang menimbulkan overhead spawning proses berulang, hilangnya connection pooling database, dan kerentanan konkurensi pada CPU terbatas.
3. **Isolasi Kegagalan & Independensi Komputasi**: Beban komputasi inferensi model dan query vektor diisolasi dalam container backend, sehingga lonjakan beban tidak mengganggu rendering frontend atau ketersediaan UI.
4. **Testability & Pipeline Reusability**: Backend Python dapat diuji secara mandiri menggunakan pytest, dievaluasi melalui skrip evaluasi otomatis (eval harness), atau diakses via antarmuka CLI tanpa memerlukan rendering web.

---

## 4. End-to-End Data Flows (Skenario Operasional)

### Flow 1: Structured / Analytic Query
*Pertanyaan:* `"Siapa 5 penulis paling produktif tahun 2023?"`
1. **UI**: Mengirimkan HTTP POST ke `/api/v1/ask`.
2. **FastAPI**: Memvalidasi request schema, men-generate `request_id`.
3. **QueryRouter**: Pola kata kunci mendeteksi intent agregasi/ranking → mengklasifikasikan ke rute `structured`, mengekstrak filter `{year_filter: {op: "eq", values: [2023]}}`.
4. **SqlRetriever**: Menyusun SQL:
   ```sql
   SELECT a.author_name, COUNT(DISTINCT pa.publication_id) AS total_pubs
   FROM authors a
   JOIN pub_author pa ON a.author_id = pa.author_id
   JOIN publications p ON pa.publication_id = p.publication_id
   WHERE p.year = 2023
   GROUP BY a.author_id, a.author_name
   ORDER BY total_pubs DESC
   LIMIT 5;
   ```
5. **AST Validator**: Memeriksa AST query (`sqlglot`): lolos whitelist tabel/kolom, memverifikasi `COUNT(DISTINCT)` pada join junction, memverifikasi ada klausul `GROUP BY`.
6. **DB Access**: Eksekusi ke PostgreSQL menggunakan role `app_readonly`.
7. **EvidenceUnifier**: Mengonversi 5 baris hasil menjadi objek `EvidenceSet` bertipe `source_type: "sql"`.
8. **AnswerSynthesizer**: Memformat hasil ke dalam tabel ringkas dan narasi grounded.
9. **CitationVerifier**: Memverifikasi tidak ada sitasi paper fiktif yang ditambahkan pada hasil agregat angka.
10. **Response**: Mengembalikan status `200 OK` dengan status `ok`, data jawaban, dan metadata log terstruktur.

### Flow 2: Semantic / Exploratory Query
*Pertanyaan:* `"Paper apa saja yang membahas stres oksidatif pada Wharton's jelly?"`
1. **UI**: Mengirimkan HTTP POST ke `/api/v1/ask`.
2. **QueryRouter**: Tidak ada pemicu agregasi/relasi → mengklasifikasikan ke rute `semantic`.
3. **VectorRetriever**:
   - Meng-embed teks pertanyaan dengan model `BAAI/bge-m3` (1024d float vector).
   - Menjalankan pencarian vektor kosinus pada tabel `chunks`:
     ```sql
     SELECT DISTINCT ON (p.publication_id)
            p.publication_id, p.title, p.year, p.doi, c.chunk_text,
            (1 - (c.embedding <=> :query_vector)) AS similarity
     FROM chunks c
     JOIN publications p ON c.publication_id = p.publication_id
     WHERE (1 - (c.embedding <=> :query_vector)) >= :threshold
     ORDER BY p.publication_id, c.embedding <=> :query_vector
     LIMIT 8;
     ```
4. **EvidenceUnifier**: Mengubah chunk yang lolos threshold ke dalam `EvidenceSet` (tipe `vector`).
5. **EvidenceRanker**: Mengurutkan dokumen berdasarkan skor kemiripan tertinggi.
6. **AnswerSynthesizer**: Menyusun narasi sintesis dan menyertakan sitasi `[Judul, Tahun, DOI]`.
7. **CitationVerifier**: Memeriksa bahwa setiap `[Judul, Tahun, DOI]` dalam teks cocok dengan dokumen pada `EvidenceSet`.
8. **Response**: Mengembalikan status `200 OK` beserta array `sources` yang terverifikasi.

### Flow 3: Hybrid Query (Semantic + Structured Constraints)
*Pertanyaan:* `"Paper tentang terapi stem cell yang dipublikasikan setelah tahun 2020 oleh institusi Indonesia"`
1. **QueryRouter**: Mendeteksi topik semantik + constraint filter terstruktur → rute `hybrid`.
2. **Entity Gate**: Memvalidasi entitas `country: "indonesia"` (lolos whitelist).
3. **HybridRetriever**: Mengeksekusi parameterized hybrid template:
   ```sql
   SELECT DISTINCT ON (p.publication_id)
          p.publication_id, p.title, p.year, p.doi, c.chunk_text
   FROM chunks c
   JOIN publications p ON c.publication_id = p.publication_id
   JOIN pub_institution pi ON p.publication_id = pi.publication_id
   JOIN institutions i ON pi.institution_id = i.institution_id
   WHERE p.year > 2020
     AND i.country = 'indonesia'
   ORDER BY p.publication_id, c.embedding <=> :query_vector
   LIMIT 8;
   ```
4. **EvidenceUnifier & Ranker**: Menormalkan dan meranking hasil.
5. **Synthesizer & Verifier**: Menghasilkan narasi ber-grounding dengan verifikasi sitasi.

### Flow 4: Relational / Knowledge-Graph Query
*Pertanyaan:* `"Institusi mana yang berkolaborasi dengan peneliti AI?"`
1. **QueryRouter**: Mendeteksi intent relasi/konektivitas antar-entitas → rute `graph`.
2. **GraphRetriever**:
   - Menjalankan pencarian 2 langkah terparameterisasi (Template T3):
     a. Menemukan publikasi bertema AI dan mengekstrak institusi asalnya.
     b. Mengeksekusi pencarian kolaborator pada edge table `institution_collaboration` (Template T1) dengan depth-bound ≤ 3 hop.
   - Mengambil relasi beserta bukti `via_publication_ids`.
3. **EvidenceUnifier**: Menormalkan graf ke dalam `EvidenceSet` (tipe `graph`), menyertakan relasi dan ID publikasi bukti.
4. **Synthesizer**: Menyusun daftar kolaborasi antar-institusi dengan menyebutkan publikasi bukti.
5. **CitationVerifier**: Memvalidasi sitasi publikasi bukti.

### Flow 5: Zero-Match Deterministic Handling
*Pertanyaan:* `"Penelitian tentang partikel tachyon di laboratorium CERN pada database ini"`
1. **RetrievalEngine**: Menjalankan query; hasil pencarian menghasilkan 0 baris/chunk di atas threshold.
2. **EvidenceUnifier**: Menghasilkan `EvidenceSet` kosong (`count = 0`).
3. **Short-Circuit Gate**: Sistem mendeteksi `EvidenceSet` kosong → **LLM synthesis call tidak dipanggil**.
4. **FastAPI Response**: Langsung mengembalikan status HTTP 200 dengan payload:
   ```json
   {
     "request_id": "...",
     "status": "not_found",
     "route": "semantic",
     "answer": "No matching evidence was found in the database.",
     "sources": []
   }
   ```
   *Keuntungan*: Latensi < 200ms, menghemat komputasi CPU, dan menghilangkan 100% risiko halusinasi pada data kosong.

---

## 5. Offline Ingestion & Indexing vs Online Query Serving Pipelines

Arsitektur sistem secara tegas memisahkan proses ingestion/indexing di latar belakang (*offline*) dari penanganan query pengguna secara real-time (*online*).

```text
================================================================================
                    OFFLINE PIPELINE (Batch & Idempotent)
================================================================================

Raw Scopus Export
       ↓
Data Validation ──> Cleaning Rules ──> Normalization (04 §1)
                                              ↓
                               ┌─────────────────────────────┐
                               │     PostgreSQL Database     │
                               │  (Canonical Source of Truth)│
                               └─────────────────────────────┘
                                              │
                      ┌───────────────────────┴───────────────────────┐
                      ▼                                               ▼
         Offline Embedding Pipeline                      Graph Materialization Script
        (`scripts/embed_chunks.py`)                       (`scripts/build_edges.py`)
                      │                                               │
           BAAI/bge-m3 Model (1024d)                       Junction Aggregation (Canonical a < b)
                      │                                               │
                      ▼                                               ▼
          `chunks.embedding` Column                    Edge Tables Materialization
          HNSW Cosine Index Build                      (`institution/author_collaboration`)
          [Derived Semantic Index]                     [Derived Relationship Index]


================================================================================
                    ONLINE PIPELINE (Low-Latency Serving)
================================================================================

User Question
      ↓
FastAPI Gateway (`POST /api/v1/ask`) ──> Boundary Validation & `request_id`
      ↓
QueryRouter (Deterministic Intent Classifier + Entity Gate)
      ↓
RetrievalEngine (Parallel Fan-Out via Connection Pool)
      ├── SqlRetriever        ──> PostgreSQL (app_readonly)
      ├── VectorRetriever     ──> pgvector HNSW
      ├── GraphRetriever      ──> Edge Tables (max_hops ≤ 3)
      └── HybridRetriever     ──> Parameterized Combined Query
              ↓                   ↓                 ↓
      ┌────────────────────────────────────────────────────────┐
      │               EvidenceUnifier Engine                   │
      │        (Canonical Normalization & Deduplication)       │
      └────────────────────────────────────────────────────────┘
                                  ↓
                        EvidenceRanker (Deterministic)
                                  ↓
                       AnswerSynthesizer (Ollama CPU)
                                  ↓
                      CitationVerifier (Post-Hoc Check)
                                  ↓
                     Grounded Response Payload
```

### Prinsip Pemisahan:
1. **Tidak Ada Indexing di Jalur Online**: Komputasi berat seperti embedding dokumen dan materialisasi graf hanya berjalan di pipeline offline.
2. **Sinkronisasi Batch untuk MVP**: Sinkronisasi data antara PostgreSQL dengan layer vector/graf dilakukan secara berkala melalui skrip batch idempotent. Arsitektur Change Data Capture (CDC / outbox pattern) dialokasikan untuk fase pasca-MVP (Phase 7 — Performance / Worker Preparation di 10 §0.1; CDC tidak diimplementasikan di MVP).

---

## 6. Knowledge Graph & Semantic Indexing Architecture

### 6.1 Model Entitas dan Relasi Knowledge Graph (MVP Surface)
Knowledge Graph adalah kapabilitas **wajib MVP** yang dimodelkan sebagai indeks relasi turunan dari PostgreSQL:

```text
(Author)       ──[:AUTHORED]──────────────> (Publication)
(Author)       ──[:AFFILIATED_WITH]───────> (Institution)
(Publication)  ──[:HAS_KEYWORD]───────────> (Keyword)
(Publication)  ──[:FUNDED_BY]─────────────> (Funder)
(Publication)  ──[:CITES]─────────────────> (Publication)  [Evaluated: unlinked di MVP jika referensi string mentah]
(Institution)  ──[:COLLABORATES_WITH]─────> (Institution)  [Materialized: institution_collaboration]
(Author)       ──[:COAUTHORED_WITH]───────> (Author)       [Materialized: author_collaboration]
```

### 6.2 Keputusan Teknologi Graf (ADR: Graph Backend Selection)
- **Status Keputusan**: **PENDING** (antara `Apache AGE` dan `Kùzu`).
- **Alasan**: Bukti infrastruktur repositori saat ini belum memadai untuk mengunci implementasi engine graf mandiri tanpa pengujian dependensi C++/ekstensi.
- **Kandidat Resmi**:
  1. `Apache AGE`: Ekstensi graf untuk PostgreSQL (memungkinkan openCypher langsung di atas PostgreSQL).
  2. `Kùzu`: Embedded columnar graph database berbasis C++ dengan integrasi Python yang sangat cepat.
- **Batasan Eksplisit**: `Neo4j` dan `Memgraph` **dinyatakan OUT-OF-SCOPE** untuk MVP karena memerlukan service terpisah yang membebani alokasi resource CPU/RAM.
- **Implementasi MVP Minimum Surface**: Untuk memastikan MVP tetap berjalan tanpa menunggu penyelesaian engine graf mandiri, Knowledge Graph diimplementasikan secara elegan menggunakan **PostgreSQL Materialized Edge Tables** (`institution_collaboration` dan `author_collaboration`) dengan traversal berbasis recursive CTE terparameterisasi (04 §4.2, 05 §6.2).

### 6.3 Aturan Pembatasan Traversal (Traversal Guardrails)
- **Hard Depth Clamp**: Traversal relasi dibatasi maksimal 3 hop (`max_hops = 3`).
- **Result Clamping**: Batas maksimal baris graf adalah `LIMIT 50`.
- **Integritas Bukti (Provenance)**: Setiap edge graf WAJIB menyertakan array `via_publication_ids` untuk memastikan setiap hubungan relasional dapat ditelusuri ke publikasi aslinya.
- **Pencegahan Injeksi**: Bahasa query graf mentah (raw Cypher/SQL) tidak pernah diekspos ke frontend atau dihasilkan bebas oleh LLM; seluruh query dieksekusi melalui templat terparameterisasi (T1–T4).

---

## 7. Deployment Topology (MVP & Local Dev)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Local Development / Single VM                   │
│                                                                        │
│   ┌────────────────────────────────┐  Internal   ┌─────────────────┐   │
│   │         FastAPI Backend        │ ──────────> │  Local Ollama   │   │
│   │  (Uvicorn, Python 3.11, async) │   HTTP      │ (Qwen2.5-Coder) │   │
│   └────────────────────────────────┘             └─────────────────┘   │
│                   │                                                    │
│                   │ HTTPS (Connection Pool, app_readonly)              │
│                   ▼                                                    │
│   ┌────────────────────────────────┐                                   │
│   │     PostgreSQL + pgvector      │                                   │
│   │      (Managed Supabase)        │                                   │
│   └────────────────────────────────┘                                   │
└────────────────────────────────────────────────────────────────────────┘
```

- **Infrastruktur MVP**: Seluruh backend Python dan Ollama berjalan di atas **satu VM/server CPU** menggunakan Docker Compose.
- **Spesifikasi Minimum**: 8 vCPU, 16 GB RAM (alokasi: ~6 GB untuk model Qwen 7B Q4_K_M, ~2 GB untuk embedding `bge-m3`, sisanya untuk OS, buffer memory, dan connection pooling).
- **Asumsi Konkurensi**: Didesain untuk **1–2 request konkuren** dalam batas target latensi 15 detik. Beban konkurensi tinggi bukan target MVP.
- **Frontend Hosting**: Next.js di-deploy terpisah (misalnya via Vercel) atau di-serve sebagai static export dari reverse proxy.

---

## 8. Async Evolution, Worker Queue & Streaming (Post-MVP / Future)

### 8.1 Target MVP (Sinkron & Langsung)
Pada fase MVP, pipeline request-response diproses secara **sinkron (synchronous)** untuk meminimalkan kompleksitas operasional:

```text
Client ──[HTTP POST]──> FastAPI Gateway ──> Retrieval ──> LLM ──[JSON Response]──> Client
```

### 8.2 Evolusi Pasca-MVP (Berbasis Kebutuhan Terukur)
Penambahan komponen asinkron **hanya akan dilakukan** jika hasil pengukuran baseline latensi pada Phase 7 (Task 12) membuktikan adanya bottleneck nyata:

```text
Client ──[HTTP POST]──> FastAPI Gateway ──> Redis Queue ──> Celery/arq Worker ──> RAG Pipeline
                                                                                    │
Client <──[SSE Stream]── FastAPI Gateway <── Redis Pub/Sub <────────────────────────┘
```

- **Kandidat Komponen Future**:
  - `Redis`: Menyediakan caching semantik untuk pertanyaan berulang dan antrean tugas background.
  - `Celery` / `arq`: Menjalankan query retrieval multi-hop yang memakan waktu lama.
  - GPU inference: Relokasi Ollama ke instance GPU bila budget tersedia (tidak mengubah arsitektur — swap model + compute, lihat 09 §7).
  - `POST /api/v1/ask/stream`: Menyediakan token-by-token streaming response melalui Server-Sent Events (SSE).
- **Aturan Arsitektur**: Tidak ada message broker, cache server, atau worker terpisah yang dipasang di awal (upfront) sebelum baseline latensi CPU selesai diukur.

---

## 9. Performance Budget & Latency Breakdown Target

Target latensi end-to-end pada lingkungan CPU-only (8 vCPU) ditetapkan secara teoritis **< 15 detik** per request. Angka ini berstatus **TARGET DESAIN** dan belum terverifikasi secara empiris hingga Phase 7 (Task 12) dijalankan:

$$\text{Total Latency} = t_{\text{validation}} + t_{\text{routing}} + t_{\text{embedding}} + t_{\text{retrieval}} + t_{\text{evidence}} + t_{\text{synthesis}} + t_{\text{verification}}$$

| Komponen Tahapan | Estimasi Target | Rationale & Batasan Teknis |
|---|---|---|
| `validation_ms` | < 10 ms | Validasi schema Pydantic di memori backend. |
| `routing_ms` | < 50 ms | Klasifikasi berbasis aturan deterministik (pola regex/kata kunci). *Jika fallback LLM aktif: ~1.500–2.500 ms*. |
| `embedding_ms` | < 250 ms | Satu pemanggilan model `bge-m3` lokal untuk string pertanyaan pendek. |
| `sql_ms` / `vector_ms` / `graph_ms` | < 500 ms | Eksekusi query terindeks di database (HNSW index / B-Tree PK join) dengan statement timeout 10s. |
| `evidence_ms` | < 50 ms | Normalisasi bukti, deduplikasi in-memory, dan pemeringkatan deterministik. |
| `llm_ms` (Sintesis) | 5.000 – 10.000 ms | Pembangkitan 200–400 token oleh Qwen2.5-Coder-7B pada 8 vCPU (~25–35 token/detik). |
| `verification_ms` | < 50 ms | Parsing regex sitasi dan pencocokan string terhadap `EvidenceSet`. |
| **Total Target** | **~7 – 12 detik** | Berada dalam batas anggaran latensi MVP (< 15 detik). |

---

## 10. Architecture Decision Records (ADRs) Summary

| Keputusan Arsitektur | Status | Rujukan Dokumen | Rationale & Detail Konsistensi |
|---|---|---|---|
| **PostgreSQL as Canonical Source of Truth** | Decided | 04 §0 | PostgreSQL adalah penyimpan tunggal metadata kanonikal. Vector dan Graf adalah indeks turunan read-only. |
| **pgvector as Semantic Layer** | Decided (Target) | 04 §4.1, 09 §3 | `BAAI/bge-m3`, 1024 dimensi, index HNSW (`vector_cosine_ops`), `DISTINCT ON (publication_id)`. |
| **Knowledge Graph as Mandatory MVP Capability** | Decided | 04 §4.2, 05 §6 | Masuk dalam cakupan MVP menggunakan minimum surface: node Author, Publication, Institution, Keyword, Funder; bounded traversal max 3 hop. |
| **Graph Technology Selection** | **Pending** | 09 §8 | Kandidat terbatas: `Apache AGE` vs `Kùzu`. `Neo4j/Memgraph` ditolak tanpa bukti repo. Interim MVP menggunakan edge tables relasional. |
| **Evidence Unification Layer** | Decided (Target) | 05 §7.0 | Seluruh engine retrieval wajib menormalkan data ke `Evidence`/`EvidenceSet` sebelum masuk ke synthesizer. |
| **Query Routing Strategy** | Decided (Target) | 05 §2.0 | Rule-based intent classification + pola deterministik diutamakan; LLM router bertindak sebagai opsi fallback. |
| **API Contract Standardization** | Decided | 06 §1 | Endpoint kanonikal: `POST /api/v1/ask` dan `GET /api/v1/health`. Desain v2 `/api/query` resmi berstatus superseded. |
| **Offline/Online Pipeline Separation** | Decided | §5 dokumen ini | Ingestion/indexing berjalan batch di offline; serving query berjalan sinkron di online. Sinkronisasi MVP bersifat batch. |
| **Embedding Versioning & Reprocessing** | Decided (Target) | 04 §7 | Kolom metadata `embedding_model`, `embedding_version`, `embedding_dim` wajib dicatat; mendukung batch reprocessing idempotent. |
| **Bounded Graph Traversal** | Decided (Target) | 05 §6.3 | Traversal dibatasi `max_hops = 3`, `LIMIT 50`, dan wajib menyertakan array bukti `via_publication_ids`. |
| **LLM Grounding & Zero-Hallucination** | Decided | 05 §7.1, §7.4 | Sintesis hanya dari evidence yang ada; verifikasi sitasi post-hoc; 0-evidence menghasilkan respons `not_found` deterministik tanpa LLM call. |
| **Deferred Asynchronous Architecture** | Decided (Deferred)| §8 dokumen ini, 11 §4 | Redis, Celery, worker queues, dan SSE streaming ditunda ke pasca-MVP hingga bottleneck latency terbukti secara empiris. |

---

## 11. Implementation Status Matrix (Verifikasi Repositori 2026-09-27)

Repositori hanya berisi Markdown (`README.md` + `docs/00–11`); tidak ada `backend/`, `frontend/`, `database/`, `scripts/`, `docker/`, `tests/`, `.env.example`, atau `docker-compose.yml`. Tidak ada status yang diklaim implemented tanpa bukti kode. Jangan merepresentasikan komponen planned sebagai implemented.

| Component | Current Status | Target Status | Gap |
|---|---|---|---|
| FastAPI | NOT IMPLEMENTED | MVP | Skeleton, `/api/v1`, validation, `request_id`, error handling, health (Phase 1) |
| API v1 | NOT IMPLEMENTED | MVP | `POST /api/v1/ask`, `GET /api/v1/health` (06 §1); `/api/query` superseded |
| Query Router | NOT IMPLEMENTED | MVP | 4 rute; rule-based/deterministik diutamakan; LLM router opsional |
| SQL Retrieval | NOT IMPLEMENTED | MVP | SqlRetriever: generation + AST validation + read-only exec (Phase 2) |
| Vector Retrieval | BLOCKED BY INFRASTRUCTURE | MVP | `chunks.embedding` kosong; embedding backfill + HNSW dulu (Phase 3) |
| Evidence Unification | NOT IMPLEMENTED | MVP | `Evidence`/`EvidenceSet`/`EvidenceUnifier` (§0, 05 §7.0; Phase 4) |
| Knowledge Graph | NOT IMPLEMENTED | MVP | Edge tables belum dibuat; graph backend decision pending (Phase 5) |
| Graph Retrieval | NOT IMPLEMENTED | MVP | GraphRetriever + bounded traversal `max_hops = 3` (Phase 5) |
| Answer Synthesis | NOT IMPLEMENTED | MVP | Synthesizer + citation verifier + `not_found` deterministik |
| Embedding Pipeline | NOT IMPLEMENTED | MVP | Batch pipeline + idempotency + versioning metadata (Phase 3) |
| Hybrid Retrieval | NOT IMPLEMENTED | MVP | Unifier + ranking + synthesizer di atas 3 retriever (Phase 6) |
| Async Workers | NOT IMPLEMENTED | Future | Hanya jika pengukuran Phase 7 membutuhkan |
| Reranking | NOT IMPLEMENTED | Future | Deterministik dulu; cross-encoder/`bge-reranker-large` kelak |

Pemetaan fase normatif (detail di 10 §0.1): Phase 0 Repository Audit → Phase 1 API Foundation → Phase 2 SQL Retrieval → Phase 3 Vector Retrieval → Phase 4 Evidence Layer → Phase 5 Knowledge Graph → Phase 6 Hybrid Retrieval → Phase 7 Performance / Worker Preparation.

---

## 12. Documentation Gaps (Keputusan & Bukti yang Masih Terbuka)

1. **Graph backend selection**: belum ada `reason`, `integration method`, `local development setup`, dan `data synchronization approach` yang tercatat — Decision: Pending (AGE vs Kùzu).
2. **Embedding similarity threshold**: nilai awal di 05 §4.1 adalah tebakan; kalibrasi manual pasca-embedding belum didokumentasikan.
3. **`chunks` granularity**: rasio `count(*)` vs `count(distinct publication_id)` (04 §3.9) belum diverifikasi ke data aktual.
4. **`information_schema` reconciliation (Task 0)**: skema 04 direkonstruksi dari deskripsi cleaning, bukan introspeksi — hasil query verifikasi belum ditempel.
5. **Latency numbers**: seluruh angka §9 adalah target desain; belum ada pengukuran baseline.
6. **Relational→graph naming**: contoh `relational` di dokumen v2 sudah dipetakan ke rute `graph`, tetapi audit menyeluruh istilah `relational` sisa masih terbuka.

---

## 13. Implementation Gaps (Pekerjaan Rekayasa yang Masih Harus Dilakukan)

Backend skeleton + `/api/v1`; QueryRouter; SqlRetriever; embedding backfill + HNSW; edge-table build script; GraphRetriever; Evidence layer; synthesizer + verifier; hybrid composition; health checks (PostgreSQL, pgvector, LLM, embedding model, graph store); logging/observability (`request_id` propagation, field NFR4); seluruh kategori test plan di 10 §0.2 (router, SQL, vector, graph, evidence, answer, API); worker/cache/streaming/reranker futures. Tidak ada yang dikerjakan dalam task dokumentasi ini.

---

## 14. Next Implementation Task (Tunggal, Paling Prioritas)

**Task 0 — Repository / Database Audit**: jalankan `information_schema.columns` terhadap Supabase real, rekonsiliasi `04 Database Schema.md`, dan verifikasi granularitas `chunks`. Seluruh lapisan di atasnya (SQL prompt, read-only role, embedding, edge tables) bergantung pada skema yang akurat — skema yang salah di system prompt adalah penyebab paling umum kegagalan text-to-SQL.
