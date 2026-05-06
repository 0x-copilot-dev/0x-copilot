# Cluster: `agent_runtime.persistence` + `agent_runtime.retention`

## Cluster boundary

- **Paths:**
  - [`services/ai-backend/src/agent_runtime/persistence/`](../../../services/ai-backend/src/agent_runtime/persistence/)
  - [`services/ai-backend/src/agent_runtime/retention/`](../../../services/ai-backend/src/agent_runtime/retention/) (`policy_resolver.py`, [`__init__.py`](../../../services/ai-backend/src/agent_runtime/retention/__init__.py))
- **Primary entrypoints:** [`ports.py`](../../../services/ai-backend/src/agent_runtime/persistence/ports.py) (protocols), [`records/`](../../../services/ai-backend/src/agent_runtime/persistence/records/), [`schema/`](../../../services/ai-backend/src/agent_runtime/persistence/schema/).

## Static signals

| Tool                          | Scope                                                          | Result (2026-05-06)                                                                                                                                                                                                                                                                                                               |
| ----------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | `src/agent_runtime/persistence`, `src/agent_runtime/retention` | No findings                                                                                                                                                                                                                                                                                                                       |
| Vulture `--min-confidence 80` | same                                                           | **100%:** unused parameter names in [`ports.py`](../../../services/ai-backend/src/agent_runtime/persistence/ports.py) Protocol definitions (`scope_id`, `payload_id`, `checkpoint_namespace`, `checkpoint_version` on methods whose bodies are `...` / abstract — **false positives** for Protocol stubs without implementations) |
| Vulture `--min-confidence 60` | same                                                           | Hundreds of record-model validators and constants — **false positives**                                                                                                                                                                                                                                                           |

## Wiring-checked

- **`MemoryMetadataPort`, `PayloadStoragePort`, `CheckpointStorePort`** — abstract methods flagged “unused” are **interface contracts** implemented in [`runtime_adapters`](../../../services/ai-backend/src/runtime_adapters/); Vulture does not connect implementations to protocols.

## Test-only usage

- Migration helpers (`render_manifest`, `expected_manifest`) may be test/dev only — confirm usage from migration runner.

## Likely dead / high-confidence candidates

- **None confirmed from static analysis alone** — Protocol and Pydantic dominate the signal.

## Smells

- **Unused protocol parameter names** in empty Protocol bodies — noise for humans too; using explicit `...` / `typing.Protocol` + **`# noqa` policy** or **`Protocol` in `.pyi`** could reduce scanner noise (optional hygiene).

## Cross-cluster links

- Retention **sweeper jobs** live under [`runtime_worker/jobs/`](../../../services/ai-backend/src/runtime_worker/jobs/) — cross-link [cluster-runtime-worker.md](./cluster-runtime-worker.md).
- HTTP retention schemas — [cluster-runtime-api.md](./cluster-runtime-api.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **140** lines):

- [`artifacts/cluster-agent-runtime-persistence-vulture.txt`](./artifacts/cluster-agent-runtime-persistence-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
