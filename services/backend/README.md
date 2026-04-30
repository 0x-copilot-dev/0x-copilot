# Backend

Core product backend for Enterprise Search. This service owns tenants, user/org
scope, product persistence, MCP registration, OAuth state, token storage, and
audit events.

## Local Environment

Use a service-local virtual environment. Do not reuse the `ai-backend` venv.

```bash
cd services/backend
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
PYTHONPATH=src .venv/bin/python -m uvicorn backend_app.app:app --host 127.0.0.1 --port 8100
```

Build the service image from this directory:

```bash
docker build -t enterprise-search-backend .
```

## Boundary Rule

This service must not import code from `services/backend-facade`,
`services/ai-backend`, or `apps/frontend`. Cross-component integration must go
through HTTP APIs, queues, or generated contracts.
