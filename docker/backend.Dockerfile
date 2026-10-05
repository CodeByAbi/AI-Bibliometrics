# syntax=docker/dockerfile:1

# ===========================================================================
# AI-Bibliometrics — backend image (multi-stage, non-root)
#
# Design notes — every choice below has a "why" comment because the defaults
# are not what you'd guess:
#
#  1. TWO stages, not one. The builder compiles/wheels everything into its own
#     filesystem; only /opt/venv is copied forward. The runtime stage therefore
#     contains NO compiler, NO pip cache, NO build headers, and no transient
#     build artefacts. Single-stage python:slim images ship all of that.
#
#  2. torch is CPU-pinned. See the long comment block in requirements.txt:35-70.
#     Plain `torch==2.14.0` on linux-x86_64 pulls +cu13 + 16 nvidia_* wheels
#     (measured: 3.2 GB) that can never run without a GPU. The previous
#     single-stage image shipped at 6.24 GB for exactly this reason.
#     `--extra-index-url` is passed HERE (not in requirements.txt) so the
#     production build resolves torch identically to CI/local from one lockfile.
#
#  3. Non-root (uid/gid 10001, name `app`). The old image ran uvicorn as root:
#     any RCE in a request handler would own the container. Fixed uid/gid (not
#     just any uid) so bind-mounted host volumes get predictable ownership.
#
#  4. No dev/test tooling. requirements.txt is runtime-only; pytest/ruff/mypy
#     live in requirements-dev.txt and never enter this image.
#
#  5. Virtualenv at /opt/venv, not a system install. Keeps every dep inside one
#     directory that can be copied between stages as a single unit, and keeps
#     `python`/`uvicorn` resolvable without polluting the base image's
#     site-packages.
# ===========================================================================


# ---------------------------------------------------------------------------
# Stage 1: builder — resolve and install dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Debian slim ships no build toolchain, but a few transitive deps may still want
# to compile from sdist. These are installed in the BUILDER only and never reach
# the runtime stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./

# Torch CPU index (see requirements.txt:35-70). --index-url stays PyPI so that
# ONLY torch is resolved from the extra index — the rest of the lockfile keeps
# coming from PyPI, which keeps the dependency-confusion surface minimal.
RUN pip install \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt \
 && python -c "import torch, sentence_transformers, fastapi, asyncpg, sqlglot; print('builder import check ok')" \
 && python -c "import torch; assert torch.version.cuda is None, 'CUDA torch leaked into CPU image'; print('torch is CPU-only:', torch.__version__)"


# ---------------------------------------------------------------------------
# Stage 2: runtime — minimal serving image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/home/app \
    HF_HOME=/home/app/.cache/huggingface \
    OMP_NUM_THREADS=1 \
    HF_HUB_DISABLE_TELEMETRY=1

# HF_HOME must be writable by uid 10001 or the FIRST query embedding fails with
# a 503 and silently falls back to Ollama (see embedding.py:174-186) — a
# degradation that looks like a healthy startup but is not. Chowned below.

# Fixed uid/gid so host bind-mounts line up predictably.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app "$HF_HOME" \
 && chown -R app:app /app "$HF_HOME"

# Copy ONLY the venv: no compiler, no pip cache, no headers, no build tools.
COPY --from=builder --chown=app:app /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app requirements.txt pyproject.toml ./

# Run as the unprivileged user from here on.
USER app

EXPOSE 8000

# Healthcheck. Uses python (already present) rather than curl/wget so the image
# needs no extra package. `/api/v1/health` is the documented liveness probe and
# returns 200 even when Ollama is down (status: degraded), so a CPU-only host
# without a running model is NOT restarted forever — that was the failure mode a
# naive `curl -f /` check would introduce.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"]

# CMD is intentionally overridden in docker-compose.yml (proxy trust is a
# deploy-time decision). Kept here so `docker run` standalone still works.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]