# SRD — Dokumen Kebutuhan Sistem (Consolidated Hybrid Master Blueprint)

**Versi Dokumen:** 3.6.0 (Consolidated Hybrid Master Blueprint)  
**Tanggal Status:** 2026-09-27  
**Menggantikan:** `02 SRD.md` Draft v2 s.d. v3.5.0  
**Konteks Otoritatif:** Selaras dengan `README.md` dan `docs/01` hingga `docs/12`  

> **Kontrak API & Realitas Infrastruktur:** Kontrak API target adalah `POST /api/v1/ask` dan `GET /api/v1/health` (lihat `docs/06 Api Design.md`). Seluruh modul aplikasi backend, pipeline embedding, dan antarmuka UI berstatus PLANNED / NOT IMPLEMENTED di repositori; basis data PostgreSQL prototipe yang memuat 9 tabel relasional kanonikal (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`) sudah dibuat dan siap pakai. **Cleaning Scopus dan cleaned export (`data/*_cleaned.csv`) DONE; generate + insert embedding/vector ke pgvector (Task 1) PENDING dan belum selesai.**

---

## 1. Kebutuhan Fungsional

### FR0 — Validasi Input & Identitas Request
- FR0.1: `question`: required, string, `min_length = 3`, `max_length = 1000`, trim whitespace.
- FR0.2: Malformed filters ditolak (422), bukan diabaikan diam-diam.
- FR0.3: Server men-generate `request_id` (uuid) per request dan mempropagasikannya ke HTTP response, logs, retrieval logs, LLM logs, dan errors.
- FR0.4: Log terstruktur minimum: `timestamp`, `request_id`, `route`, `status`, `latency_ms`, `error_code` (+ field NFR4).

### FR1 — Input Kueri
- FR1.1: Sistem menerima pertanyaan berbahasa natural (campuran Indonesia/Inggris, sesuai data yang juga multibahasa) via UI web.
- FR1.2: Sistem tidak mensyaratkan sintaks khusus — pengguna tidak perlu tahu nama tabel/kolom.

### FR2 — Routing Kueri
- FR2.1: Sistem mengklasifikasikan setiap pertanyaan ke salah satu dari 4 rute normatif: `SQLRoute` (agregasi/faktual ke 9 tabel Silver), `VectorRoute` (pencarian semantik pada `chunks.embedding`), `GraphRoute` (traversal graf kolaborasi pada tabel edge `institution_collaboration` dan `author_collaboration`), atau `HybridRoute` (analitik tren topik dan kepakaran Gold Layer `topics`, `topic_evolution`, `researcher_expertise` digabung dengan filter relasional/vektor).
- FR2.2: Klasifikasi dilakukan otomatis (lihat `docs/05 Retrieval Rag Design.md` §3 untuk metode).
- FR2.3: Output router berupa typed entity-contract (`RouterOutput`) yang divalidasi skema Pydantic v2, termasuk operator filter yang di-enumerasi (`eq`, `gt`, `gte`, `lt`, `lte`, `between`).
- FR2.4: Nama author/institusi dari pertanyaan WAJIB melalui entity resolution gate (`docs/05 Retrieval Rag Design.md` §3): 0 kandidat → `not_found`; >1 kandidat → `needs_clarification` dengan daftar kandidat; 1 kandidat → binding ke ID kanonikal. Tidak ada binding string mentah.

### FR3 — Jalur Terstruktur (Text-to-SQL / `SQLRoute`)
- FR3.1: Sistem membuat (generate) SQL SELECT read-only dari pertanyaan + skema 9 tabel relasional Silver (`publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks`).
- FR3.2: SQL divalidasi via AST parser Python `sqlglot` (parse, whitelist keyword, whitelist tabel/kolom) sebelum eksekusi.
- FR3.3: SQL yang gagal validasi tidak dieksekusi; sistem mencoba ulang (retry) 1x atau mengembalikan error terstruktur 422 (`sql_generation_failed`).
- FR3.4: Hasil kueri dibatasi row count. `LIMIT 50` wajib disisipkan untuk query **non-agregat**; agregat tunggal (`SELECT COUNT(*) ...`) tidak boleh diberi LIMIT.
- FR3.5: Pertanyaan dengan maksud agregat (berapa/jumlah/total/rata-rata/terbanyak) WAJIB menghasilkan query yang mengandung fungsi agregat atau `GROUP BY`.
- FR3.6: `COUNT` yang melibatkan join junction table WAJIB `COUNT(DISTINCT publication_id)` untuk mencegah double-count akibat fan-out.

### FR4 — Jalur Semantik (Vector RAG / `VectorRoute`)
- FR4.1: Pertanyaan di-embed dengan model embedding yang sama dengan saat indexing `chunks`: `BAAI/bge-m3` (1024-dim, Float32).
- FR4.2: Pencarian similaritas (similarity search; jarak Cosine pgvector `<=>`) mengembalikan top-K chunk paling relevan dari `chunks.embedding` menggunakan indeks HNSW (`m=16, ef_construction=64`).
- FR4.3: Chunks dikaitkan kembali ke `publications.publication_id` asal untuk metadata sitasi (`[Title, Year, DOI]` / `[Title, Year, no-doi]`).
- FR4.4: Hasil pencarian vector dideduplikasi menurut `publication_id` SEBELUM LIMIT — `DISTINCT ON (p.publication_id) LIMIT 8` berarti 8 publikasi unik, bukan baris chunk yang tumpang tindih.
- FR4.5: Gerbang ambang similaritas (similarity threshold gate): kueri dengan nilai kemiripan $< 0.65$ diarahkan ke `status: not_found`, top-K tidak dipaksakan.

### FR5 — Sintesis Jawaban
- FR5.1: LLM (`Qwen2.5-Coder-7B-Instruct` via Ollama) menyusun jawaban natural language murni sebagai mesin sintesis naratif/komparasi berdasarkan `EvidenceSet` — dilarang memproduksi angka mentah di luar objek bukti.
- FR5.2: Jawaban menyertakan sitasi formal terstandarisasi: `[Judul Publikasi, Tahun, DOI]` jika ada DOI, dan `[Judul Publikasi, Tahun, no-doi]` jika naskah tidak memiliki DOI.
- FR5.3: Bila retrieval menghasilkan 0 item bukti, sistem melakukan short-circuit deterministik mengembalikan `status: not_found` dalam <200 ms tanpa memanggil LLM.
- FR5.4: Setiap sitasi dalam teks jawaban diverifikasi pencocokannya secara post-hoc oleh `CitationVerifier`; sitasi fiktif dipangkas (strip) ke array metadata `unverified_citations`.

### FR6 — Tampilan UI
- FR6.1: Menampilkan jawaban + daftar sumber terpisah dan jelas.
- FR6.2: Menampilkan state visual eksplisit: `loading` (elapsed counter), `ok` (jawaban + chip metrik), `not_found` (banner netral), `needs_clarification` (kartu pilihan kandidat), dan `error` (pesan aman) (lihat `docs/07 UI Spec.md`).
- FR6.3: Menampilkan Dev Mode inspector panel untuk query SQL/CTE yang dieksekusi, latensi, dan reasoning rute jika `developer_mode: true`.

### FR7 — Jalur Relasional (Permukaan Minimum Knowledge-Graph / `GraphRoute`)
- FR7.1: Pertanyaan relasional (jaringan kolaborasi institusi, co-authorship penulis) diklasifikasikan ke rute `GraphRoute`.
- FR7.2: Retrieval relasional dieksekusi lewat **4 templat traversal recursive CTE terparameterisasi (T1–T4)** atas tabel edge derivatif `institution_collaboration` dan `author_collaboration` (`docs/04 Database Schema.md` §6) — bukan SQL yang digenerate oleh LLM. Kedalaman traversal dibatasi (`max_hops = 3`) dan hasil di-`LIMIT 50`.
- FR7.3: Setiap hasil relasional membawa array provenance `via_publication_ids` yang dapat ditelusuri ke publikasi bukti asal pada `publications`.
- FR7.4: Validasi jalur relasional setara jalur terstruktur: timeout 10 detik, role `app_readonly`, `search_path` terkunci ke `public`, dan entity gate.

---

## 2. Kebutuhan Non-Fungsional

### NFR1 — Kinerja (Performance)
- Target latensi end-to-end per kueri: < 15 detik pada MVP khusus CPU.
- **Asumsi konkurensi:** MVP menargetkan 1–2 request bersamaan di dalam budget 15 detik pada VM 8 vCPU/16GB (`docs/09 Tech Stack.md` §6).
- Indexing embedding berjalan sebagai batch job offline, tidak real-time.

### NFR2 — Kebenaran / Keter-groundingan (Correctness / Groundedness)
- Tidak ada jawaban yang tidak bisa ditelusuri balik ke row/chunk sumber.
- SQL yang dieksekusi harus selalu read-only (`SELECT` only, role DB `app_readonly` — lihat `docs/08 Security.md`).

### NFR3 — Keandalan (Reliability)
- Kegagalan satu jalur tidak boleh membuat seluruh request gagal total tanpa pesan — selalu ada fallback response yang informatif.

### NFR4 — Observabilitas (Observability)
- Setiap kueri dicatat (log) dengan field konkret: `request_id`, pertanyaan asli, rute terpilih, kontrak-entitas hasil router, hasil gerbang resolusi entitas, `answered_via_fallback`, SQL yang dieksekusi, skor similaritas hasil, threshold hit/miss, waktu eksekusi per tahap, dan sukses/gagal.

### NFR5 — Keamanan (Security)
- Kredensial DB dan API key tidak pernah ada di kode frontend/client-side.
- Lihat `docs/08 Security.md` untuk requirement lengkap.

### NFR6 — Keterpeliharaan (Maintainability)
- Skema database, prompt template, dan router logic didokumentasikan dan versioned di repo.
- Perubahan skema tabel harus disertai update ke system prompt text-to-SQL.

---

## 3. Batasan (Constraints)

- C1: Tidak ada budget GPU untuk fase MVP → LLM (`Qwen2.5-Coder-7B`) dan embedding (`BAAI/bge-m3`) harus CPU-capable.
- C2: Basis data PostgreSQL sudah tersedia dan terisi dataset prototipe (9 tabel kanonikal) — tidak membuat ulang database; kredensial tersimpan secara internal.
- C3: 9 tabel relasional kanonikal adalah baseline Silver Layer yang diberikan; kolom vektor `chunks.embedding` dan 2 edge tables akan ditambahkan sebagai struktur turunan tanpa mengubah data mentah yang ada.
- C4: Akses internal-only — tidak perlu compliance/privacy publik tingkat tinggi, tapi tetap wajib mengisolasi kredensial dan menerapkan role `app_readonly`.
- C5: Frontend Next.js (React) dan backend Python (FastAPI) dipisahkan secara tegas untuk menjamin modularitas dan keamanan.

---

## 4. Asumsi

- A1: **Pembedaan Skala Data Prototipe vs Produksi:**
  - **Fase Prototipe (Saat Ini):** Dataset berukuran kecil (20 publikasi, 40 chunk, 138 author, 107 institusi) digunakan untuk memverifikasi fungsionalitas end-to-end secara cepat dan deterministik.
  - **Indeks Vektor:** Indeks HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) tetap diwajibkan sejak Task 1 agar arsitektur dan rencana kueri (query plan) identik antara fase prototipe dan fase produksi.
  - **Fase Produksi (Masa Depan):** Skalabilitas akan diuji ulang ketika volume naskah mencapai >100K publikasi/chunks dengan automated batch ingestion.
- A2: Tim pengguna internal tidak akan mencoba secara aktif membobol sistem.
- A3: Bahasa pertanyaan mayoritas Inggris/Indonesia, sesuai bahasa data naskah.

---

## 5. Matriks Konsistensi Keputusan (Lintas Dokumen)

| Area Keputusan | Keputusan Kanonikal | Dokumen Terkait | Status |
|---|---|---|---|
| **Database** | PostgreSQL 15+ (sudah dibuat & siap pakai, kredensial internal aman) | `01`, `02`, `03`, `04`, `08`, `09`, `10`, `11` | ALIGNED |
| **Penyimpanan vector** | `pgvector` HNSW (`m=16, ef_construction=64`, `vector_cosine_ops`) pada `chunks.embedding vector(1024)` (PLANNED, Task 1) | `02`, `03`, `04`, `05`, `09`, `10`, `12` | ALIGNED |
| **Konvensi penamaan** | 9 tabel relasional kanonikal standar: `publications`, `authors`, `institutions`, `keywords`, `funding`, `pub_author`, `pub_institution`, `publication_references`, `chunks` | `01`, `02`, `03`, `04`, `05`, `06`, `10`, `11`, `12` | ALIGNED |
| **Pembersihan data (cleaning)** | Bronze → Silver via script Python — **DONE** (hasil pembersihan ter-export di `data/*_cleaned.csv`, 9 file; sudah ter-load di 9 tabel Silver) | `01`, `04`, `10`, `12` | ALIGNED |
| **Normalisasi lowercase** | Narasi & kategorikal (`abstract`, `keyword`, `country`, dll.) disimpan full lowercase; tampilan & ID asli dipertahankan; kolom `*_normalized` (`author_name_normalized`, `institution_name_normalized`, `funding_agency_normalized`) disimpan lowercase+trim+strip-punct untuk agregasi/pencarian | `01`, `02`, `04`, `05`, `12` | ALIGNED |
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

## 6. Keputusan Arsitektur Kanonikal

1. **Keputusan Sitasi Tanpa DOI:**
   - *Keputusan:* Format sitasi inline menggunakan pola baku `[Judul, Tahun, DOI]` jika DOI tersedia, dan `[Judul, Tahun, no-doi]` jika publikasi tidak memiliki DOI. Pola ini menjamin regex parser `CitationVerifier` dan parser frontend bekerja deterministik tanpa salah tafsir koma.
2. **Keputusan Ambang Batas Kesamaan Kosinus (`VectorRoute`):**
   - *Keputusan:* Nilai ambang batas kesamaan kosinus dikunci pada $\ge 0.65$ untuk model `BAAI/bge-m3`. Kueri yang menghasilkan nilai $< 0.65$ langsung diarahkan ke `status: not_found`.
3. **Keputusan Mesin Graf Pasca-MVP:**
   - *Keputusan:* MVP menggunakan Recursive CTE Terparameterisasi PostgreSQL (Templat T1–T4) pada tabel edge `institution_collaboration` dan `author_collaboration`. Untuk fase pasca-MVP (Fase 9), sistem menetapkan **Apache AGE** sebagai target evaluasi utama karena terintegrasi langsung sebagai ekstensi PostgreSQL tanpa memerlukan infrastruktur instance database graf terpisah.

---

## 7. Riwayat Perubahan

| Dokumen | Perubahan | Alasan |
|---|---|---|
| `docs/02 SRD.md` v3.6.0 | Sinkronisasi Bahasa Indonesia; tanpa perubahan keputusan teknis | Penyelarasan bahasa 2026-09-27 |
| `docs/02 SRD.md` v3.5.0 | Menandai cleaning + cleaned export sebagai DONE; menegaskan generate + insert embedding/vector ke pgvector PENDING | Sinkronisasi progress aktual 2026-09-27 |
| `docs/02 SRD.md` v3.4.0 | Mengembalikan seluruh nama tabel menjadi standar tanpa akhiran `_cleaned` (`publications`, `authors`, dll.) | Penyelarasan format penamaan sesuai instruksi project |
| `docs/02 SRD.md` v3.4.0 | Mengunci threshold kesamaan kosinus $\ge 0.65$ dan format sitasi `[Judul, Tahun, no-doi]` | Standardisasi fungsionalitas RAG dan parser |
| `docs/02 SRD.md` v3.4.0 | Memperbarui Matriks Konsistensi Keputusan dan Riwayat Perubahan | Menjamin konsistensi dokumen di seluruh repository |
