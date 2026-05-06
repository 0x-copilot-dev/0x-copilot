# Cluster: `runtime_adapters`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/runtime_adapters/`](../../../services/ai-backend/src/runtime_adapters/) (`in_memory`, `postgres`, [`factory.py`](../../../services/ai-backend/src/runtime_adapters/factory.py)).
- **Primary entrypoints:** Factory selects backend via `RUNTIME_STORE_BACKEND`; stores implement ports from `agent_runtime.persistence`.

## Static signals

| Tool                          | Scope                  | Result (2026-05-06) |
| ----------------------------- | ---------------------- | ------------------- |
| Ruff `F401`, `F841`           | `src/runtime_adapters` | No findings         |
| Vulture `--min-confidence 80` | `src/runtime_adapters` | No 80%+ hits        |
| Vulture `--min-confidence 60` | `src/runtime_adapters` | Few hits            |

Notable Vulture 60% lines:

- `list_for_run` on citation stores ([`in_memory/citation_store.py`](../../../services/ai-backend/src/runtime_adapters/in_memory/citation_store.py), [`postgres/runtime_api_store.py`](../../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py)) — may implement a shared protocol; confirm callers.
- `seed_approval_request` on [`in_memory/runtime_api_store.py`](../../../services/ai-backend/src/runtime_adapters/in_memory/runtime_api_store.py) — likely **test fixture** API.
- **`InMemoryShareSnapshotStore`** — used from **tests** (`tests/unit/runtime_api/test_share_fork_route.py`, `tests/unit/agent_runtime/api/test_conversation_fork.py`); Vulture without tests sees it as unused — **test-only from repo perspective**.

## Wiring-checked

- **`READ_REPLICA_MAX_LAG_SECONDS`** in Postgres store — module-level constant possibly reserved for future read routing; grep before deleting.

## Test-only usage

- **`InMemoryShareSnapshotStore`**, **`seed_approval_request`** — primarily test/dev ergonomics.

## Likely dead / high-confidence candidates

- **`list_for_run` on citation store** — if no caller matches after repo-wide grep under `services/ai-backend/src`, candidate for removal or intentional protocol stub (document).

## Smells

- **Large generated-style stores** (`runtime_api_store.py`) — high churn and merge conflict surface; dead-code sweeps should prefer **grep callers** over Vulture alone.
- **Duplicate protocol methods** across in-memory vs Postgres — drift risk; unused overrides worth a focused refactor PR.

## Cross-cluster links

- Implements ports documented in [cluster-agent-runtime-persistence.md](./cluster-agent-runtime-persistence.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **6** lines):

- [`artifacts/cluster-runtime-adapters-vulture.txt`](./artifacts/cluster-runtime-adapters-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
