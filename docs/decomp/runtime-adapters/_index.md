# Cluster: `runtime_adapters/`

**Total: 4,572 LOC across 6 files** (the smallest file count, but contains two of the six XL files in the codebase). Backend implementations of the runtime persistence ports — one in-memory pair (sync + async) for tests/dev, one Postgres async store for production. Plus a base module of shared business logic that both backends subclass, sync→async wrappers, and a factory that selects the backend from env.

## Role in the request lifecycle

Every persisted runtime artifact (conversations, messages, runs, events, approvals, subagent tasks, tool observations, outbox rows, daily rollups, audit chain entries) flows through one of these stores. The API layer ([`agent_runtime/api/`](../agent-api/_index.md)) and the worker ([`runtime_worker/`](../runtime-worker/_index.md)) interact only with port protocols defined in [`agent_runtime/api/ports.py`](../agent-api/_index.md); these adapters provide the implementation. `RUNTIME_STORE_BACKEND` env var selects `in_memory` vs `postgres`. The base module owns shared status-transition rules so the two backends don't drift.

## Files in this cluster

| File                                                                                                                                                                                                  |   LOC | Doc                                                                   |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----: | --------------------------------------------------------------------- |
| [`postgres/runtime_api_store.py`](../../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — Async Postgres-backed runtime API, event store, and durable queue adapter.       | 2,344 | [postgres-runtime-api-store.md](postgres-runtime-api-store.md) (XL)   |
| [`in_memory/runtime_api_store.py`](../../../services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py) — Deterministic in-memory runtime API ports for local tests and development.     |   944 | [in-memory-runtime-api-store.md](in-memory-runtime-api-store.md) (XL) |
| [`base.py`](../../../services/ai-backend/src/runtime_adapters/base.py) — Shared business logic for runtime API adapter stores.                                                                        |   408 | [base-and-async.md](base-and-async.md)                                |
| [`async_wrappers.py`](../../../services/ai-backend/src/runtime_adapters/async_wrappers.py) — Sync to async port wrappers for runtime backend transition.                                              |   369 | [base-and-async.md](base-and-async.md)                                |
| [`in_memory/async_runtime_api_store.py`](../../../services/ai-backend/src/runtime_adapters/in_memory/async_runtime_api_store.py) — Async in-memory runtime API store for tests and local development. |   359 | [base-and-async.md](base-and-async.md)                                |
| [`factory.py`](../../../services/ai-backend/src/runtime_adapters/factory.py) — Runtime adapter composition from env-backed settings.                                                                  |   135 | [base-and-async.md](base-and-async.md)                                |

## Doc layout

- [postgres-runtime-api-store.md](postgres-runtime-api-store.md) — `postgres/runtime_api_store.py` (XL, 2,344) — split into sub-sections in the doc itself: connection/pool, conversation/message CRUD, run lifecycle, event append+cursor, approvals, subagents, tools, memory, outbox/queue, audit chain, daily rollups, retention.
- [in-memory-runtime-api-store.md](in-memory-runtime-api-store.md) — `in_memory/runtime_api_store.py` (XL, 944)
- [base-and-async.md](base-and-async.md) — `base.py`, `async_wrappers.py`, `factory.py`, `in_memory/async_runtime_api_store.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/api/ports.py`](../agent-api/_index.md) and [`agent_runtime/api/async_ports.py`](../agent-api/_index.md) — port protocols implemented here
- [`agent_runtime/persistence/`](../persistence/_index.md) — record dataclasses, optimistic-lock helper, errors
- `psycopg` (postgres adapter) / `asyncio` primitives
- `service-contracts` constants

**Imported by:**

- [`runtime_worker/dependencies.py`](../runtime-worker/_index.md) — selects + wires
- API layer in `services/ai-backend/src/runtime_api/` — same wiring for in-process API+worker dev mode

## Use-case relevance

Every use-case touches this cluster on the persistence side. Primary anchors:

- [12-stream-disconnect-and-resume.md](../../use-cases/12-stream-disconnect-and-resume.md) — event-store cursor logic in `postgres/runtime_api_store.py`.
- [09-new-thread-while-interrupt-active.md](../../use-cases/09-new-thread-while-interrupt-active.md) — pending-approval state across conversation switch.
- [13-memory-compression-token-budget.md](../../use-cases/13-memory-compression-token-budget.md) — memory + payload-ref tables in postgres.
