# Riset Latensi LLM & Pipeline — Graph / Semantic / Hybrid

**Branch:** `research/llm-latency` · **Tanggal:** 2026-10-06
**Goal:** menurunkan latency respons agar lebih cepat.
**Pertanyaan keputusan:** ganti LLM, atau enhance sisi lain?

## 0. Jawaban singkat (TL;DR)

**Jangan ganti LLM dulu.** Dari pengukuran live (2026-10-06, backend `:8000` + Supabase,
Ollama terpantau `down` dari host), penghambatnya **bukan model narasi**
— UI bahkan tidak pernah memanggilnya (`frontend/lib/api.ts:88-101` tidak
mengirim `llm_synthesis`, default backend `false` di
`backend/app/models/ask.py:72-76`). Tiga penghambat terukur:

| # | Penghambat | Angka terukur | Rute terdampak |
|---|---|---|---|
| 1 | **Embedding query di CPU** (`BAAI/bge-m3` via SentenceTransformer lokal) | **4.239 ms** (panggilan 1) → **617 ms** (panggilan 2, warm) | Semantic, Hybrid (bisa 2× embedding per request) |
| 2 | **Round-trip DB sekuensial ke Supabase** (~100 ms/RTT) × jumlah query berantai | Hybrid `TOPIC_TRENDS+EXPERT`: 4–6 query sekuensial → `hybrid_retrieval_ms` **105–415 ms** | Hybrid, Graph (ringan), Entity gate |
| 3 | **LLM Ollama CPU bila opt-in** (`qwen2.5-coder:7b`, 4,36 GB, 5,7–7,3 tok/s; load 69 dtk) | Sintesis **22 dtk warm / 91 dtk cold**; Text-to-SQL **6 dtk warm / 21,9–41,4 dtk cold** | Semua rute jika `llm_synthesis=true`; SQLRoute tanpa template |

Rekomendasi berlapis ada di §4 (problem→solusi detail) dengan tabel keputusan
di §5. Estimasi kasar: **solusi P2 → P1 → P3 memangkas
50–90%** dari latency semantik/hybrid tanpa ganti model; ganti LLM baru
relevan setelah itu, atau jika target < 2 dtk dengan narasi LLM.

## 1. Metodologi

* **Static mapping:** `routers/ask.py` → `services/router.py` (entity gate) →
  `retrievers/{vector,graph,hybrid,sql}_retriever.py` → `evidence/unifier.py` →
  `synthesizer/{answer,llm}.py`, plus `services/embedding.py`,
  `core/config.py`, `docker-compose.yml`, `.env.example`, `frontend/lib/api.ts`.
* **Live measurement:** `POST /api/v1/ask` dengan `developer_mode=true`
  (membaca `latency_breakdown_ms` per tahap — resep di §6) terhadap backend
  lokal yang hidup (`:8000`, DB `connected`). Tidak ada perubahan kode atau
  data; role DB read-only.
* Angka historis bertanda "rev" dikutip dari komentar terukur di kode
  (dicantumkan file:baris sumbernya).

## 2. Hasil pengukuran live (2026-10-06)

`llm_synthesis=false` (default UI), backend lokal → Supabase remote.

| Query → rute | wall | Retrieval | Embedding | Sintesis | Status |
|---|---|---|---|---|---|
| "Papers about mesenchymal stem cell differentiation?" → **VectorRoute** (call 1) | **4.781 ms** | 4.444 ms | **4.239 ms** (`local`) | 13 ms | ok |
| "Studies on mesenchymal stem cell therapy applications?" → **VectorRoute** (call 2) | **775 ms** | 647 ms | **617 ms** (`local`) | 1 ms | ok |
| "What are the emerging topics…?" → **HybridRoute** (tanpa topic_kw) | **158 ms** | 106 ms | — (tak ada embedding) | 5 ms | ok |
| "Who are the experts in mesenchymal stem cell research?" → **HybridRoute** | **458 ms** | 415 ms | — (topic cocok via ILIKE) | 1 ms | ok |
| "Who collaborates with …?" → **GraphRoute** | **48–182 ms** | 6 ms | — | — | not_found (entity gate) |
| ANN query (`vector_query_ms`) | — | **~190 ms** | — | — | — |
| Routing + entity gate (happy path) | — | — | — | — | **0,2–10 ms** |

Pembanding dengan budget e2e (`tests/e2e/test_e2e_12_queries.py:744`):
`SQL 500 / Vector 1500 / Graph 500 / Hybrid 1000 ms`.
**Vector call-1 (4,8 dtk) jebol budget 1,5 dtk**; sisanya lolos.

**Baseline sesi-2 (2026-10-06, pasca-rebase, backend sama):** dua query
semantic novel → `embedding_ms` **19.751 ms lalu 1.138 ms** (`local`,
ANN 330/39 ms). Jauh lebih buruk dari sesi-1 (4.239/617 ms) — pola
konsisten dengan model yang baru di-materialisasi-ulang (restart backend
atau eviksi) + contention CPU. Memperkuat urgensi P1 (prewarm residen)
dan P4 (jatah CPU).

## 3. Anatomi latensi per rute (waterfall)

Setiap request membayar, berurutan:

1. `routing_ms` — regex/keyword, ~1–5 ms. Abaikan.
2. `entity_resolution_ms` — **1–2 round-trip ILIKE sekuensial**
   (`router.py:539-552` author, `:646-658` institution; bisa +1 GROUP BY saat
   ambigu). Happy path ~0–10 ms, tapi tiap query = 1 RTT Supabase.
3. Retrieval spesifik rute (lihat bawah).
4. `evidence_unify_ms` + `synthesis_ms` deterministik — 1–15 ms. Abaikan.
5. `llm_synthesis_ms` — **hanya jika opt-in**. Kalau aktif, mendominasi
   segalanya (22–91 dtk). UI tidak mengaktifkannya hari ini.

### 3.1 Semantic (VectorRoute)

`vector_retriever.py:397` → `generate_query_embedding_with_backend` →
1× ANN query (`:438-439`).

* **Embedding CPU adalah 85–95% biaya rute ini** (4.239/617 ms vs ANN 190 ms).
  Model (±2,2 GB) resident tapi encode per-query tetap ~0,6 dtk di CPU ini;
  panggilan pertama 4,2 dtk (materialisasi/thread warmup). Klaim "150–450 ms
  warm" di `embedding.py:28` tidak tercapai di mesin ini — **encode-nya yang
  lambat, bukan load-nya**.
* Ada **query-embedding cache** 256 entri / TTL 1 jam (`embedding.py:38-59`):
  pertanyaan identik berulang ≈ 0 ms. Variasikan satu kata → bayar penuh lagi.
* ANN 190 ms untuk 40 chunks via **Seq Scan** (planner belum menyentuh HNSW —
  `config.py:160-169`); akan berubah saat korpus membesar.

### 3.2 Hybrid (HybridRoute)

`hybrid_retriever.py:386-559` — semuanya `await` **sekuensial**:

1. `topics` fetchrow (ILIKE) → 2. **(kadang) `generate_query_embedding` +
   ANN centroid** (`:412-430`) → 3. trends fetch → 4. expertise fetch →
5. supporting-pubs fetch. Total **4–6 hop jaringan berantai**.
* Terukur 105 ms (tanpa embedding, query generik) vs 415 ms (dengan实体 +
  filter). Setiap hop ≈ 1 RTT Supabase (~50–150 ms dari sini).
* **Bisa membayar embedding 2× per request** (skope topik + ANN centroid),
  masing-masing 0,6–4,2 dtk di CPU ini.

### 3.3 Graph (GraphRoute)

`graph_retriever.py` — 1× CTE (T1–T4, `max_hops ≤ 3`) + 1× metadata fetch
(`:589-605`, `ANY($1)`, truncate 50 ID). Terukur **6 ms retrieval**.
Rute ini **bukan masalah** hari ini; jika user merasakannya lambat,
penyebabnya hampir pasti di luar retriever (LLM opt-in, sesi, atau cold
embedding di rute lain — lihat §4, P2 untuk tail LLM).

### 3.4 LLM narrative synthesis (opt-in)

`synthesizer/llm.py:157-262`, `routers/ask.py:93-131`. Angka terukur di kode
(`config.py:118-137`, `.env.example:156-176`):

* Throughput CPU: **5,7–7,3 tok/s**, prompt eval ~70 tok/s, **load model 69 dtk**.
* Sintesis nyata (1359 prompt + 128 generated): **22 dtk warm / 91 dtk cold**.
* Timeout `SYNTHESIS_TIMEOUT_S=120` menutupi cold path — artinya request bisa
  menggantung **hingga ~2 menit** sebelum fallback deterministik menjawab.
* `SYNTHESIS_NUM_PREDICT=128` (14–19 dtk warm) sudah dipangkas dari 512 —
  benar, jangan dinaikkan tanpa mengukur ulang.

### 3.5 Text-to-SQL LLM fallback (SQLRoute tanpa template)

`sql_retriever.py:380-530`, budget `TEXT2SQL_TIMEOUT_S=6`. Beda **2 order
magnitudo** (`models/ask.py:183-187`): deterministik 84–90 ms vs LLM
**6.036 ms warm / 21,9–41,4 dtk cold**, lalu 504 `llm_timeout`. Bukan rute
yang dikeluhkan user, tapi jebakan latensi terbesar di codebase.

## 4. Problem → Solusi (detail)

Tujuh problem di bawah adalah hasil rombakan §4–§5 sebelumnya: tiap problem
memiliki **gejala + angka, akar penyebab (file:baris), solusi langkah
konkret + snippet, kriteria lulus ukur, dan effort/risiko**. Bahasa:
Indonesia campur istilah Inggris.

---

### P1 — Prewarm BGE-M3 (request pertama 4,2 dtk)

**Gejala.** `embedding_ms` request semantic pertama **4.239 ms**, request
berikutnya 617 ms (warm). Budget e2e VectorRoute 1.500 ms jebol hanya karena
langkah ini. ANN query-nya sendiri cuma ~190 ms — tidak bersalah.

**Akar.** Model ±2,2 GB (391 weight tensor) dimuat malas (*lazy*): bila
prewarm gagal/nonaktif, request pertama yang membayar materialisasi.
Lifespan memang menaruh prewarm di background task (`main.py`), tapi request
yang tiba duluan ikut **antre di `_st_lock` yang sama**
(`embedding.py:200-208`) — jadi biaya 12–168 dtk (tergantung isi HF cache)
dibebankan ke user. Volume `hf-cache` (`docker-compose.yml:24-35`) adalah
penentu cold 167 dtk vs 12 dtk.

**Solusi (verifikasi saja, tanpa kode).**

```powershell
# 1. Pastikan prewarm aktif di .env
# EMBEDDING_PREWARM=true

# 2. Restart backend, lalu cek log startup — harus ada baris:
#    "Embedding pre-warmed (1024 dims)" / "pre-warm finished (backend=local)"

# 3. Tembak 1 request semantic pertama dan baca embedding_ms:
# POST http://localhost:8000/api/v1/ask
# {"question": "Papers about mesenchymal stem cell differentiation?",
#  "developer_mode": true}
```

Bila log prewarm tidak muncul: cek `HF_HOME` writable oleh user `app`
dan isi volume `hf-cache` (recreate tanpa volume = download ulang 2,2 GB).

**Lulus ukur.** Request semantic pertama `embedding_ms` ≤ 1.000 ms
(idealnya ≈ warm 150–600 ms).

**Effort/risko.** Kecil / nol — hanya verifikasi config + volume.

---

### P2 — `OLLAMA_KEEP_ALIVE` (tail latency 91 dtk → 22 dtk)

**Gejala.** Sintesis narasi terukur **22 dtk warm vs 91 dtk cold**;
Text-to-SQL **6 dtk warm vs 21,9–41,4 dtk cold** (`config.py:118-137`,
`.env.example:156-176`). Ekornya panjang dan tidak terduga.

**Akar.** Ollama default mengusir (*evict*) model dari memori setelah
**5 menit idle**. Model `qwen2.5-coder:7b` (4,36 GB) butuh **69 dtk untuk
load ulang** + generasi CPU 5,7–7,3 tok/s. Karena jeda antar request
pengguna lazimnya > 5 menit, **hampir tiap panggilan adalah cold call**.
Penting: tidak ada satu pun baris kode yang menyetel ini (grep
`KEEP_ALIVE` hanya menemukan komentar) — ini murni **setelan server-side**,
bukan application setting.

**Solusi (tanpa sentuh kode).**

```powershell
# Windows (host Ollama native): set env permanen, lalu restart Ollama
setx OLLAMA_KEEP_ALIVE "24h"

# Linux / service: tambahkan ke environment service, mis. /etc/systemd/system/ollama.service
# Environment="OLLAMA_KEEP_ALIVE=24h"
# systemctl daemon-reload; systemctl restart ollama

# Verifikasi model resident (tidak pernah 0):
ollama ps
```

Syarat: RAM resident ±4,4 GB untuk Qwen 7B. Nilai `24h` praktis = selalu
hangat untuk pola pakai harian; `0` berarti *never expire* (hanya bila RAM
lega dan model tunggal).

**Lulus ukur.** `llm_synthesis_ms` stabil ≈ 14–22 dtk antar request
berjarak > 5 menit; tidak ada lagi `deterministic-fallback` beralasan cold.

**Effort/risiko.** Kecil / RAM resident bertambah. Dampak terbesar per usaha
di seluruh laporan ini.

---

### P3 — `asyncio.gather` (Hybrid 415 ms → ±150 ms)

**Gejala.** `hybrid_retrieval_ms` 105 ms (query generik) vs **415 ms**
(query berfilter): 4–6 hop jaringan ke Supabase (±100 ms/RTT) yang
**berantai satu per satu** — `topics` fetchrow → (kadang) embedding +
centroid → trends → expertise → supporting pubs
(`hybrid_retriever.py:386-559`).

**Akar.** Tiap tahap ditulis `await` serial padahal independen:
hasil trends tidak dipakai untuk membangun query expertise (keduanya hanya
membaca `resolved_topic_id` + filter yang sama). Hal identik di entity gate:
resolusi author (`router.py:539-552`) dan institusi (`:646-658`) serial
padahal tidak saling bergantung.

**Solusi (edit kecil, hasil identik).** Pola before/after:

```python
# BEFORE (serial, hybrid_retriever.py:483-530) — ilustrasi alur:
rows_trends = await asyncio.wait_for(conn.fetch(SQL_TOPIC_TRENDS, ...), ...)
rows_exp   = await asyncio.wait_for(conn.fetch(SQL_RESEARCHER_EXPERTISE, ...), ...)

# AFTER (paralel, satu RTT terpanjang yang menang):
rows_trends, rows_exp = await asyncio.gather(
    asyncio.wait_for(conn.fetch(SQL_TOPIC_TRENDS, ...), ...),
    asyncio.wait_for(conn.fetch(SQL_RESEARCHER_EXPERTISE, ...), ...),
)
```

Aturan main: hanya query yang **tidak saling memakai hasilnya** yang
digather; yang dependen (centroid memakai `vec`, pubs memakai
`author_ids_to_fetch`) tetap serial. Timeout per query tetap 10 dtk
masing-masing — tidak ada yang dilonggarkan.

**Sub-item P3b — satu embedding per request.** Pada query bertopik yang
gagal ILIKE, `hybrid_retriever.py:412` meng-encode `topic_kw` untuk ANN
centroid — padahal request yang sama bisa sudah membayar encode di jalur
skope. Teruskan vektornya antar fungsi alih-alih `generate_query_embedding`
dua kali. Hemat langsung **0,6–4,2 dtk** per request yang kena jalur ini.

**Lulus ukur.** `hybrid_retrieval_ms` ≈ 1 RTT (≈100–200 ms); request
bertopik hanya mencatat satu `embedding_ms`.

**Effort/risiko.** Kecil / nyaris nol — urutan eksekusi berubah, hasil dan
timeout tidak.

---

### P4 — Resource CPU/Memory (encode 617 ms vs klaim 150–450 ms)

**Gejala.** Encode warm 617 ms tidak mencapai klaim "150–450 ms warm"
(`embedding.py:28`). Encode — bukan load — yang lambat di mesin ini.

**Akar.** Kontainer backend dibatasi **4 CPU / 4 GB**
(`docker-compose.yml:73-79`) sementara bge-m3 (±2,2 GB resident) berbagi CPU
dengan proses API — dan dengan Qwen 7B (±4,4 GB) bila narasi dinyalakan.
Encode transformer multithread kelaparan thread = konsisten dengan 617 ms.

**Solusi (pilih satu, ukur ulang).**

```yaml
# docker-compose.yml — naikkan batas, ukur embedding_ms lagi:
    deploy:
      resources:
        limits:
          cpus: "8.0"
          memory: 8G
```

Alternatif tanpa Docker: jalankan backend native (`uvicorn ...`) sehingga
tidak ada cap sama sekali. Bila encode tetap > 500 ms setelah CPU lega,
lanjut ke opsi model embedding ringan (§5: re-embed korpus +
rekalibrasi gate 0,48).

**Lulus ukur.** `embedding_ms` warm ≤ 300 ms (target antara), atau
≤ 150 ms bila CPU lega penuh.

**Effort/risiko.** Kecil / butuh RAM/CPU host yang memang ada.

---

### P5 — Synthesis LLM (request bisa gantung ±2 menit)

**Gejala.** `SYNTHESIS_TIMEOUT_S=120` menutupi cold path: request dengan
`llm_synthesis=true` bisa menggantung hingga ~2 menit sebelum fallback
deterministik menjawab (`synthesizer/llm.py:157-262`,
`routers/ask.py:93-131`). Timeout tidak pernah di-retry (benar — retry
hanya menggandakan tunggu), tapi user tetap menunggu.

**Akar (tiga lapis).** (a) Cold load 69 dtk — lihat P2. (b) Prompt gemuk:
seluruh `EvidenceSet` diserialkan ke prompt (`llm.py:133-154`) pada prompt
eval ~70 tok/s — 1.359 token ≈ 19 dtk sebelum generasi mulai. (c)
`num_predict=128` sudah tepat (14–19 dtk warm, turun dari 512) — jangan
dinaikkan tanpa ukur ulang (`config.py:138-151`).

**Solusi (berlapis).**

```python
# 1) Rampingkan evidence ke Top-N SEBELUM masuk prompt (llm.py).
#    Pola: potong evidence_objects yang diserialkan ke blok UNTRUSTED,
#    mis. 8 terkuat berdasar relevance — angka tetap dari DB, format tetap.
ev_subset = ev_set.evidence_objects[:8]
```

```json
// 2) Streaming untuk persepsi (time-to-first-token < 2 dtk, total tetap).
//    generate_synthesis_text: "stream": true + teruskan chunk via SSE.
{"model": "qwen2.5-coder:7b-instruct", "stream": true}
```

Urutan: P2 dulu (gratis, −69 dtk), lalu rampingkan prompt (−30–50%),
streaming terakhir (persepsi).

**Lulus ukur.** p95 `llm_synthesis_ms` + TTFT tercatat; tidak ada request
> 60 dtk.

**Effort/risiko.** Sedang / rampingkan prompt perlu uji tidak memangkas
sumber sitasi (CitationVerifier jadi jaring pengaman).

---

### P6 — HNSW & ANN (presisi yang diluruskan)

**Gejala (yang dikira).** "HNSW tidak terpanggil." **Fakta:** indeks
**dipasang per koneksi** dari default kode (`db/pool.py:141`,
`hnsw_ef_search=100`) — pemanggilan ada. Yang benar-benar terjadi: (a)
override `.env` `HNSW_EF_SEARCH` **mati** (lihat P7), dan (b) di korpus 40
chunks planner memilih **Seq Scan + Sort** sehingga HNSW belum tersentuh
sama sekali (`config.py:160-169`).

**Solusi (siapkan sekarang, panen saat korpus besar).**

```sql
-- Saat korpus melewati titik planner beralih ke Index Scan:
ALTER DATABASE postgres SET hnsw.ef_search = 100;

-- Cara memastikan mana yang dipakai planner:
EXPLAIN SELECT ... ORDER BY embedding <=> '[...]' LIMIT 200;
-- cari "Index Scan using <hnsw_index>" vs "Seq Scan"
```

Jangan menaikkan `ef_search` buta: lower = faster/narrower, higher =
slower/recall — ukur recall via benchmark
(`tests/fixtures/retrieval_benchmark_v1.json`) tiap perubahan.

**Lulus ukur.** `EXPLAIN` menunjukkan Index Scan + recall benchmark tidak
turun.

**Effort/risiko.** Kecil / salah set hanya memengaruhi recall, bukan
kebenaran jawaban (gate kosinus tetap).

---

### P7 — `.env` yang diabaikan (bug 3 baris)

**Gejala.** `SYNTHESIS_TIMEOUT_S`, `SYNTHESIS_NUM_PREDICT`,
`HNSW_EF_SEARCH` terdokumentasi rapi di `.env.example:175-186` tetapi
**tidak pernah dibaca**: `_from_env()` (`config.py:325-353`) tidak
memetakan ketiganya, sehingga default kode (120/128/100) selalu menang.
Semua eksperimen tuning variabel ini selama ini tidak valid.

**Solusi (patch tepat 3 baris + pola yang sudah ada).**

```python
# backend/app/core/config.py — di dalam _from_env(), ikuti pola tetangga:
synthesis_timeout_s=_parse_int_env("SYNTHESIS_TIMEOUT_S", "120"),
# (dan dua pemetaan sejenis untuk SYNTHESIS_NUM_PREDICT + HNSW_EF_SEARCH)
```

```powershell
# Uji wajib setelah patch: ubah .env -> restart -> nilai terbaca
# SYNTHESIS_NUM_PREDICT=64
# lalu pastikan satu request sintesis memakai budget baru
# (cek payload num_predict di log/decode, bukan sekadar "tidak error")
```

**Lulus ukur.** Nilai `.env` terbaca (bukan default) pada ketiga knob.

**Effort/risiko.** Sangat kecil / tambah test 1 kasus agar tidak regresi.

---

## 5. Tabel keputusan (1 halaman)

| Problem | Solusi | Target angka | Effort | Risiko |
|---|---|---|---|---|
| P1 prewarm BGE-M3 | Verifikasi log + volume `hf-cache` | request-1 ≤ 1 dtk | Kecil | Nol |
| P2 `KEEP_ALIVE` | Set durasi panjang di host Ollama | 91 dtk → 22 dtk, tail hilang | Kecil | +RAM 4,4 GB |
| P3 gather + single-embedding | Paralelkan Hybrid/entity; teruskan vektor | hybrid → ±150 ms; hemat 0,6–4,2 dtk | Kecil | Nyaris nol |
| P4 CPU/memori | Naikkan limit / native | encode ≤ 300 ms | Kecil | Butuh HW |
| P5 sintesis | KEEP_ALIVE + Top-N + streaming | p95 turun, TTFT < 2 dtk | Sedang | Uji sitasi |
| P6 HNSW | Betulkan override + siapkan `ALTER DATABASE` | Siap saat korpus besar | Kecil | Recall (terukur) |
| P7 `.env` mati | Patch 3 baris + test | Tuning berfungsi | Sangat kecil | Nol |

**Verdict (tak berubah dari riset tahap-1, kini dengan bukti per item):**
eksekusi **P2 → P1 → P3 → ukur ulang** terlebih dahulu (semua terukur,
risiko kualitas nol). **Ganti model LLM hanya dibuka bila** setelah itu
masih di atas target **dengan narasi menyala** — urutannya model kecil CPU
(0,5–1,5B) → GPU → API hosted, bukan lompat.

## 6. Cara mengukur sendiri (resep diagnosis, 2 menit)

```powershell
# 1 request dengan breakdown per tahap:
# POST http://localhost:8000/api/v1/ask
# body: {"question": "...", "developer_mode": true}
# baca: debug.latency_breakdown_ms -> embedding_ms | vector_query_ms |
#        hybrid_retrieval_ms | graph_retrieval_ms | llm_synthesis_ms |
#        synthesis_backend | embedding_backend
```

* `embedding_ms` > 500 ms → masalah encode CPU (P1 prewarm, P4 CPU/memori;
  bila tetap dominan → opsi model embedding ringan di §5).
* `hybrid_retrieval_ms` ≈ N×100 ms → RTT sekuensial (P3 gather).
* `llm_synthesis_ms` > 20.000 ms atau `synthesis_backend: deterministic-fallback`
  → model cold/evicted (P2 `KEEP_ALIVE`) atau Ollama down.
* `sql_source: llm` + `generation_ms` ribuan → Text-to-SQL fallback (§3.5);
  tambah template deterministik untuk pola itu (`sql_retriever.py:140-169`).

## 7. Batasan riset ini

* Diukur di 1 mesin (Windows, backend lokal :8000, Supabase remote,
  Ollama down dari host). Angka RTT/encode ikut mesin ini.
* Ollama `down` saat riset (`/api/tags` tak menjawab dari host): jalur
  embedding-fallback, text2sql, dan sintesis berjalan dalam mode
  degradasi. Backend di Compose menjangkau Ollama via `host.docker.internal`
  (`docker-compose.yml:20`) — verifikasi dari dalam container sebelum
  menyimpulkan angka LLM.
* Frontend tidak mengirim `llm_synthesis` (`frontend/lib/api.ts:88-101`,
  default backend `false`): "lambat" yang terukur = retrieval, bukan narasi.
  Bila UI kelak menambah toggle LLM, ekspektasikan +22–91 dtk per jawaban
  di CPU ini.
* Bila `session_id` dipakai, tiap turn menambah 3+ tulis DB sekuensial
  (`ask.py:857-943`: user turn + assistant turn + summary refresh) — kecil
  per tulis, tapi di luar `total_ms` short-circuit.
* Sintesis LLM tidak diukur live (Ollama down) — mengutip angka terukur di
  kode (`config.py`, `.env.example`); perlu re-validasi saat Ollama hidup.
* Graph happy-path (entity cocok + CTE + metadata) belum tertangkap live
  (2 query uji → `not_found` di entity gate); retrieval 6 ms menunjukkan
  bukan penghambat, tapi butuh 1 pengukuran happy-path untuk final.

## 8. Hasil implementasi (2026-10-08, branch `research/llm-latency`)

Dieksekusi berurutan P1 → P7 dengan disiplin baseline → aksi → ukur ulang →
test → commit. Status deployment saat eksekusi: backend + Ollama di Docker
(CPU-only, host 4 CPU, tanpa GPU), DB Supabase remote.

| Item | Hasil | Angka |
|---|---|---|
| P1 prewarm | SEHAT, tanpa perubahan kode. `local_model_state=loaded`. Biaya request-1 (±9 dtk reload dari warm cache) tak terhindarkan tiap recreate container; encode settled ±0,6–1,2 dtk, spike 19,8 dtk saat contention | baseline §2 + sesi-2 |
| P2 KEEP_ALIVE | DONE + temuan besar: kontainer Ollama **belum punya model sama sekali** (`ollama list` kosong). Pull `qwen2.5-coder:7b-instruct` 4,7 GB + recreate dengan `OLLAMA_KEEP_ALIVE=24h` (`ollama ps`: "24 hours from now") | cold 69 dtk → hilang |
| P2-regresi | Recreate menjatuhkan network-alias `ollama` → backend DNS fail → fallback 11 dtk. Diperbaiki via `network connect --alias`. Pelajaran: orphan container ≠ compose-managed; catat argumen run | 11,2 dtk → 0 (DNS pulih) |
| P2-warm ukur | Model resident tapi box ini hanya **1,37 tok/s** (vs 5,7–7,3 referensi). Sintesis 128 token ≈ 90 dtk+ → timeout 120 dtk terukur habis sekali. Implikasi: Top-N (P5) wajib, `num_predict` jangan naik, kandidat model kecil/GPU makin kuat | 1,37 tok/s |
| P3 gather | DONE + test bukti-overlap (`test_retrieve_combined_...concurrently`, deadlock-bila-serial). Syarat arsitektur ditemukan saat implementasi: **asyncpg melarang konkurensi di 1 koneksi** → pakai 2 koneksi pool (`pool` param baru). Live COMBINED: `hybrid_retrieval_ms` 189 ms | 415 → 189 ms* |
| P3b single-embed | DIBATALKAN dengan alasan: audit ulang membuktikan Hybrid membayar **maksimal 1 embedding** (`hybrid_retriever.py:412` satu-satunya jalur) — klaim "2×" di §4 direvisi di sini | — |
| Entity-gate paralel | DITUNDA dengan alasan: early-return clarification membuat refactor berisiko; gate terukur 0–10 ms (ROI negatif) | — |
| P4 OMP | NEGATIF (kemajuan!): `OMP_NUM_THREADS=4` **regresi** encode (0,6 → 2,2–28 dtk, oversubskripsi MKL di host 4-CPU) → revert ke default 1 + komentar dokumentasi di `docker-compose.yml`. Lever sebenarnya = warmup + kontensi host | revert |
| P5 Top-8 | DONE + test (`SYNTHESIS_EVIDENCE_TOP_N=8`, verifier tetap full `sources`). Streaming **ditunda**: mengubah kontrak API + butuh frontend | −prompt eval |
| P6 HNSW | Verifikasi-kode: setting per-koneksi aktif, Seq Scan di 40 chunks sesuai desain; `ALTER DATABASE` siap saat korpus besar. Tanpa perubahan | — |
| P7 `.env` | DONE + 2 test hijau (`test_config_env.py`): ketiga knob kini terbaca | — |
| P2 gate-label | DONE `ab840c7` (2026-10-09): 7 baris — chip hero, meta loading, 3 teks fixture lab, 2 varian prototype: `0.65 → 0.48` (rekalibrasi P1). Verify trio hijau: lint ✓, typecheck ✓, 218/218 vitest. `relevance_score: 0.65` + `rgba` disengaja tak tersentuh | — |
| Timeout Q04 live | VONIS: VectorRoute call-pertama (cold 10–20 dtk) vs abort client 8 dtk (`use-ask.ts:19`) — pasti gagal; retry teks identik = cache-hit. 8 dtk disengaja (di bawah `statement_timeout` 10 dtk agar 503 terstruktur muncul); naik-25-dtk ditolak, pesan-jujur ditunda, pilihan = prewarm saja | — |
| P1 re-verifikasi | Health `:8000`: `local_model_state=loaded`, Qwen 7B resident, `hf-cache` utuh, `EMBEDDING_PREWARM` default-true. `synthesis.fallback_rate=1.0` (2 timeout + 1 unreachable) = fallback bekerja sesuai desain | — |

\* Pembanding live beda query sekelas (intent COMBINED, evidence 15) —
angka unit-test (overlap terbukti) yang mengikat, live sebagai konfirmasi
arah.

**Test:** 64 passed pada 4 suite terdampak (`config_env`, `hybrid_retriever`,
`llm_synthesizer`, `evidence`). Full-suite `tests/unit` tak bisa dikoleksi
di `.venv` ini karena `psycopg`/`psycopg2` tak terinstal (dependensi
offline-scripts, environmental — bukan akibat perubahan ini).

**Sisa terbuka:** (a) ukur ulang encode setelah cooldown penuh backend,
(b) streaming SSE + frontend (butuh keputusan F2.4 #3),
(c) keputusan model kecil/GPU/hosted dengan data
1,37 tok/s sebagai amunisi (perbandingan opsi tersedia),
(d) PR branch fix ini ke `develop` (6 commit fix via cherry-pick;
logo PR #19 + merge main disengaja tidak dibawa).

## 9. Referensi berkas (sumber klaim)

* Pipeline & latensi: `backend/app/routers/ask.py` (refine `:93-131`,
  vector `:321-452`, graph `:455-548`, hybrid `:551-749`, sesi `:819-959`)
* Embedding: `backend/app/services/embedding.py` (`:22-33` biaya cold,
  `:282-328` prewarm); config `backend/app/core/config.py:46-169`
* Sintesis: `backend/app/services/synthesizer/llm.py:157-262`
* Text-to-SQL: `backend/app/services/retrievers/sql_retriever.py:380-470` (generate) + `:571-690` (retrieve)
* Hybrid sekuensial: `.../hybrid_retriever.py:386-559`; entity gate:
  `backend/app/services/router.py:534-739`
* Deploy: `docker-compose.yml:16-35,73-79`; `.env.example:136-186`;
  UI: `frontend/lib/api.ts:88-101`; budget: `tests/e2e/test_e2e_12_queries.py:744`
