# AI-Bibliometrics backend image (Phase 0).
# NOTE: backend/app/main.py lands in Task 2 (Fase 2). Phase 0 acceptance is
# `docker compose build` only — the image is not expected to run yet.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
