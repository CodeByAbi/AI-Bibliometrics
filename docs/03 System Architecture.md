# Arsitektur Sistem — End-to-End (Hybrid Master Blueprint)

**Versi Dokumen:** 3.7.2 (Fase 7 Close-out — sinkronisasi Gold DONE terverifikasi live)  
**Tanggal Status:** 2026-10-03  
**Menggantikan:** `03 System Architecture.md` v3.7.1 (2026-10-03)
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  
> **Status Implementasi & Realitas Basis Data (Sinkronisasi Progress 2026-09-29):**  
> 1. **Database PostgreSQL:** Tim **sudah membuat database PostgreSQL**. Kredensial koneksi sudah tersedia secara internal (tidak diekspos di dokumentasi).  
> 2. **Dataset Prototipe:** Database memuat **dataset prototipe kecil** (22 kolom pada `publications`) untuk **validasi end-to-end (E2E)**.
> 3. **9 Tabel Relasional Silver:** Dataset prototipe ter-load pada 9 tabel kanonikal: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, dan `chunks`.  
> 4. **Komponen Turunan (Phase 0–2 DONE, Gold DONE — terverifikasi live 2026-10-03):** Kolom vektor `chunks.embedding vector(1024)` + indeks HNSW (Task 1), 2 edge tables `institution_collaboration` (254 baris) dan `author_collaboration` (484 baris) (Task 8), kerangka FastAPI + DB pool + Ollama (Task 2–3), dan 3 tabel Gold Analytics `topics` (5), `topic_evolution` (25), `researcher_expertise` (140) (Task 8.5) sudah DONE dan terverifikasi live.  
> 5. **Cleaning & Export — DONE:** Data Scopus sudah dibersihkan dan berhasil di-export sebagai 9 file `data/*_cleaned.csv`. Retrieval 4-rute + sintesis LLM opt-in DONE (Fase 7). NEXT: Fase 8 (verifikasi formal + baseline latensi + sign-off MVP).

---

## 0. Arsitektur Target & Invariant Inti

### 0.1 Pipeline Target End-to-End
Arsitektur target mengalirkan pertanyaan pengguna secara linear dan deterministik dari antarmuka web hingga respons ter-grounding:

```mermaid
flowchart TD
    UserQuestion[Pertanyaan Pengguna] --> APIGateway[Gateway FastAPI: POST /api/v1/ask]
    APIGateway --> BoundaryVal[Validasi Batas & Pembuatan ID Request]
    BoundaryVal --> Router[Question Router: Dynamic 4-Route Dispatcher]
    
    subgraph RetrievalEngine [Fan-Out Mesin Retrieval]
        Router -->|SQLRoute| SQLR[SqlRetriever: 9 Tabel Relasional Silver]
        Router -->|VectorRoute| VecR[VectorRetriever: chunks.embedding BAAI/bge-m3 HNSW]
        Router -->|GraphRoute| GraphR[GraphRetriever: Recursive CTE Terparameterisasi T1-T4 pada Derived Edge]
        Router -->|HybridRoute| HybR[HybridRetriever: Lapisan Gold Analytics + Tabel Silver]
    end
    SQLR & VecR & GraphR & HybR --> Unifier[EvidenceUnifier: Normalisasi & Deduplikasi]
    Unifier --> Ranker[EvidenceRanker: Skoring Deterministik & Kalkulasi Confidence]
    Ranker --> Context[Konstruksi Konteks & Pembingkaian Data Tidak Tepercaya]
    Context --> Synth[AnswerSynthesizer: Mesin LLM Analitik]
    Synth --> Verifier[CitationVerifier & Validator Objek Bukti]
    Verifier --> Output[Jawaban Ter-grounding + Objek Bukti Terstruktur + Sumber]

    Unifier -.->|0 Item Bukti| ShortCircuit[Deterministic Short-Circuit Gate]
    ShortCircuit -->|status: not_found / 0 pemanggilan LLM| Output
```

### 0.2 Lapisan Data Medallion & Pemisahan Dua Fase (Data Architecture & Phasing)

Arsitektur data memisahkan secara tegas dua fase rekayasa:
- **Fase Prototipe / Validasi E2E (Saat Ini):** Basis data PostgreSQL sudah aktif memuat **dataset prototipe kecil** pada 9 tabel relasional Silver (20 publikasi, 40 chunk, 138 author, 107 institusi, 344 keyword, 33 funding, 4.120 referensi). Tujuannya membuktikan seluruh flow end-to-end (Router → Retrieval 4 Rute → Evidence Unifier → LLM Synthesizer → Citation Verifier → API → UI) berfungsi sempurna dengan latensi terukur sebelum menangani dataset besar.
- **Fase Produksi / Skala Besar (Future):** Pipeline otomatisasi ingestion untuk ratusan ribu record Scopus dari berkas mentah (Bronze), pembersihan & deduplikasi multi-tier berkala, batch re-embedding bertahap, dan kalkulasi ulang Gold analytics.

Tingkatan data Medallion pada sistem ini:
1. **Bronze Layer (Raw Staging - Future/Production):** Berkas arsip ekspor mentah Scopus yang immutable beserta hash SHA-256 untuk auditability dan re-ingestion (`docs/12 Data Pipeline.md`).
2. **Silver Layer (Canonical Relational Storage - 9 Tabel Prototipe + 2 Edge Tables):** Sumber kebenaran terstruktur kanonikal yang telah dibersihkan dan dinormalisasi:
   - 9 Tabel Relasional: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, dan `chunks`.
   - 2 Derived Edge Tables (DONE, Task 8 — terverifikasi live: 254 + 484 baris): `institution_collaboration` dan `author_collaboration` yang dimaterialisasi secara idempoten dari `pub_institution` dan `pub_author`.
3. **Gold Layer (Analytics & Intelligence - 3 Tabel - DONE, Task 8.5 — terverifikasi live 2026-10-03):** Tabel analitik derivatif untuk mendukung sintesis kebijakan:
   - `topics`: Klaster topik BERTopic dan representasi vektor 1024-dimensi.
   - `topic_evolution`: Metrik time-series tahunan, growth score, dan citation acceleration.
   - `researcher_expertise`: Pemeringkatan kepakaran peneliti multi-dimensi terbobot ($\text{ExpertiseScore} = w_1 \cdot \text{Relevance} + w_2 \cdot \text{Productivity} + w_3 \cdot \text{Impact} + w_4 \cdot \text{Recency}$).

### 0.3 Invarian Arsitektur Wajib (Architectural Invariants)
1. **Source of Truth Invariant**: PostgreSQL adalah satu-satunya sumber kebenaran kanonikal. Ekstensi `pgvector`, edge tables, dan Gold tables adalah struktur turunan (*derived structures*) yang selalu disinkronkan dari tabel Silver.
2. **Strict Grounding & Evidence Object Enforcement**: LLM berfungsi murni sebagai **mesin sintesis analitik naratif, BUKAN sumber angka mentah atau statistik**. Setiap fakta numerik wajib dibungkus dalam `EvidenceObject` terstruktur (`claim`, `metric`, `value`, `period`, `sources`, `confidence`) yang ditarik langsung dari database.
3. **Security Invariant**: Akses database aplikasi runtime FastAPI wajib menggunakan role `app_readonly` dengan izin `SELECT` saja, `SET search_path = public`, dan `statement_timeout = '10s'`. Seluruh teks publikasi yang ditarik diperlakukan sebagai **DATA TIDAK TERPERCAYA (UNTRUSTED DATA)**.
4. **Zero-Hallucination Invariant**: Jika retrieval menghasilkan 0 item bukti, sistem wajib mengembalikan `status: not_found` secara deterministik dalam waktu < 200ms tanpa memanggil LLM.
5. **Session Isolation Invariant**: Persistensi aplikasi/sesi (`research_sessions`, `research_messages`, `research_session_summaries` pada schema `app`) menyimpan **conversation state saja** dan **bukan** bagian dari canonical bibliometric source of truth. **Tidak ada operasi lifecycle pada sesi yang boleh mengubah atau menghapus data bibliometrik kanonikal.** Seluruh fakta dan metrik bibliometrik tetap harus diperoleh melalui retrieval terhadap data bibliometrik PostgreSQL di schema `public`.

   Relaksinya bersifat struktural, bukan konvensi:

   - **Batas skema.** Tabel sesi berada di schema `app`, sedangkan pool retrieval mengunci `search_path = public`. Tabel sesi karena itu *tidak dapat dicapai* dari jalur baca retrieval, bahkan oleh SQL hasil Text-to-SQL yang salah nama.
   - **Batas kredensial.** Dua pool, dua peran: `app_readonly` (SELECT pada `public`) dan `app_session` (SELECT/INSERT/UPDATE/DELETE pada `app`). Keduanya diverifikasi dua arah oleh `scripts/grant_session_role.py`.
   - **Batas whitelist.** `ALLOWED_TABLES` pada `sql_security.py` **tidak** memuat tabel sesi, sehingga SQL yang dihasilkan LLM tidak dapat membaca transkrip.
   - **Batas data ownership.** Tidak ada kolom metrik bibliometrik **tingkat sesi** pada tabel sesi mana pun. `SessionSummaryService.build()` secara struktural tidak menerima `EvidenceSet`/`EvidenceObject` sebagai parameter, sehingga tidak *bisa* memuat angka.

   **Pengecualian terelatasi (migrasi 006) — `evidence_objects` / `sources`.** Dua kolom JSONB pada `research_messages` memang memuat nilai metrik, jadi ini pengecualian yang **disetujui owner** terhadap invarian di atas, dan **dipagar**, bukan dilonggarkan diam-diam. Keterangkannya: snapshot **immutable per-turn** dari apa yang sudah dibawa satu `AskResponse` terverifikasi, agar workspace yang ditutup dapat digambar ulang tanpa menjalankan ulang RAG. Yang tetap dilarang: menjadi penyimpanan metrik kanonik; dibaca sebagai sumber kebenaran oleh retrieval/agregasi/peringkat/analitik/sintesis/verifikasi; menjadi agregat tingkat sesi; dan tetap tidak terjangkau dari jalur baca (`search_path = public`). Kunci re-verifikasi tetap `request_id`. Pagar mekanisnya: `audit_provenance_columns()` di `scripts/verify_schema.py` (harus JSONB, hanya di `research_messages`), pembedaan sengaja antara `list_messages()` (memilih) dan `list_recent_messages()` (tidak memilih, karena itu menyusun prompt), serta `tests/unit/test_session_provenance.py`. Detail lengkap: docs/04 §13.1.

   Konsekuensi operasional: session context (summary + N pesan terakhir) hanya membantu **memahami** pertanyaan lanjutan dengan mengisi filter yang tidak disebutkan pengguna. `question` tidak pernah ditulis ulang, sehingga routing, Text-to-SQL, `EvidenceSet.query`, dan verifikasi sitasi tetap sama persis. Setiap angka dalam jawaban tetap berasal dari retrieval ke `public` — angka yang pernah muncul di jawaban assistant sebelumnya harus **di-query ulang**, bukan dipercaya (AC-SESSION-8, AC-SESSION-11).

   **Opt-out (`use_session_context=false`).** Mematikan session context punya **dua** efek, bukan satu:

   1. **Scope** dihitung dari nol — filter sesi tidak diwariskan ke `FilterParams`.
   2. **Transkrip ditahan dari prompt LLM.** `ConversationContext` tidak dirender menjadi blok `[Percakapan sebelumnya]`, sehingga langkah narasi optional tidak pernah melihat jawaban sebelumnya.

   Efek (2) ada karena flag tersebut berarti "anggap pertanyaan ini berdiri sendiri". Kalau transkrip tetap masuk prompt, flag itu hanya bekerja setengah: scope global, tapi narasi lokal — jawaban masih bisa merujuk percakapan lama yang seharusnya diabaikan. Persistensi turn **tetap** berjalan; opting out dari prompt bukan opting out dari riwayat.

   ```
   if payload.use_session_context:            # satu cabang untuk KEDUA efek
       merged_filters, applied = effective_filters(explicit, context.scope)
       effective_payload = payload.model_copy(update={"filters": merged_filters})
       conversation_block = render_conversation_block(context) or None
   ```

   Keduanya berada di dalam satu `if` dengan sengaja. Membaginya menjadi dua kondisi terpisah akan memungkinkan kombinasi yang tidak masuk akal — scope global tapi transkrip lokal — dan kombinasi itulah yang membuat flag menyesatkan.

   ```
                    APPLICATION STATE (schema `app`)
                              │
                  ┌───────────┴───────────┐
                  │                       │
               Session               Messages
                  │                       │
                  └───────────┬───────────┘
                              │
                            Summary
                              │
                              ▼
                     Conversation Context
                              │
                              ▼
                         User Question
                              │
                              ▼
                         /api/v1/ask
                              │
                              ▼
                       QuestionRouter
                              │
                              ▼
               ┌──────────────────────────┐
               │  Bibliometric Retrieval  │
               └────────────┬─────────────┘
                            │
                            ▼
                 BIBLIOMETRIC SOURCE OF TRUTH
                       (schema `public`)
                            │
                            ▼
                          Evidence
                            │
                            ▼
                             LLM
                            │
                            ▼
                     Grounded Answer
                            │
                            ▼
                    Persist Message
   ```

   **Aturan satu kalimat:** *Session mengingat apa yang dibicarakan pengguna dan assistant; basis data bibliometrik menentukan apa yang secara faktual benar tentang data riset.*

### 0.4 Di mana Lapisan Session Berdiri (Session Layer Placement)

```
Frontend  (Next.js)
    │  /research/{session_id}  — identitas workspace, lives di URL
    ↓
Session Layer / Research Workspace        ← ORKESTRASI + PERSISTENSI
    │  POST /api/v1/sessions · GET · PATCH · DELETE
    │  orkestrasi turn: simpan user → jalankan pipeline → simpan assistant
    ↓
POST /api/v1/ask
    ↓
Pipeline RAG yang ada (TIDAK DIROMBAH)
    Gateway → QuestionRouter → Retrieval → EvidenceUnifier
            → EvidenceRanker → Context → AnswerSynthesizer
            → CitationVerifier → Output
```

> **Persistensi session adalah urusan orkestrasi/persistensi di sekeliling pipeline
> RAG — bukan urusan retrieval.** Lapisan session menyimpan dan memulihkan
> hasil RAG; ia tidak mendefinisikan, menghitung, atau menafsirkan ulangannya.
> Tidak ada satu pun baris retriev, router, evidence, maupun synthesizer yang
> diubah oleh lapisan ini.

Alasannya, dan mengapa urutan itu tidak boleh dibalik:

- **Orkestrasi_wraps, bukan mewarisi.** `SessionService` memanggil pipeline
  melalui satu titik yang sudah ada; ia tidak pernah masuk ke router, retriever,
  evidence, atau synthesizer. Import boundary itu juga yang membuat
  `SessionRepository` mustahil mencapai korpus.
- **Urutan mattered.** Validasi sesi → muat konteks → **simpan turn user** →
  pipeline → **simpan turn assistant** → `updated_at` → ringkasan. Turn user
  di-commit **sebelum** retrieval, karena LLM berjalan beberapa detik; kegagalan
  di hilir tidak boleh ikut merollback pertanyaan pengguna.
- **Transaksi hanya untuk hal yang perlu atomik.** `_persist_turn` adalah satu-satunya
  tempat transaksi dibuka, dan **tidak** membungkus eksekusi RAG. Satu INSERT
  menyimpan turn beserta snapshot provenance-nya, sehingga snapshot mustahil
  ter-commit tanpa turn-nya.
- **Biaya ditambahkan ke latency nol.** Pembuatan judul dan ringkasan bersifat
  deterministik dan tanpa LLM — bukan panggilan LLM tambahan setelah sintesis.
  Ringkasan dibangun ulang dari ekor transkrip yang **dibatasi**
  (`session_recent_messages_limit`), bukan dengan mengirim seluruh transkrip ke
  model pada setiap permintaan.

---

## 1. Topologi Komponen Tingkat Tinggi

```
┌─────────────────────────────────┐           HTTPS            ┌──────────────────────────────────────────────┐
│        Next.js Frontend         │ ─────────────────────────> │             FastAPI Backend API              │
│   (Putih Bersih, Padat,             │ <───────────────────────── │       (Inti Aplikasi Python Async)         │
│      gaya Notion/Linear)            │        JSON Response       └──────────────────────────────────────────────┘
└─────────────────────────────────┘                                                   │
                                                       ┌───────────────────────────────┼───────────────────────────────┐
                                                       ▼                               ▼                               ▼
                                               ┌───────────────┐               ┌───────────────┐               ┌───────────────┐
                                                │Question Router  │               │ Ollama Lokal  │               │ Embed Lokal   │
                                               │   (4 Rute)  │               │(Qwen2.5-Coder │               │ (BAAI/bge-m3, │
                                               │               │               │  7B-Instruct) │               │   1024 dims)  │
                                               └───────────────┘               └───────────────┘               └───────────────┘
                                                       │                               │                               │
                                                       └───────────────────────┬───────┴───────────────────────────────┘
                                                                               ▼
                                               ┌───────────────────────────────────────────────────────────────┐
                                               │               Database PostgreSQL                             │
                                                │  • Silver: 9 Tabel Relasional Kanonikal                       │
                                                │  • Derived Edge: institution/author_collaboration (DONE)      │
                                                │  • Gold Analytics: topics, topic_evolution, exp (DONE)        │
                                                │  • Lapisan Semantik pgvector: chunks.embedding (DONE)         │
                                                │  • Peran Koneksi Target: app_readonly (hanya SELECT); runtime .env saat ini memakai role postgres — risiko yang diterima, lihat reports/fase7_closeout.md §B2 │
                                               └───────────────────────────────────────────────────────────────┘
```

---

## 2. Spesifikasi Rute & Komponen Retrieval

| Rute RAG | Lapisan Data Target | Strategi Eksekusi & Validasi | Jenis Objek Bukti yang Dihasilkan |
|---|---|---|---|
| **`SQLRoute`** | Silver Relasional (`publications`, `authors`, `institutions`, `funding`, `keywords`, `pub_author`, `pub_institution`, `publication_references`) | Text-to-SQL $\rightarrow$ Validasi AST `sqlglot` $\rightarrow$ Penegakan `LIMIT 50`. | `publication_count`, `citation_count`, total pendanaan. |
| **`VectorRoute`** | Silver Vector (`chunks.embedding vector(1024)`) JOIN `publications` | Embedding kueri `BAAI/bge-m3` $\rightarrow$ jendela ANN ber-indeks HNSW (urut operator jarak, *overfetch* 25×) $\rightarrow$ `DISTINCT ON (publication_id)` di luar jendela $\rightarrow$ `LIMIT 8`. Gerbang kosinus $\ge 0.65$. | Ringkasan abstrak ilmiah, kemiripan semantik, tautan DOI. |
| **`GraphRoute`** | Derived Edge (`institution_collaboration`, `author_collaboration`) | Recursive CTE Terparameterisasi (Templat T1–T4) $\rightarrow$ Kedalaman `max_hops = 3`. | Bukti kolaborasi institusi/penulis via `via_publication_ids`. |
| **`HybridRoute`** | Gold Analytics (`topics`, `topic_evolution`, `researcher_expertise`) + Silver & `chunks` | Gabungan (join) analitik multi-tabel terparameterisasi $\rightarrow$ Ekstraksi metrik deret waktu & skor kepakaran. | `growth_score`, `citation_acceleration`, `expertise_score` terbobot ($w_1\text{--}w_4$). |

---

## 3. Kebutuhan Non-Fungsional (Verifikasi NFR)

1. **NFR1: Anggaran Latensi Kueri (Khusus CPU)**:
   - `SQLRoute` & `GraphRoute`: $\le 500\text{ ms}$
   - `VectorRoute`: $\le 1.5\text{ detik}$
   - `HybridRoute` (Gold Analytics): $\le 1.0\text{ detik}$
    - Sintesis LLM (`Qwen2.5-Coder-7B` CPU): $\sim 5\text{–}10\text{ detik}$
    - Total End-to-End: $\le 15\text{ detik}$ (dengan indikator progres pada UI).
2. **NFR2: Keter-groundingan Ketat (Strict Groundedness)**:
   - 100% fakta statistik dan sitasi terikat pada bukti database melalui `EvidenceObject` dan verifikasi `CitationVerifier`. Format sitasi baku: `[Judul, Tahun, DOI]` jika ada DOI, dan `[Judul, Tahun, no-doi]` jika naskah tanpa DOI.
3. **NFR3: Ketertelusuran & Silsilah Data (Auditability & Lineage)**:
   - Setiap respons menyertakan `request_id` (UUIDv4) dan array `sources` yang dapat ditelusuri balik ke record publikasi kanonikal.
4. **NFR4: Keamanan Hak Minimum (Least Privilege Security)**:
   - Isolasi runtime database dengan peran `app_readonly`, statement timeout 10 detik, dan search path terkunci.

---

## 4. Matriks Konsistensi Keputusan (Lintas Dokumen)

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
| **Vector Similarity Gate** | Cosine similarity threshold dikunci deterministik $\ge 0.65$ untuk model `BAAI/bge-m3`; kueri di bawah ambang → short-circuit ke `status: not_found` | `02`, `03`, `05`, `06` | ALIGNED |
| **Format sitasi** | Standar deterministik 3-elemen: `[Judul, Tahun, DOI]` jika ada DOI, dan `[Judul, Tahun, no-doi]` jika naskah tanpa DOI | `01`, `05`, `06`, `07` | ALIGNED |
| **Graph Engine Strategy** | MVP dikunci menggunakan parameterized PostgreSQL Recursive CTE (T1–T4); evaluasi pasca-MVP menggunakan Apache AGE pada Fase 9 | `03`, `04`, `09`, `11` | ALIGNED |
| **Konteks RAG** | Pembingkaian `UNTRUSTED DATA`, LLM murni menyintesis narasi & memvalidasi `EvidenceObject`, short-circuit deterministik pada 0 bukti, `CitationVerifier` post-hoc | `02`, `03`, `05`, `06`, `07`, `08` | ALIGNED |
| **Kontrak API** | `POST /api/v1/ask` (`AskRequest` & `AskResponse` dengan `evidence_objects`) + `GET /api/v1/health`. Endpoint `/api/query` resmi SUPERSEDED | `02`, `03`, `05`, `06`, `07`, `10`, `11` | ALIGNED |
| **Dataset prototipe** | Dataset prototipe kecil (~20 publikasi, 40 chunk, 138 author, 107 institusi, 22 kolom naskah) untuk validasi end-to-end lengkap | `01`, `02`, `03`, `04`, `10`, `11`, `12` | ALIGNED |
| **Dataset skala produksi** | Target masa depan untuk ingestion Scopus skala besar (>100K publikasi) dengan pipeline batch otomatis, deduplikasi multi-tier, dan worker async | `01`, `02`, `03`, `04`, `11`, `12` | ALIGNED |

---

## 5. Keputusan Arsitektur Kanonikal

1. **No-DOI Citation Decision:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Cosine Similarity Threshold Decision (`VectorRoute`):**
   - *Keputusan:* Nilai cosine similarity threshold dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri dengan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Post-MVP Graph Engine Decision:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 6. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/03 System Architecture.md` v3.6.2 | Aturan bahasa: narasi Indonesia, teknis Inggris (`Question Router`, `Short-Circuit Gate`, `Vector Similarity Gate`, dll); sync status Phase 0–2 DONE | Tanpa duplikasi bilingual; perbaiki terjemahan literal yang aneh |
| `docs/03 System Architecture.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/03 System Architecture.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; menandai vector storage sebagai PENDING eksplisit | Sinkronisasi progress aktual 2026-09-27 |
| `docs/03 System Architecture.md` v3.4.0 | Menyeragamkan seluruh komponen dan diagram ke nama tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/03 System Architecture.md` v3.4.0 | Mengunci keputusan threshold kosinus $\ge 0.65$, sitasi `[Judul, Tahun, no-doi]`, dan strategi graf Apache AGE | Menutup open decision menjadi keputusan kanonikal |
| `docs/03 System Architecture.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin konsistensi format dan keputusan di seluruh repository |
