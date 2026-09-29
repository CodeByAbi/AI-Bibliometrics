# Desain Pipeline Data & Ingestion — Scopus menuju Riset Intelijen (Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.2 (Consolidated Hybrid Master Blueprint — aturan bahasa: narasi Indonesia, teknis Inggris)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `12 Data Pipeline.md` Draft v1 s.d. v3.5.0  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/00` hingga `docs/11`  

> **Status Implementasi & Kesiapan Basis Data (Sinkronisasi Progress Phase 1):**  
> 1. **Database PostgreSQL — DONE:** Basis data PostgreSQL **sudah dibuat dan siap pakai**, memuat **dataset prototipe kecil** (~20 publikasi, 40 chunk, 138 author, 107 institusi) pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) untuk validasi end-to-end. Kredensial diamankan secara internal.  
> 2. **Cleaning & Export — DONE:** Data Scopus **sudah melalui proses cleaning dan berhasil di-export** — tersedia sebagai 9 file `data/*_cleaned.csv` (`publications`, `chunks`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`).  
> 3. **Vector Storage & HNSW Index — DONE (Task 1):** Seluruh 40 chunk telah memiliki embedding vector 1024-dim (`BAAI/bge-m3`) di kolom `chunks.embedding` dan indeks HNSW `idx_chunks_embedding_hnsw` (`m=16, ef_construction=64`) serta `idx_chunks_pub_id` telah aktif dan diverifikasi di basis data PostgreSQL.  
> 4. **Tabel Edge Kolaborasi — DONE (Task 8 Bagian Edge):** `institution_collaboration` (254 baris) dan `author_collaboration` (484 baris) telah berhasil dimaterialisasi secara idempoten dari tabel junction Silver.  
> 5. **Pemisahan Dua Fase Pipeline:**  
>    - **Fase Validasi Prototipe E2E (Current):** Data prototipe siap-vektor dan siap-graf menjadi input untuk Task 2-10 (FastAPI, Retrieval, RAG Flow, Evaluasi E2E).  
>    - **Fase Produksi Skala Besar (Future):** Pipeline batch otomatis penuh untuk ingestion berkas mentah Scopus (Bronze), pembersihan multi-tier, dan deduplikasi skala besar.
---

## 1. Tujuan & Arsitektur Ingestion Medallion

Dokumen ini mendefinisikan arsitektur **Pipeline Data & Ingestion** yang mentransformasikan data bibliometrik mentah dari **Scopus** menjadi data publikasi ilmiah yang **kanonikal, ternormalisasi, terindeks secara semantik (vector), siap-graf (*graph-ready*), dan kaya analitik kepakaran (*Lapisan Gold Analytics*)** untuk mendukung sistem **Asisten Riset Intelijen**.

Arsitektur pipeline data distrukturkan ke dalam 3 lapisan Medallion:
1. **Lapisan Bronze (Penampungan Mentah - Masa Depan/Produksi)**: Penyimpanan arsip berkas mentah ekspor Scopus yang immutable beserta hash integritas SHA-256 dan metadata batch run untuk reproduktibilitas.
2. **Lapisan Silver (Penyimpanan Relasional Kanonikal - 9 Tabel Prototipe + 2 Tabel Edge)**: Sumber kebenaran terstruktur yang telah dibersihkan, dinormalisasi, dan di-deduplikasi (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`), dilengkapi 2 tabel edge kolaborasi (`institution_collaboration`, `author_collaboration`).
3. **Lapisan Gold (Analitik & Intelijen - 3 Tabel - PLANNED, Task 8.5)**: Pemrosesan analitik tingkat lanjut untuk mengekstrak klaster topik riset (`topics`), evolusi tren temporal (`topic_evolution`), dan skor kepakaran peneliti multi-dimensi (`researcher_expertise`).

---

## 2. Gambaran Pipeline & Aliran End-to-End

```mermaid
flowchart TD
    subgraph Bronze [1. Ekstraksi & Penampungan Data Mentah - Bronze (Produksi Mendatang)]
        ScopusFile[Berkas Export Mentah Scopus\nCSV / JSON / BibTeX] --> RawStaging[(Arsip Penyimpanan Mentah\nPayload Immutable + SHA256)]
    end

    subgraph Silver_Processing [2. Parsing, Normalisasi & Pemuatan Kanonikal - Silver]
        RawStaging --> Parser[Parser Bibliometrik & Ekstraktor Field]
        Parser --> QualityValidator{Gerbang Kualitas & Skema\nPemeriksaan ID & Tahun Wajib}
        QualityValidator -->|Tidak Valid| QuarantineLog[(Log Karantina)]
        QualityValidator -->|Valid| Normalizer[Normalizer Teks & Entitas\nLowercase / Trim / Titlecase]
        Normalizer --> Deduplicator[Deduplikator Multi-Tier\nDOI -> EID -> Title+Year]
        Deduplicator --> PGLoader[Loader Transaksional Atomik]
        PGLoader --> PostgresSilver[(Inti PostgreSQL: 9 Tabel Kanonikal)]
    end

    subgraph Silver_Vector_Graph [3. Materialisasi Vector & Graf - Task 1 & 8]
        PostgresSilver --> EmbedPipeline[Generator Batch Embedding\nBAAI/bge-m3 1024-dim Float32]
        EmbedPipeline --> VectorStore[(chunks.embedding + Indeks HNSW\nm=16, ef=64, vector_cosine_ops)]
        
        PostgresSilver --> EdgeBuilder[Skrip Materialisasi Edge\nTruncate + Insert Idempoten]
        EdgeBuilder --> GraphStore[(Derived Edge Tables:\ninstitution/author_collaboration)]
    end

    subgraph Gold_Analytics [4. Mesin Analitik Lapisan Gold - Task 8.5]
        PostgresSilver & VectorStore --> TopicEngine[Mesin Pemodelan BERTopic & Co-word]
        TopicEngine --> T_Topics[(Tabel topics\nKlaster & Vector Centroid 1024-dim)]
        
        T_Topics & PostgresSilver --> EvolEngine[Mesin Evolusi Deret Waktu]
        EvolEngine --> T_Evol[(Tabel topic_evolution\nSkor Pertumbuhan & Akselerasi)]
        
        T_Topics & PostgresSilver --> ExpertiseEngine[Mesin Skoring Kepakaran Peneliti\nw1*Rel + w2*Prod + w3*Imp + w4*Rec]
        ExpertiseEngine --> T_Exp[(Tabel researcher_expertise\nExpertiseScore & H-index Topik)]
    end

    subgraph Serving [5. Lapisan Serving RAG Online - /api/v1/ask]
        VectorStore --> OnlineRAG[Mesin RAG Multi-Rute\nBatas FastAPI]
        GraphStore --> OnlineRAG
        PostgresSilver --> OnlineRAG
        Gold_Analytics --> OnlineRAG
    end
```

### 2.1 Pipeline Status-Tagged (Current vs Next — Sinkronisasi 2026-09-27)

Rantai pipeline konseptual normatif beserta status aktual tiap tahap. Tidak ada tahap yang boleh diklaim selesai sebelum implementasinya dieksekusi:

```text
Scopus Raw Data                    [DONE — sumber cleaning, arsip Bronze future]
    ↓
Cleaning                           [DONE — aturan §3, output terverifikasi]
    ↓
Cleaned / Exported Data            [DONE — 9 file data/*_cleaned.csv + ter-load di 9 tabel Silver]
    ↓
Data Preparation / Normalization   [DONE — Task 1a: seleksi & validasi 40 chunk siap-embed]
    ↓
Embedding Input / Text Construction[DONE — format Title+Abstract terkunci §4, dieksekusi di Task 1a]
    ↓
Embedding Generation               [DONE — Task 1b: BAAI/bge-m3 1024-dim, 40 chunk terproses]
    ↓
Vector                             [DONE — 40 vector padat 1024-dim dihasilkan]
    ↓
PostgreSQL + pgvector              [DONE — Task 1c & 1d: tersimpan di chunks.embedding + HNSW index aktif]
    ↓
RAG / Retrieval Layer              [NEXT (Phase 2-4) — Task 4/5/6/7: router, similarity search,
                                    context construction, LLM generation]
    ↓
E2E Testing                        [PENDING — Task 12, setelah seluruh pipeline terhubung]
```

**Pemetaan data per tahap (tanpa mengarang kolom/variabel baru):**

| Tahap | Data disimpan sebagai | Field / Kolom | Status |
|---|---|---|---|
| Structured data | 9 tabel Silver relasional | Seluruh kolom kanonikal `docs/04 §4` (teks + metadata + junction) | DONE (ter-load) |
| Input embedding | `chunks.chunk_text` + `publications.title` | Teks terkonstruksi `Title: {title}\nAbstract: {abstract}` (§4); `section = 'title_abstract'` | Desain DONE, eksekusi batch NEXT |
| Embedding / vector | `chunks.embedding vector(1024)` + metadata (`embedding_model`, `embedding_version`, `embedding_dimension`) | `BAAI/bge-m3`, `v1.0`, `1024` | DONE (40/40 terisi) |
| Kaitan vector ↔ artikel asal | `chunks.publication_id FK → publications.publication_id` + `chunk_id` | Join existing, tidak ada kolom baru | DONE (100% terhubung) |
| Konsumsi retrieval/RAG | `VectorRoute` (§5 docs/05) + `HybridRoute` | Cosine `<=>` HNSW, gate $\ge 0.65$, `DISTINCT ON (publication_id) LIMIT 8` | PENDING (menunggu vector terisi) |

- **Kapan embedding dibuat:** pekerjaan (job) batch offline Task 1b (`scripts/embed_chunks.py`), setelah Task 0 (verifikasi skema) dan Task 1a (preparation). Bukan waktu-nyata (real-time).
- **Di mana embedding disimpan:** kolom `chunks.embedding` di PostgreSQL + pgvector, indeks HNSW `idx_chunks_embedding_hnsw`.
- **Bagaimana vector dikaitkan kembali:** via `chunks.publication_id` ke `publications` untuk metadata sitasi `[Title, Year, DOI]` / `[Title, Year, no-doi]`.
- **Bagaimana vector digunakan retrieval/RAG:** `VectorRetriever` mengubah kueri menjadi embedding (meng-embed) dengan model yang sama lalu melakukan similarity search; di bawah ambang $\ge 0.65$ → `not_found` deterministik tanpa LLM.
- Field/variabel di luar yang sudah diputuskan di `docs/04`/`docs/05` berstatus **TBD / needs confirmation** — tidak dikarang dalam dokumen ini.

---

## 3. Pemrosesan Lapisan Silver (Pembersihan, Normalisasi, & Deduplikasi)

> **Status tahap: DONE.** Aturan di bawah sudah dieksekusi terhadap data Scopus; hasilnya ter-export sebagai 9 file `data/*_cleaned.csv` dan ter-load pada 9 tabel Silver kanonikal di PostgreSQL. Jangan tandai tahap ini sebagai pending.

### 3.1 Aturan Pembersihan & Normalisasi Casing (Aturan Cleaning & Lowercase)
Pembersihan (cleaning) dilakukan pada **lapisan transformasi Python** sebelum data disimpan ke tabel kanonikal:
- **Identifier (PK/FK)**: Casing & format asli dipertahankan (`publication_id`, `author_id`, `institution_id`, `doi`, `eid`, `grant_number`).
- **Teks Tampilan (Display Text)**: Casing asli dipertahankan (`author_name`, `institution_name`, `funding_agency`, `reference_text`).
- **Judul Publikasi**: **Titlecase** (`publications.title`).
- **Teks Naratif & Kategorikal**: **Lowercase murni** (`abstract`, `keyword`, `document_type`, `publication_stage`, `country`, `city`, `publisher`, `source`, `funding_text`).
- **Kolom Agregasi (`*_normalized`)**: **Lowercase + strip whitespace + strip punctuation** (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`, `topic_name_normalized`). Kolom ini wajib digunakan untuk kueri `GROUP BY` dan pencarian eksak guna menghindari fragmentasi variasi nama.

### 3.2 Strategi Deduplikasi Multi-Tier
1. **Tier 1**: Kesamaan DOI eksak (case-insensitive).
2. **Tier 2**: Kesamaan Scopus EID eksak.
3. **Tier 3**: Kesamaan hash kombinasi Normalized Title + Publication Year.
- *Resolusi Konflik (Conflict Resolution)*: Metadata record lama diperbarui jika record baru menyediakan atribut yang lebih lengkap, dan `citation_count` diperbarui ke nilai tertinggi (*snapshot* terbaru).

---

## 4. Pipeline Batch Embedding & Indeks HNSW (Lapisan Vector)

> **Status tahap: DONE (Task 1).** Sub-tahap normatif telah dieksekusi dan tervalidasi: **(1a)** Data Preparation (40 chunk siap-embed) → **(1b)** Embedding Generation (`BAAI/bge-m3`, 1024 dimensi) → **(1c)** Vector Storage (insert ke `chunks.embedding` + metadata) → **(1d)** HNSW index `idx_chunks_embedding_hnsw` (`m=16, ef_construction=64`) + `ANALYZE` → **(1e)** validasi (`COUNT WHERE embedding IS NULL = 0`, 0 orphan records).

- **Model:** `BAAI/bge-m3` (Hugging Face / sentence-transformers).
- **Dimensi Vektor:** $1024$ dimensi (*vector padat (dense) Float32*).
- **Gerbang Ambang Kosinus (Cosine Threshold Gate):** $\ge 0.65$.
- **Format Input Teks:**
  ```text
  Title: {title}
  Abstract: {abstract}
  ```
- **Ukuran Batch (Batch Size):** $32$ hingga $64$ chunk per batch (dioptimalkan untuk utilisasi CPU/RAM server).
> **Literature:** [[literature/2024 - BGE M3 Embedding]] · [[literature/2018 - HNSW Index]]
- **DDL & Indeks HNSW (`pgvector`) pada `chunks`:**
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  
  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(1024);
  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64) DEFAULT 'BAAI/bge-m3';
  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_version VARCHAR(32) DEFAULT 'v1.0';
  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_dimension SMALLINT DEFAULT 1024;
  
  CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
  ON chunks 
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
  
  ANALYZE chunks;
  ```

---

## 5. Pipeline Materialisasi Edge Graf Kolaborasi

Untuk mendukung kueri relasional jaringan kolaborasi tanpa membebani runtime kueri, skrip materialisasi idempoten dieksekusi pada tabel junction `pub_institution` dan `pub_author`:

```sql
-- Materialisasi Kolaborasi Institusi (Task 8)
TRUNCATE TABLE institution_collaboration;

INSERT INTO institution_collaboration (institution_a, institution_b, weight, via_publication_ids)
SELECT 
    LEAST(pi_a.institution_id, pi_b.institution_id) AS institution_a,
    GREATEST(pi_a.institution_id, pi_b.institution_id) AS institution_b,
    COUNT(DISTINCT pi_a.publication_id) AS weight,
    ARRAY_AGG(DISTINCT pi_a.publication_id::TEXT) AS via_publication_ids
FROM pub_institution pi_a
JOIN pub_institution pi_b 
     ON pi_b.publication_id = pi_a.publication_id
    AND pi_b.institution_id > pi_a.institution_id
GROUP BY 1, 2;

-- Materialisasi Co-authorship Penulis (Task 8)
TRUNCATE TABLE author_collaboration;

INSERT INTO author_collaboration (author_a, author_b, weight, via_publication_ids)
SELECT 
    LEAST(pa_a.author_id, pa_b.author_id) AS author_a,
    GREATEST(pa_a.author_id, pa_b.author_id) AS author_b,
    COUNT(DISTINCT pa_a.publication_id) AS weight,
    ARRAY_AGG(DISTINCT pa_a.publication_id::TEXT) AS via_publication_ids
FROM pub_author pa_a
JOIN pub_author pa_b 
     ON pa_b.publication_id = pa_a.publication_id
    AND pa_b.author_id > pa_a.author_id
GROUP BY 1, 2;
```

---

## 6. Pipeline Analitik Lapisan Gold (Pemodelan Topik & Skoring Kepakaran)

### 6.1 Pipeline Pemodelan Topik & Akselerasi Tren (`topics` & `topic_evolution`)
1. **Ekstraksi Klaster Topik (BERTopic / Co-word)**:
> **Literature:** [[literature/2022 - BERTopic]] · [[literature/1983 - Co-word Analysis]]
   - Mengelompokkan naskah berdasarkan representasi vektor `chunks.embedding` dan matriks kemunculan bersama kata kunci (`keywords`).
   - Menghasilkan 10 kata kunci representatif per topik (`cluster_keywords`).
   - Menghitung centroid vektor topik (`representation_vector vector(1024)`) sebagai rata-rata naskah anggota klaster.
2. **Perhitungan Metrik Evolusi Temporal (`topic_evolution`)**:
   - Menghitung agregasi publikasi dan sitasi per tahun untuk setiap topik dari `publications`.
   - **Laju Pertumbuhan (Growth Score)**:
     $$\text{GrowthScore}_t = \frac{N_{\text{pubs}, t} - N_{\text{pubs}, t-1}}{\max(1, N_{\text{pubs}, t-1})}$$
   - **Percepatan Sitasi (Citation Acceleration)**:
     $$\text{CitationAcceleration}_t = (\text{Cites}_t - \text{Cites}_{t-1}) - (\text{Cites}_{t-1} - \text{Cites}_{t-2})$$
   - **Klasifikasi Topik Berkembang (`is_emerging`)**: Ditetapkan `TRUE` jika $\text{GrowthScore}_t \ge 0.20$ dan $N_{\text{pubs}, t} \ge 10$.

### 6.2 Pipeline Pemeringkatan Kepakaran Peneliti (`researcher_expertise`)
Menghitung skor kepakaran multi-dimensi per kombinasi penulis dan topik riset:

$$\text{ExpertiseScore} = w_1 \cdot \text{Relevance} + w_2 \cdot \text{Productivity} + w_3 \cdot \text{Impact} + w_4 \cdot \text{Recency}$$

- **Konfigurasi Bobot:** $w_1 = 0.30$ (Relevansi Semantik), $w_2 = 0.25$ (Volume Publikasi Logaritmik), $w_3 = 0.25$ (Field-Weighted Citation Impact), $w_4 = 0.20$ (Kebaruan 3 Tahun Terakhir).
> **Literature:** [[literature/2005 - Hirsch h-index]]
- **Metrik Jaringan & H-Index:** Menghitung `h_index_topic` (h-index khusus publikasi dalam topik tersebut) dan `coauthor_network_size` (derajat sentralitas kolaborator aktif dalam topik).

---

## 7. Kriteria Penerimaan Desain Pipeline (Kriteria Penerimaan)

- [ ] **AC-PIPE-1**: Arsitektur Medallion (Bronze $\rightarrow$ Silver $\rightarrow$ Gold) didefinisikan secara lengkap dengan pemetaan tabel (§1 & §2).
- [ ] **AC-PIPE-2**: Aturan normalisasi dan pembersihan data Silver selaras dengan `04 Database Schema.md` pada 9 tabel kanonikal (§3).
- [ ] **AC-PIPE-3**: Pipeline batch embedding `BAAI/bge-m3` 1024-dimensi dan indeks HNSW (`m=16, ef=64`) terstandarisasi pada `chunks` (§4) dengan ambang $\ge 0.65$.
- [ ] **AC-PIPE-4**: Prosedur SQL materialisasi idempoten untuk 2 tabel edge kolaborasi graf telah dirumuskan dengan array `via_publication_ids` (§5).
- [ ] **AC-PIPE-5**: Pipeline Gold Layer untuk Topic Modeling (`topics`), Tren Waktu (`topic_evolution`), dan Skor Kepakaran Terbobot (`researcher_expertise`) didefinisikan dengan formula matematis eksplisit (§6).
- [ ] **AC-PIPE-6**: Seluruh respons kueri RAG dipastikan dapat menarik data terverifikasi dari lapisan (layer) Silver, Edge, dan Gold untuk mengisi `EvidenceObject` terstruktur (§2).

---

## 8. Matriks Konsistensi Keputusan (Lintas Dokumen)

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

## 9. Keputusan Arsitektur Kanonikal

1. **No-DOI Citation Decision:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Cosine Similarity Threshold Decision (`VectorRoute`):**
   - *Keputusan:* Nilai cosine similarity threshold dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri dengan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Post-MVP Graph Engine Decision:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 10. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/12 Data Pipeline.md` v3.6.2 | Aturan bahasa: narasi Indonesia, teknis Inggris (`Vector Storage`, `Dynamic 4-Route`, `Vector Similarity Gate`, dll) | Tanpa duplikasi bilingual; perbaiki terjemahan literal yang aneh |
| `docs/12 Data Pipeline.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/12 Data Pipeline.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE (9 file `data/*_cleaned.csv`); menambah pipeline status-tagged §2.1 (Raw → Cleaning → Export → Preparation → Embedding Input → Generation → Vector → pgvector → RAG → E2E) dan tabel pemetaan structured vs embedding vs linkage; menandai vector storage sebagai PENDING eksplisit | Sinkronisasi progress aktual 2026-09-27 |
| `docs/12 Data Pipeline.md` v3.4.0 | Mengembalikan seluruh target tabel pipeline dan DDL ke nama standar tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/12 Data Pipeline.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan penunjukan Apache AGE untuk Phase 9 | Menutup open decisions menjadi keputusan kanonikal |
| `docs/12 Data Pipeline.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumen di seluruh repository |
