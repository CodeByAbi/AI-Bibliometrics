"""Direct streaming downloader for BAAI/bge-m3 pytorch_model.bin."""

import os
import pathlib
import sys
import time
import urllib.request

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
TARGET = SNAPSHOT_DIR / "pytorch_model.bin"
def download():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if TARGET.exists() and TARGET.stat().st_size == 2271145830:
        print("Target already exists and is complete.")
        return 0

    max_retries = 20
    total_size = 2271145830

    for attempt in range(max_retries):
        downloaded = 0
        mode = "wb"
        if PART.exists():
            downloaded = PART.stat().st_size
            mode = "ab"
            if downloaded >= total_size:
                print("Part file already complete. Renaming...")
                PART.rename(TARGET)
                return 0
            print(f"Resuming download from byte {downloaded} ({downloaded/(1024*1024):.1f} MB)...")

        req = urllib.request.Request(
            URL,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )
        if downloaded > 0:
            req.add_header("Range", f"bytes={downloaded}-")

        print(f"Attempt {attempt+1}/{max_retries}: Connecting to {URL}...")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                start_time = time.time()
                last_log = start_time
                bytes_in_interval = 0

                with open(PART, mode) as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        bytes_in_interval += len(chunk)

                        now = time.time()
                        if now - last_log >= 5.0:
                            speed = (bytes_in_interval / (1024 * 1024)) / max(0.001, (now - last_log))
                            pct = (downloaded / total_size * 100)
                            print(
                                f"[{pct:5.1f}%] {downloaded/(1024*1024):.1f}/{total_size/(1024*1024):.1f} MB "
                                f"({speed:.2f} MB/s) elapsed: {now-start_time:.0f}s"
                            )
                            last_log = now
                            bytes_in_interval = 0

            if downloaded >= total_size:
                print(f"Download complete: {downloaded} bytes.")
                PART.rename(TARGET)
                print("Renamed part to target.")
                return 0
        except Exception as exc:
            print(f"Attempt {attempt+1} interrupted: {exc}. Retrying in 2 seconds...")
            time.sleep(2)

    return 1
    print(f"Download complete: {downloaded} bytes.")
    PART.rename(TARGET)
    print("Renamed part to target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(download())
