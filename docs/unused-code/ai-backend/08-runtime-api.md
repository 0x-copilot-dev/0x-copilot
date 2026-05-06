# Cluster 08 — runtime_api

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

FastAPI application, HTTP routers, Pydantic schemas, SSE adapters, auth/RBAC helpers under [`services/ai-backend/src/runtime_api/`](../../../services/ai-backend/src/runtime_api/).

## Entrypoints / wiring

- [`runtime_api/app.py`](../../../services/ai-backend/src/runtime_api/app.py) mounts routers and lifespan hooks.
- Routes register explicit paths in [`http/routes.py`](../../../services/ai-backend/src/runtime_api/http/routes.py) and satellite modules (`*_routes.py`).

## Likely unused or low-value symbols

| Location           | Symbol / issue                   | Evidence                                                                                                                                                                                                                         | Confidence                   | Action                                                                                                                                                   |
| ------------------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemas/inbox.py` | Class `InboxEventEnvelopeSchema` | **No imports** anywhere (only definition site matches ripgrep). **0%** pytest coverage on module. Inbox SSE builds JSON manually in [`sse/inbox_adapter.py`](../../../services/ai-backend/src/runtime_api/sse/inbox_adapter.py). | **High**                     | **Delete** module if FE/parser truly does not need Pydantic validation **or** wire adapter to emit validated schema / export from `schemas/__init__.py`. |
| `schemas/inbox.py` | —                                | Not listed in [`schemas/__init__.py`](../../../services/ai-backend/src/runtime_api/schemas/__init__.py) `__all__`.                                                                                                               | Confirms dead export surface | Align package exports if kept                                                                                                                            |

### Coverage highlights (not automatically “dead”)

- `sse/event_bus.py` ~44% — may indicate legacy or rarely used generic bus vs inbox/run adapters; trace callers before deleting.
- `schemas/shares.py` partial misses — newer share fork payloads may need more tests rather than deletion.

## Test-only vs production

Many routes have focused unit tests; SSE streaming branches differ under asyncio timeouts.

## Code smells

- **Duplicate wire formats:** Inbox SSE bypasses `InboxEventEnvelopeSchema`, duplicating field names in `InboxSseAdapter.format_event` — risks schema drift vs commented intent (“same parser as run stream”).
- **Large `schemas/events.py`:** Natural accumulation point; watch for unused `Literal` arms when event vocabulary changes.

## Follow-ups

- Decide single source of truth for inbox SSE payload shape (Pydantic model vs dict builder) and add a regression test comparing serialized keys to model fields.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 139 — **largest cluster raw count** · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Confirmed false positives

- [`routes/health.py`](../../../services/ai-backend/src/runtime_api/routes/health.py) — `healthz` / `readyz` reported unused functions — they are **mounted** via FastAPI `add_api_route`.

### Likely noise

- **`schemas/events.py`, `schemas/conversations.py`, `schemas/common.py`** — hundreds of optional-field helpers / serializer branches flagged as unused methods — often **Pydantic validators** or defensive helpers.
- **`schemas/shares.py`** — `_enforce_view_access_invariants` etc. — verify before deleting (may run from model validators).

### Strong candidates (reconfirm)

- **`schemas/inbox.py`** — remains the clearest **unused module** in this cluster (see table above).
