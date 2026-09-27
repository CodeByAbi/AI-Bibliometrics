# Tumpukan Teknologi — Rekomendasi & Rasional (Consolidated Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.0 (Consolidated Hybrid Master Blueprint)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `09 Tech Stack.md` Draft v2 s.d. v3.5.0  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  

> **Status Implementasi & Realitas Stack (Sinkronisasi Progress 2026-09-27):**  
> 1. **Database PostgreSQL:** Basis data PostgreSQL **sudah dibuat dan siap pakai**, memuat **dataset prototipe kecil** (~20 publikasi, 40 chunk, 138 author, 107 institusi) pada 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) untuk validasi end-to-end. Kredensial diamankan secara internal.  
> 2. **Implementasi Aplikasi:** Direktori aplikasi (`backend/`, `frontend/`, `docker/`, `scripts/`, `tests/`) berstatus **PLANNED / NOT YET IMPLEMENTED** dan dikunci oleh keputusan arsitektur di bawah ini.
> 3. **Sinkronisasi Progress 2026-09-27:** Cleaning Scopus dan cleaned export (`data/*_cleaned.csv`) **DONE**; generate + insert embedding `BAAI/bge-m3` ke pgvector (Task 1) **PENDING**.

---

## 1. Ringkasan Stack

| Lapisan | Pilihan | Alasan singkat |
|---|---|---|
| **Database** | PostgreSQL 15+ (sudah ada & siap pakai) + pgvector | Sudah memuat 9 tabel kanonikal, tidak perlu migrasi atau setup DB baru |
| **Backend** | Python 3.11+ & FastAPI | Ekosistem RAG, validasi AST SQL (`sqlglot`), embedding, dan I/O async paling matang |
| **LLM (mandiri)** | Qwen2.5-Coder-7B-Instruct (GGUF Q4_K_M) via Ollama | Model terbaik di kelas 7B untuk text-to-SQL dan sintesis terstruktur di CPU |
| **Embedding** | BAAI/bge-m3 (1024-dim, Float32) | Multibahasa (EN/ID), representasi semantik berkualitas tinggi, layak di CPU |
| **Validasi SQL** | `sqlglot` | Parser AST Python yang kokoh untuk pemeriksaan daftar putih tabel kanonikal dan keamanan |
| **Frontend** | Next.js (React) | Tata letak padat (dense) Notion/Linear, tabular monospace, visualisasi `evidence_objects` |
| **Deployment** | Docker Compose (Single Host VPS) | Backend FastAPI + Ollama dalam container, frontend di Vercel |

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

---

## 4. Backend: Python/FastAPI — Pemisahan dari Next.js

Ekosistem parsing AST (`sqlglot`), embedding (`sentence-transformers`), dan orkestrasi bukti jauh lebih alami (native) dan aman di Python. FastAPI dipilih karena mendukung I/O async penuh untuk koneksi database PostgreSQL (`asyncpg`) dan Ollama, serta menyediakan validasi skema ketat via Pydantic v2.

---

## 5. Rasional: Mengapa Tanpa Framework RAG Berat (LangChain/LlamaIndex)

Framework seperti LangChain/LlamaIndex dihindari untuk MVP karena lapisan abstraksi yang berlebih menyulitkan debugging pada model 7B CPU. Memanggil Ollama via klien HTTP standar dan PostgreSQL via SQL mentah (raw) terparameterisasi jauh lebih transparan, mudah diaudit, dan aman terhadap kegagalan sintaks.

---

## 6. Pengembangan Lokal & Deployment

- **Dev lokal**: Docker Compose dengan service `backend` dan `ollama`, terhubung langsung ke database PostgreSQL prototipe.
- **MVP Produksi**: Single VPS (8 vCPU, 16GB RAM) menjalankan Docker Compose yang sama, melayani konkurensi 1–2 request dalam batas latensi 15 detik.
- **Frontend**: Next.js di-deploy via Vercel dengan environment variable menunjuk ke URL backend FastAPI.

---

## 7. Strategi Teknologi Graf

- **Fase MVP**: Penelusuran jaringan kolaborasi dijalankan via **Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4)** pada tabel edge `institution_collaboration` dan `author_collaboration`.
- **Fase Pasca-MVP (Fase 9)**: Evaluasi graf lanjutan menetapkan **Apache AGE** sebagai ekstensi native PostgreSQL pilihan untuk mengeksekusi kueri openCypher langsung di dalam database PostgreSQL tanpa memerlukan runtime database graf terpisah.

---

## 8. Matriks Konsistensi Keputusan (Lintas Dokumen)

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

## 9. Keputusan Arsitektur Kanonikal

1. **Keputusan Sitasi Tanpa DOI:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Keputusan Ambang Batas Kesamaan Kosinus (`VectorRoute`):**
   - *Keputusan:* Nilai ambang batas kesamaan kosinus dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri yang menghasilkan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Keputusan Mesin Graf Pasca-MVP:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 10. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/09 Tech Stack.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/09 Tech Stack.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; embedding pgvector PENDING | Sinkronisasi progress aktual 2026-09-27 |
| `docs/09 Tech Stack.md` v3.4.0 | Menyelaraskan referensi stack basis data dengan 9 tabel kanonikal tanpa akhiran `_cleaned` | Penyelarasan format penamaan sesuai instruksi project |
| `docs/09 Tech Stack.md` v3.4.0 | Mengunci keputusan format sitasi (`no-doi`), threshold kosinus $\ge 0.65$, dan strategi graf Apache AGE | Menutup open decisions menjadi keputusan kanonikal |
| `docs/09 Tech Stack.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin standarisasi dokumentasi di seluruh repository |
