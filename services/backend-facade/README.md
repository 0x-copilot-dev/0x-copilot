# Backend Facade

Product-facing API facade for Enterprise Search apps. It shapes app responses,
proxies service calls, and owns client-compatible streaming surfaces.

## Local Environment

Use a service-local virtual environment. Do not reuse another service venv.

```bash
cd services/backend-facade
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run tests:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

Run the API:

```bash
BACKEND_URL=http://127.0.0.1:8100 \
AI_BACKEND_URL=http://127.0.0.1:8000 \
PYTHONPATH=src .venv/bin/python -m uvicorn backend_facade.app:app --host 127.0.0.1 --port 8200
```

Build the service image from this directory:

```bash
docker build -t enterprise-search-backend-facade .
```

## Boundary Rule

This service must not import implementation code from `services/backend`,
`services/ai-backend`, or `apps/frontend`. It should call backend services
through APIs and depend only on shared generated contracts when needed.
