"""Fast multi-threaded segmented downloader for large model weights.

Uses HTTP Range requests across 16 concurrent workers to maximize
download throughput for BAAI/bge-m3 pytorch_model.bin.
"""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import sys
import time
import urllib.request

TOTAL_SIZE = 2271145830  # Exact size of BAAI/bge-m3 pytorch_model.bin
CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB per chunk
NUM_WORKERS = 16
URL = "https://huggingface.co/BAAI/bge-m3/resolve/main/pytorch_model.bin"

SNAPSHOT_DIR = (
    pathlib.Path(os.environ.get("USERPROFILE", ""))
    / ".cache"
    / "huggingface"
    / "hub"
    / "models--BAAI--bge-m3"
    / "snapshots"
    / "5617a9f61b028005a4858fdac845db406aefb181"
)


def download_chunk(part_idx: int, start_byte: int, end_byte: int, out_file: pathlib.Path) -> tuple[int, int]:
    req = urllib.request.Request(
        URL,
        headers={
            "Range": f"bytes={start_byte}-{end_byte}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
    )
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if len(data) != (end_byte - start_byte + 1):
                    raise ValueError(f"Incomplete chunk {part_idx}: expected {end_byte-start_byte+1}, got {len(data)}")
                with open(out_file, "r+b") as f:
                    f.seek(start_byte)
                    f.write(data)
                return part_idx, len(data)
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Chunk {part_idx} failed after {max_retries} attempts: {e}") from e
            time.sleep(1 + attempt * 2)
    return part_idx, 0


def main() -> int:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    target_file = SNAPSHOT_DIR / "pytorch_model.bin"

    print(f"Target path: {target_file}")
    print(f"Total size: {TOTAL_SIZE / (1024*1024):.1f} MB ({TOTAL_SIZE} bytes)")

    if target_file.exists() and target_file.stat().st_size == TOTAL_SIZE:
        print("pytorch_model.bin already completely downloaded!")
        return 0

    # Pre-allocate file if not created
    if not target_file.exists() or target_file.stat().st_size != TOTAL_SIZE:
        print("Pre-allocating target file...")
        with open(target_file, "wb") as f:
            f.seek(TOTAL_SIZE - 1)
            f.write(b"\0")

    # Generate chunk ranges
    chunks = []
    part_idx = 0
    start = 0
    while start < TOTAL_SIZE:
        end = min(start + CHUNK_SIZE - 1, TOTAL_SIZE - 1)
        chunks.append((part_idx, start, end))
        part_idx += 1
        start = end + 1

    total_chunks = len(chunks)
    print(f"Divided into {total_chunks} chunks ({CHUNK_SIZE/(1024*1024)} MB each) using {NUM_WORKERS} workers.")

    start_time = time.time()
    downloaded_bytes = 0
    completed_chunks = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {
            executor.submit(download_chunk, idx, s, e, target_file): (idx, s, e)
            for idx, s, e in chunks
        }
        for future in concurrent.futures.as_completed(futures):
            idx, nbytes = future.result()
            downloaded_bytes += nbytes
            completed_chunks += 1
            elapsed = time.time() - start_time
            speed_mb = (downloaded_bytes / (1024 * 1024)) / max(0.001, elapsed)
            percent = (completed_chunks / total_chunks) * 100
            print(
                f"\r[{completed_chunks}/{total_chunks}] ({percent:.1f}%) "
                f"{downloaded_bytes/(1024*1024):.1f}/{TOTAL_SIZE/(1024*1024):.1f} MB "
                f"at {speed_mb:.2f} MB/s (elapsed: {elapsed:.0f}s)",
                end="",
                flush=True,
            )

    print(f"\nDownload completed in {time.time()-start_time:.1f} seconds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
