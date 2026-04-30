# Backend Facade Testing

Facade tests run inside the facade service environment. They should validate
product-facing routing and forwarding behavior without importing upstream
service modules.

## Commands

```bash
cd services/backend-facade
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m pytest
```

The project supports Python 3.11 or newer, with Python 3.13 as the local and
Docker target.

## Current Coverage

- `tests/test_facade_settings.py`: environment settings and forwarding behavior.

## Test Shape

- Patch or fake HTTP forwarding at the facade boundary; do not import upstream
  service internals.
- Test route families by upstream ownership: backend routes, AI backend routes,
  and SSE stream passthrough.
- Verify upstream errors preserve status codes in a predictable way.
- Verify non-object JSON responses become `502` when a facade route expects an
  object payload.
- Add contract tests when facade routes start shaping responses instead of
  passing through upstream JSON.

## Manual Smoke

For route changes, run the facade with both upstreams configured:

```bash
BACKEND_URL=http://127.0.0.1:8100 \
AI_BACKEND_URL=http://127.0.0.1:8000 \
PYTHONPATH=src .venv/bin/python -m uvicorn backend_facade.app:app --host 127.0.0.1 --port 8200
```

Then verify at least one route from each upstream family and, when changing
agent streaming, verify `/v1/agent/runs/{run_id}/stream` remains an SSE stream.
