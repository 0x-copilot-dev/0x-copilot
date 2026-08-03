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
COPY services/ai-backend/scripts ./scripts
# Vendored fallbacks read at runtime: pricing_overrides.yaml and the
# models.dev catalog snapshot (offline tier of ModelsDevCatalogSource).
COPY services/ai-backend/config ./config

EXPOSE 8000

USER app

CMD ["sh", "-c", "gunicorn runtime_api.app:app -k uvicorn.workers.UvicornWorker --workers ${WEB_CONCURRENCY:-4} --bind 0.0.0.0:${PORT:-8000}"]
