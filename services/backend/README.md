# Backend

Core product backend for Enterprise Search. This service owns tenants, user/org
scope, product persistence, MCP registration, OAuth state, token storage, and
audit events.

## Local Environment

Use a service-local virtual environment. Do not reuse the `ai-backend`,
`backend-facade`, or any other sibling service venv.

```bash
cd services/backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use Python 3.11 or newer; the Docker image and local development target Python
3.13.

Run tests:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

If `.venv` is missing, create it from this service's own `requirements.txt`
before running tests. Do not point `PYTHONPATH` at sibling services.

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

This service owns its own `requirements.txt`, `pyproject.toml`, `Dockerfile`,
test environment, and deploy path.
