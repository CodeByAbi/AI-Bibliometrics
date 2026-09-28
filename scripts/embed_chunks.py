"""Task 1 — Batch Chunk Embedding via BAAI/bge-m3 (Phase 1).

Generates 1024-dimensional dense Float32 embeddings for document chunks
in the `chunks` table using BAAI/bge-m3 and stores them in PostgreSQL pgvector.

Resume behavior (DB is source of truth):
    - Results are committed per persist-batch (see --commit-every).
    - Re-running the same command after a disconnect (WiFi drop, Ctrl+C,
      Supabase pooler timeout) automatically skips rows where
      `embedding IS NOT NULL` and continues from the latest committed batch.
    - No local checkpoint file is used.

Usage:
    python scripts/embed_chunks.py [--model BAAI/bge-m3] [--batch-size 32]
        [--commit-every 100] [--max-retries 5] [--resume] [--force]
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, TypeVar

# Ensure project root is in sys.path
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.db import get_db_connection, safe_dsn_label  # noqa: F401  (kept for log debugging)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("embed_chunks")

T = TypeVar("T")

_RETRY_KEYWORDS = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "reset",
    "broken pipe",
    "temporarily",
    "try again",
    "pool",
    "pooler",
    "ssl",
    "socket",
    "eof",
    "502",
    "503",
    "504",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch embedding for publication chunks.")
    parser.add_argument(
        "--model",
        default="BAAI/bge-m3",
        help="Embedding model identifier (default: BAAI/bge-m3)",
    )
    parser.add_argument(
        "--version",
        default="v1.0",
        help="Embedding version tag (default: v1.0)",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=1024,
        help="Expected embedding dimension (default: 1024)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Embedding encode batch size (default: 32)",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=100,
        help="Persist-batch size: rows encoded+committed per iteration (default: 100)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries for transient network/DB failures per operation (default: 5)",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=2.0,
        help="Base backoff in seconds for retries, exponential (default: 2.0)",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume embedding, skipping already embedded chunks (default: True; use --no-resume to disable)",
    )
    parser.add_argument(
        "--allow-ollama-fallback",
        action="store_true",
        default=False,
        help="Allow silent fallback to Ollama embeddings if SentenceTransformer load fails (default: False, fail fast)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-embedding of all chunks, ignoring existing embeddings",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of chunks to process",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Torch computation device (default: cpu)",
    )
    return parser.parse_args()


def construct_input_text(title: str, chunk_text: str) -> str:
    """Construct canonical embedding input text per docs/12 §4 & docs/09 §2.

    Format:
        Title: {title}
        Abstract: {chunk_text}
    """
    clean_title = (title or "").strip()
    clean_chunk = (chunk_text or "").strip()
    return f"Title: {clean_title}\nAbstract: {clean_chunk}"


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient network/DB failures (WiFi drop, pooler timeout)."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, (urllib.error.URLError, urllib.error.HTTPError, OSError)):
        return True
    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    if "operationalerror" in name or "connection" in name or "timeout" in name:
        return True
    if "psycopg" in module and ("operational" in name or "connection" in name or "timeout" in name):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _RETRY_KEYWORDS)


def _with_retry(
    op_name: str,
    fn: Callable[[], T],
    *,
    max_retries: int,
    backoff: float,
) -> T:
    """Run fn() with exponential-backoff retry on transient failures."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            last_exc = exc
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            sleep_s = backoff * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (attempt %d/%d, retryable: %s). Retrying in %.1fs...",
                op_name,
                attempt,
                max_retries,
                exc,
                sleep_s,
            )
            time.sleep(sleep_s)
    assert last_exc is not None  # for type checkers
    raise last_exc


def _fetch_batch(
    *,
    last_id: Any | None,
    batch_limit: int,
    apply_null_filter: bool,
    max_retries: int,
    backoff: float,
) -> list[tuple]:
    """Fetch one persist-batch with keyset pagination, retrying transient DB errors."""

    def _do() -> list[tuple]:
        conditions: list[str] = []
        params: list[Any] = []
        if apply_null_filter:
            conditions.append("c.embedding IS NULL")
        if last_id is not None:
            conditions.append("c.chunk_id > %s")
            params.append(last_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT
                c.chunk_id,
                c.publication_id,
                c.chunk_text,
                COALESCE(p.title, '') AS title
            FROM chunks c
            LEFT JOIN publications p ON p.publication_id = c.publication_id
            {where}
            ORDER BY c.chunk_id
            LIMIT %s
        """
        params.append(batch_limit)
        with get_db_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                return cur.fetchall()

    return _with_retry("fetch_batch", _do, max_retries=max_retries, backoff=backoff)


def _persist_batch(
    update_data: list[tuple],
    *,
    max_retries: int,
    backoff: float,
) -> None:
    """Persist one batch with its own transaction; each call commits independently."""
    update_sql = """
        UPDATE chunks
        SET
            embedding = %s,
            embedding_model = %s,
            embedding_version = %s,
            embedding_dimension = %s
        WHERE chunk_id = %s;
    """

    def _do() -> None:
        with get_db_connection(autocommit=False) as conn:
            try:
                with conn.cursor() as cur:
                    cur.executemany(update_sql, update_data)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    _with_retry("persist_batch", _do, max_retries=max_retries, backoff=backoff)


def _ollama_base() -> str:
    """Ollama base URL honoring OLLAMA_HOST (Docker service name) over localhost."""
    import os

    try:
        from backend.app.core.config import get_settings

        return get_settings().ollama_host.rstrip("/")
    except Exception:
        return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _embed_batch_ollama(
    *,
    ollama_model: str,
    texts: list[str],
    chunk_ids: list[Any],
    max_retries: int,
    backoff: float,
) -> list[list[float]]:
    """Embed one batch via Ollama with per-item retry (honors OLLAMA_HOST)."""
    base = _ollama_base()
    vectors: list[list[float]] = []
    for i, text in enumerate(texts):
        def _do_single() -> list[float]:
            req_data = json.dumps({"model": ollama_model, "prompt": text}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/api/embeddings",
                data=req_data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
            vec = resp_json.get("embedding")
            if not vec:
                raise ValueError(f"Ollama returned empty embedding for chunk {chunk_ids[i]}")
            return list(vec)

        vec = _with_retry(
            f"ollama_embed[{chunk_ids[i]}]",
            _do_single,
            max_retries=max_retries,
            backoff=backoff,
        )
        vectors.append(vec)
    return vectors


def main() -> int:
    args = parse_args()
    if args.commit_every < 1:
        logger.error("--commit-every must be >= 1, got %d", args.commit_every)
        return 1
    logger.info("Initializing batch embedding pipeline (resumable, per-batch commit)...")
    logger.info(
        "Configuration: model=%s version=%s dimension=%d batch_size=%d commit_every=%d "
        "max_retries=%d retry_backoff=%.1f device=%s force=%s limit=%s",
        args.model,
        args.version,
        args.dimension,
        args.batch_size,
        args.commit_every,
        args.max_retries,
        args.retry_backoff,
        args.device,
        args.force,
        args.limit,
    )

    apply_null_filter = (not args.force) and args.resume

    # 1. Initial stats (DB is source of truth for resume).
    try:
        with get_db_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks;")
                total_chunks = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL;")
                existing_embeddings = cur.fetchone()[0]
                if apply_null_filter:
                    cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;")
                    pending_total = cur.fetchone()[0]
                else:
                    pending_total = total_chunks
                try:
                    cur.execute(
                        "SELECT DISTINCT embedding_model, embedding_version "
                        "FROM chunks WHERE embedding IS NOT NULL LIMIT 5;"
                    )
                    prior = cur.fetchall()
                except Exception:
                    prior = []
        if prior:
            for pm, pv in prior:
                if pm != args.model or pv != args.version:
                    logger.warning(
                        "Existing embeddings use model=%s version=%s, "
                        "but this run uses model=%s version=%s. "
                        "Mixing versions may degrade retrieval quality. "
                        "Use --force to re-embed consistently.",
                        pm,
                        pv,
                        args.model,
                        args.version,
                    )
                    break
    except Exception as exc:
        logger.error("Failed to read initial database stats: %s", exc)
        return 1

    logger.info(
        "Database status: total_chunks=%d already_embedded=%d pending_to_embed=%d",
        total_chunks,
        existing_embeddings,
        pending_total,
    )
    if pending_total == 0:
        logger.info("No chunks pending embedding. All records up to date.")
        return 0

    # 2. Load embedding backend ONCE, reuse across batches.
    st_model = None
    use_ollama = False
    ollama_model = "bge-m3" if "bge-m3" in args.model.lower() else args.model
    try:
        logger.info("Loading SentenceTransformer for %s (device: %s)...", args.model, args.device)
        from sentence_transformers import SentenceTransformer

        st_model = SentenceTransformer(args.model, device=args.device)
        logger.info("SentenceTransformer loaded successfully.")
    except Exception as exc:
        if not args.allow_ollama_fallback:
            logger.error(
                "SentenceTransformer load failed (%s). Refusing silent backend switch "
                "(embeddings would mix models). Re-run with --allow-ollama-fallback to use Ollama '%s'.",
                exc,
                ollama_model,
            )
            return 1
        logger.warning(
            "SentenceTransformer load encountered issue (%s). Using Ollama backend '%s'...",
            exc,
            ollama_model,
        )
        use_ollama = True
        logger.info("Connecting to Ollama at %s with model '%s'...", _ollama_base(), ollama_model)

    # 3. Encode + persist loop with keyset pagination and per-batch commit.
    last_id: Any | None = None
    processed_total = 0
    batch_no = 0
    total_embed_s = 0.0
    total_persist_s = 0.0
    first_sample_logged = False

    try:
        while True:
            if args.limit is not None:
                remaining_budget = args.limit - processed_total
                if remaining_budget <= 0:
                    break
                batch_limit = min(args.commit_every, remaining_budget)
            else:
                batch_limit = args.commit_every

            rows = _fetch_batch(
                last_id=last_id,
                batch_limit=batch_limit,
                apply_null_filter=apply_null_filter,
                max_retries=args.max_retries,
                backoff=args.retry_backoff,
            )
            if not rows:
                break  # no more pending rows → done

            batch_no += 1
            chunk_ids = [r[0] for r in rows]
            input_texts = [construct_input_text(title=r[3], chunk_text=r[2]) for r in rows]

            if not first_sample_logged:
                logger.info(
                    "Sample input text for %s:\n---\n%s\n---",
                    chunk_ids[0],
                    input_texts[0][:250] + ("..." if len(input_texts[0]) > 250 else ""),
                )
                first_sample_logged = True

            # 3a. Encode this batch only (memory-bounded, disconnect loses at most 1 batch).
            start_embed = time.perf_counter()
            if not use_ollama:
                assert st_model is not None

                def _do_st() -> Any:
                    return st_model.encode(
                        input_texts,
                        batch_size=args.batch_size,
                        show_progress_bar=False,
                        normalize_embeddings=False,
                    )

                try:
                    batch_embeddings = _with_retry(
                        f"st_encode_batch{batch_no}",
                        _do_st,
                        max_retries=args.max_retries,
                        backoff=args.retry_backoff,
                    )
                except Exception as exc:
                    logger.error("Batch %d ST encoding failed: %s", batch_no, exc)
                    return 1
            else:
                try:
                    batch_embeddings = _embed_batch_ollama(
                        ollama_model=ollama_model,
                        texts=input_texts,
                        chunk_ids=chunk_ids,
                        max_retries=args.max_retries,
                        backoff=args.retry_backoff,
                    )
                except Exception as exc:
                    logger.error("Batch %d Ollama encoding failed: %s", batch_no, exc)
                    return 1
            total_embed_s += time.perf_counter() - start_embed

            if not batch_embeddings or len(batch_embeddings) != len(input_texts):
                logger.error(
                    "Batch %d produced %d embeddings for %d inputs.",
                    batch_no,
                    0 if not batch_embeddings else len(batch_embeddings),
                    len(input_texts),
                )
                return 1
            bad_dims = [len(v) for v in batch_embeddings if len(v) != args.dimension]
            if bad_dims:
                logger.error(
                    "Embedding dimension mismatch: expected %d, got %s (batch %d). Aborting.",
                    args.dimension,
                    bad_dims[:5],
                    batch_no,
                )
                return 1

            # 3b. Persist this batch immediately with its own commit.
            update_data = [
                (
                    vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    args.model,
                    args.version,
                    args.dimension,
                    cid,
                )
                for cid, vec in zip(chunk_ids, batch_embeddings, strict=True)
            ]
            start_persist = time.perf_counter()
            try:
                _persist_batch(
                    update_data,
                    max_retries=args.max_retries,
                    backoff=args.retry_backoff,
                )
            except Exception as exc:
                logger.error("Batch %d persist failed after retries: %s", batch_no, exc)
                logger.error(
                    "Progress saved up to last committed batch (%d rows). Re-run the same command to resume.",
                    processed_total,
                )
                return 1
            total_persist_s += time.perf_counter() - start_persist

            processed_total += len(rows)
            last_id = chunk_ids[-1]
            logger.info(
                "Batch %d committed: %d rows this batch, %d total this run, last_chunk_id=%s.",
                batch_no,
                len(rows),
                processed_total,
                last_id,
            )
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted by user. Progress saved: %d rows committed this run, last_chunk_id=%s. "
            "Re-run the same command to resume.",
            processed_total,
            last_id,
        )
        return 130

    logger.info(
        "Run complete: processed=%d batches=%d embed_time=%.1fs persist_time=%.1fs.",
        processed_total,
        batch_no,
        total_embed_s,
        total_persist_s,
    )

    # 4. Verification (fresh counts from DB).
    try:
        with get_db_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL;")
                null_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL;")
                valid_count = cur.fetchone()[0]
    except Exception as exc:
        logger.error("Verification query failed: %s", exc)
        return 1

    logger.info("Verification summary: valid_embedded_chunks=%d null_embeddings=%d", valid_count, null_count)
    if args.limit is not None and processed_total >= args.limit:
        logger.info("--limit %d reached with %d null embeddings remaining. Re-run without --limit to finish.", args.limit, null_count)
        return 0
    if null_count > 0:
        logger.warning(
            "%d chunks still lack embeddings! Re-run the same command to resume "
            "(already committed rows will be skipped).",
            null_count,
        )
        return 1

    logger.info("Batch chunk embedding pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
