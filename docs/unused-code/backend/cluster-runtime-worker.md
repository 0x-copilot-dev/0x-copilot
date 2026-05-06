# Cluster: `runtime_worker`

## Cluster boundary

- **Paths:** [`services/ai-backend/src/runtime_worker/`](../../../services/ai-backend/src/runtime_worker/) (worker loop, streaming executor, stream helpers, handlers, background jobs).
- **Primary entrypoints:** [`__main__.py`](../../../services/ai-backend/src/runtime_worker/__main__.py), [`loop.py`](../../../services/ai-backend/src/runtime_worker/loop.py), [`streaming_executor.py`](../../../services/ai-backend/src/runtime_worker/streaming_executor.py), [`handlers/run.py`](../../../services/ai-backend/src/runtime_worker/handlers/run.py).

## Static signals

| Tool                          | Scope                | Result (2026-05-06)  |
| ----------------------------- | -------------------- | -------------------- |
| Ruff `F401`, `F841`           | `src/runtime_worker` | No findings          |
| Vulture `--min-confidence 80` | `src/runtime_worker` | No 80%+ hits         |
| Vulture `--min-confidence 60` | `src/runtime_worker` | Few hits (see below) |

Sample Vulture 60% lines: `emit_tool_call_outcome` ([`audit.py`](../../../services/ai-backend/src/runtime_worker/audit.py)), `build_default_sweeper` ([`jobs/approval_expiry_sweeper.py`](../../../services/ai-backend/src/runtime_worker/jobs/approval_expiry_sweeper.py)), `FieldEncryptionBackfill` ([`jobs/encrypt_existing_columns.py`](../../../services/ai-backend/src/runtime_worker/jobs/encrypt_existing_columns.py)), `run_until_idle` ([`loop.py`](../../../services/ai-backend/src/runtime_worker/loop.py)), methods on metrics/ledger helpers.

## Wiring-checked

- **`healthz`-style issue does not apply** — worker is not FastAPI-heavy; many symbols are called from the executor loop or tests.

## Test-only usage

- **`build_default_sweeper`** — grep suggests factory used from tests or CLI wiring; confirm before removal.
- **Ledger / metrics helpers** (`has_seen`, `tool_call_payload`, `has_entries`, etc.) — may be used only in tests or optional instrumentation paths.

## Likely dead / high-confidence candidates

- **[`jobs/encrypt_existing_columns.py`](../../../services/ai-backend/src/runtime_worker/jobs/encrypt_existing_columns.py)** — `FieldEncryptionBackfill` flagged unused at 60%; likely invoked only from a job runner entrypoint or ops script. Grep **job registration** (`encrypt`, `backfill`) before treating as dead.

## Smells

- **Retention sweeper / approval expiry** live here while policy types live under `agent_runtime` — keep behavioral docs cross-linked when changing either side.
- **Audit trail helpers** flagged unused may still be part of planned observability; verify against product requirements before deleting.

## Cross-cluster links

- Implements ports and stores from [cluster-runtime-adapters.md](./cluster-runtime-adapters.md).
- Emits events shaped by contracts from [cluster-runtime-api.md](./cluster-runtime-api.md) schemas via persistence.

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **9** lines):

- [`artifacts/cluster-runtime-worker-vulture.txt`](./artifacts/cluster-runtime-worker-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
