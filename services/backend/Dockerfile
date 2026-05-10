FROM python:3.13-slim-bookworm

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

EXPOSE 8100

USER app

CMD ["uvicorn", "backend_app.app:app", "--host", "0.0.0.0", "--port", "8100"]
