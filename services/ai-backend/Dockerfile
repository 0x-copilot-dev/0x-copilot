FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000 \
    WEB_CONCURRENCY=4

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY packages/service-contracts ./packages/service-contracts
COPY packages/audit-chain ./packages/audit-chain
COPY services/ai-backend/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ./packages/service-contracts \
    && python -m pip install --no-cache-dir ./packages/audit-chain \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY services/ai-backend/src ./src
# Ship the versioned schema so the image can create its own database.
# MigrationRunner resolves <app>/migrations (parents[4] of schema/migrate.py);
# scripts/migrate.py is the operator CLI (`scripts/migrate.py apply`) used by
# the desktop supervisor and the self-host migration container. Boot-time
# auto-apply (RUNTIME_MIGRATIONS_AUTO_APPLY) also reads this directory.
COPY services/ai-backend/migrations ./migrations
COPY services/ai-backend/scripts ./scripts

EXPOSE 8000

USER app

CMD ["sh", "-c", "gunicorn runtime_api.app:app -k uvicorn.workers.UvicornWorker --workers ${WEB_CONCURRENCY:-4} --bind 0.0.0.0:${PORT:-8000}"]
