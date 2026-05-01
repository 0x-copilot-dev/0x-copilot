# Backend

Core product backend for Enterprise Search. The current implementation owns MCP
registration, OAuth state, token storage, user skills, and audit events. Tenant
auth, permissions, broad product persistence, admin workflows, billing, and jobs
are target backend responsibilities that should land here as those features are
implemented.

See `ARCHITECTURE.md` for module ownership and `TESTING.md` for the service test
strategy. Internal service routes are specified in `docs/specs/internal-api.md`.

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

## Generic MCP OAuth Setup

When adding an OAuth-protected MCP server, the backend first uses standard MCP
OAuth discovery and dynamic client registration. Some providers require a
pre-registered OAuth app instead. For those servers, provide the optional
per-server OAuth client fields when creating or updating the MCP registration:

- `client_id`
- `client_secret`, when the provider requires a confidential client
- `scope`, when the provider requires scopes beyond the default `mcp`
- `authorization_endpoint` and `token_endpoint`, only when the server does not
  advertise OAuth metadata

Client secrets are stored through the backend token vault and are not returned in
public MCP server responses.

Build the service image from this directory:

```bash
docker build -f services/backend/Dockerfile -t enterprise-search-backend .
```

## Boundary Rule

This service must not import code from `services/backend-facade`,
`services/ai-backend`, or `apps/frontend`. Cross-component integration must go
through HTTP APIs, queues, constants-only service contracts, or generated
contracts.

This service owns its own `requirements.txt`, `pyproject.toml`, `Dockerfile`,
test environment, and deploy path.
