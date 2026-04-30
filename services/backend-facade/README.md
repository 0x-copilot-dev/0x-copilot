# Backend Facade

Product-facing API facade for Enterprise Search apps. It shapes app responses,
proxies service calls, and owns client-compatible streaming surfaces.

See `ARCHITECTURE.md` for the forwarding matrix, `TESTING.md` for test
guidance, and `docs/specs/product-api-surface.md` for the current app-facing
route surface.

## Local Environment

Use a service-local virtual environment. Do not reuse `backend`, `ai-backend`,
or any other sibling service venv.

```bash
cd services/backend-facade
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

This service owns its own `requirements.txt`, `pyproject.toml`, `Dockerfile`,
test environment, and deploy path.
