# Backend Testing

Backend tests run inside the backend service environment. Do not use a sibling
service virtual environment or add sibling `src` directories to `PYTHONPATH`.

## Commands

```bash
cd services/backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m pytest
```

The project supports Python 3.11 or newer, with Python 3.13 as the local and
Docker target.

## Current Coverage

- `tests/test_mcp_registry.py`: MCP registry domain behavior.
- `tests/test_mcp_api_flow.py`: public and internal MCP route flows.
- `tests/test_skills_registry.py`: skill registry domain behavior.
- `tests/test_skills_api_flow.py`: public and internal skill route flows.

## Test Shape

- Prefer deterministic service and route tests over tests that require external
  MCP servers or OAuth providers.
- Use in-memory stores for unit and route tests unless the behavior specifically
  requires persistent storage.
- Add Postgres-backed smoke or integration tests only when migrations, SQL
  mapping, or production persistence behavior changes.
- Verify org/user scoping for every route that lists, mutates, or fetches data.
- Assert internal route behavior separately from public routes; they have
  different consumers and security expectations.

## Contract Checks

When route payloads change, update:

- `backend_app.contracts`
- `packages/api-types` for app-facing shapes
- `services/backend/ARCHITECTURE.md`
- `services/backend/docs/specs/internal-api.md` when an internal route changes
- `services/backend-facade/docs/specs/product-api-surface.md` when a public route
  is exposed through the facade
