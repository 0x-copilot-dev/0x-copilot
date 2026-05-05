# Cluster: `agent_runtime/persistence/`

**Total: 1,726 LOC across 17 files.** Persistence contracts (NOT implementations — implementations live in [`runtime_adapters/`](../runtime-adapters/_index.md)). Three sub-areas: core utilities (constants, ports, errors, optimistic-lock helper, pool metrics), record dataclasses (one file per durable artifact), and schema/migration primitives.

## Role in the request lifecycle

These modules are the pure-data-and-protocol surface. The runtime emits event envelopes (`api/events.py`); the worker / API service write them via persistence ports defined here; the postgres / in-memory adapters in [`runtime_adapters/`](../runtime-adapters/_index.md) implement them. `optimistic.py` is shared by both backends for CAS-with-retry on `row_version`. `schema/migrate.py` is the versioned schema runner; `schema/postgres.py` defines the table layout. `pool_metrics.py` exports OTel meters consumed by the deployment monitoring layer.

## Files in this cluster

### Core (5 files, 539 LOC)

| File                                                                                                                                                            | LOC | Doc                                            |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ---------------------------------------------- |
| [`pool_metrics.py`](../../../services/ai-backend/src/agent_runtime/persistence/pool_metrics.py) — OTel meters for DB pool health and atomic-write outcomes.     | 172 | [persistence-bundle.md](persistence-bundle.md) |
| [`optimistic.py`](../../../services/ai-backend/src/agent_runtime/persistence/optimistic.py) — Bounded retry helper for optimistic-lock CAS misses.              | 120 | [persistence-bundle.md](persistence-bundle.md) |
| [`constants.py`](../../../services/ai-backend/src/agent_runtime/persistence/constants.py) — Constants for durable runtime persistence contracts and migrations. | 115 | [persistence-bundle.md](persistence-bundle.md) |
| [`ports.py`](../../../services/ai-backend/src/agent_runtime/persistence/ports.py) — Persistence provider ports beyond the narrow FastAPI producer surface.      |  92 | [persistence-bundle.md](persistence-bundle.md) |
| [`errors.py`](../../../services/ai-backend/src/agent_runtime/persistence/errors.py) — Typed persistence-layer errors for the agent runtime.                     |  40 | [persistence-bundle.md](persistence-bundle.md) |

### Records (10 files, 761 LOC)

| File                                                                                                                                                                      | LOC | Doc                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | -------------------------------------- |
| [`records/telemetry.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/telemetry.py) — Compression, capability, and per-run usage telemetry records. | 165 | [records-bundle.md](records-bundle.md) |
| [`records/outbox.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/outbox.py) — Outbox, worker claim, and consumer cursor records.                  | 138 | [records-bundle.md](records-bundle.md) |
| [`records/common.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/common.py) — Shared persistence enums and value normalization.                   | 135 | [records-bundle.md](records-bundle.md) |
| [`records/memory.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/memory.py) — Persisted runtime memory metadata records.                          |  65 | [records-bundle.md](records-bundle.md) |
| [`records/tools.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/tools.py) — Persisted tool invocation records with redacted inputs and outputs.   |  60 | [records-bundle.md](records-bundle.md) |
| [`records/subagents.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/subagents.py) — Persisted async subagent task and result records.             |  50 | [records-bundle.md](records-bundle.md) |
| [`records/payloads.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/payloads.py) — Large context payload reference records.                        |  41 | [records-bundle.md](records-bundle.md) |
| [`records/approvals.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/approvals.py) — Persisted approval request records for runtime actions.       |  39 | [records-bundle.md](records-bundle.md) |
| [`records/audit.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/audit.py) — Append-only runtime audit records for security and operations.        |  38 | [records-bundle.md](records-bundle.md) |
| [`records/checkpoints.py`](../../../services/ai-backend/src/agent_runtime/persistence/records/checkpoints.py) — Runtime checkpoint metadata records with blob references. |  30 | [records-bundle.md](records-bundle.md) |

### Schema (2 files, 229 LOC)

| File                                                                                                                                                                                | LOC | Doc                                  |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --: | ------------------------------------ |
| [`schema/migrate.py`](../../../services/ai-backend/src/agent_runtime/persistence/schema/migrate.py) — Versioned schema migration runner for the agent runtime.                      | 143 | [schema-bundle.md](schema-bundle.md) |
| [`schema/postgres.py`](../../../services/ai-backend/src/agent_runtime/persistence/schema/postgres.py) — PostgreSQL schema migration metadata for durable agent runtime persistence. |  86 | [schema-bundle.md](schema-bundle.md) |

## Doc layout

- [persistence-bundle.md](persistence-bundle.md) — `constants.py`, `ports.py`, `errors.py`, `optimistic.py`, `pool_metrics.py`
- [records-bundle.md](records-bundle.md) — all `records/*` (10 files)
- [schema-bundle.md](schema-bundle.md) — `schema/postgres.py`, `schema/migrate.py`

## Cross-cluster dependencies

**Imports from:**

- `service-contracts` (constants only)
- `psycopg`, OTel SDK
- Pydantic v2

**Imported by:**

- [`runtime_adapters/`](../runtime-adapters/_index.md) — implements ports + uses records
- [`agent_runtime/api/`](../agent-api/_index.md) — service uses ports + records
- [`agent_runtime/capabilities/`](../capabilities/_index.md) — record types referenced
- [`agent_runtime/context/memory/`](../context-memory/_index.md) — memory record types
- [`runtime_worker/`](../runtime-worker/_index.md) — outbox claim + records
- [`runtime_api/`](../../../services/ai-backend/src/runtime_api/) — schema/migrate runs at boot

## Use-case relevance

Background to all use-cases. Direct anchors:

- [12-stream-disconnect-and-resume.md](../../use-cases/12-stream-disconnect-and-resume.md) — `records/outbox.py` cursor.
- [08-user-cancels-mid-stream.md](../../use-cases/08-user-cancels-mid-stream.md) — `optimistic.py` for status CAS.
- [11-multi-subagent-plus-tool.md](../../use-cases/11-multi-subagent-plus-tool.md) — `records/subagents.py` + `records/tools.py`.
