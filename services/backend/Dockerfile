FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY packages/service-contracts ./packages/service-contracts
COPY packages/audit-chain ./packages/audit-chain
COPY services/backend/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ./packages/service-contracts \
    && python -m pip install --no-cache-dir ./packages/audit-chain \
    && python -m pip install --no-cache-dir -r requirements.txt --require-hashes

COPY services/backend/src ./src
# Ship the versioned schema so the image can create its own database.
# MigrationRunner resolves <app>/migrations (parents[3] of db/migrate.py),
# and scripts/migrate.py is the operator CLI (`scripts/migrate.py apply`)
# used by the desktop supervisor and the self-host migration container.
COPY services/backend/migrations ./migrations
COPY services/backend/scripts ./scripts

EXPOSE 8100

USER app

CMD ["uvicorn", "backend_app.app:app", "--host", "0.0.0.0", "--port", "8100"]
