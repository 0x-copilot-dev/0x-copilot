# Adapters

How `runtime_adapters/` implements the port protocols for dev/test and production.

See also:

- [02-contracts.md](02-contracts.md) — port protocol signatures
- [reference/persistence-ports.md](../reference/persistence-ports.md) — full method list
- [reference/env-vars.md](../reference/env-vars.md) — `RUNTIME_STORE_BACKEND` and related vars

---

## Adapter selection

`runtime_adapters/factory.py` reads `RUNTIME_STORE_BACKEND` and returns a fully wired
`RuntimeStoreBundle` (a struct with all port instances).

| `RUNTIME_STORE_BACKEND` | Adapter class                  | When to use                                                  |
| ----------------------- | ------------------------------ | ------------------------------------------------------------ |
| `in_memory`             | `InMemoryRuntimeApiStore`      | Tests; single-process dev without a DB                       |
| `in_memory_async`       | `AsyncInMemoryRuntimeApiStore` | Thin async wrapper over `in_memory`; same semantics          |
| `postgres`              | `PostgresRuntimeApiStore`      | Shared-store production; requires `DATABASE_URL`             |
| `file`                  | `FileRuntimeApiStore`          | `single_user_desktop` only; local JSONL store, single-writer |

The API process and the worker process must use **the same** `RUNTIME_STORE_BACKEND` value.
Mixing `in_memory` API with `postgres` worker is not supported. The `file` backend is
**single-process only**: its queue claim is an in-process lock, so the desktop runs the
worker in-process (`start_in_process_worker` gates on the `single_user_desktop` profile,
not the store backend) and the standalone `python -m runtime_worker` refuses it.

The `file` backend's append-with-fold "state" ledgers (usage, approvals, budgets, …) are
**compacted at boot**: `open()` folds any ledger whose on-disk log has grown past a ratio
threshold back to its live set via the crash-safe `StateLedger.rewrite` (temp → fsync →
`os.replace`), so replay cost tracks live state, not total history. The **command queue**
(`state/queue.jsonl`, a raw op-log — enqueue + a status/attempts op per claim + a terminal
status) is folded the same way to only its non-terminal commands, so both boot replay and
every `claim_next` scan track live commands, not total history. The **audit log**
(append-only immutable evidence — the per-org `seq`/signature chain) and **session
event/message/run streams** (monotonic `sequence_no` = stream resume) are never folded.
All compaction is best-effort — a failure never breaks `open()`. Kill switch:
`RUNTIME_FILE_STORE_COMPACTION=0`.

---

## In-memory adapters (`runtime_adapters/in_memory/`)

Used in tests and single-process local dev (`RUNTIME_START_IN_PROCESS_WORKER=true` +
`RUNTIME_STORE_BACKEND=in_memory`).

| File                                        | Port implemented                                        |
| ------------------------------------------- | ------------------------------------------------------- |
| `runtime_api_store.py`                      | `PersistencePort`, `EventStorePort`, `RuntimeQueuePort` |
| `draft_store.py`                            | `DraftStorePort`                                        |
| `citation_store.py`                         | `CitationStorePort`                                     |
| `source_store.py`                           | `SourceStorePort`                                       |
| `subagent_store.py`                         | `SubagentStorePort`                                     |
| `conversation_tool_ordinal_store.py`        | `ConversationToolOrdinalStorePort`                      |
| `share_store.py`, `share_snapshot_store.py` | `ShareStorePort`                                        |

**Characteristics:**

- All state in Python dicts keyed by entity id.
- Sequence numbers are in-memory counters; reset on process restart.
- No transactions; concurrent mutations to the same run can race (not a problem in
  single-process use where there is one worker coroutine).
- `EventBus` is the in-memory `RuntimeEventBus` (`runtime_api/sse/event_bus.py`):
  an asyncio `Event` per `run_id`.

---

## Postgres adapters (`runtime_adapters/postgres/`)

Used in production and integration tests that spin up a real Postgres instance.

| File                                 | Port implemented                                        |
| ------------------------------------ | ------------------------------------------------------- |
| `runtime_api_store.py`               | `PersistencePort`, `EventStorePort`, `RuntimeQueuePort` |
| `draft_store.py`                     | `DraftStorePort`                                        |
| `citation_store.py`                  | `CitationStorePort`                                     |
| `source_store.py`                    | `SourceStorePort`                                       |
| `subagent_store.py`                  | `SubagentStorePort`                                     |
| `conversation_tool_ordinal_store.py` | `ConversationToolOrdinalStorePort`                      |
| `share_store.py`                     | `ShareStorePort`                                        |

**Characteristics:**

- asyncpg connection pool; all operations are async.
- `EventStorePort` writes are two ops: `INSERT INTO runtime_events` then
  `UPDATE runs SET latest_sequence_no`. Consider using `append_events_batch` for
  multi-event writes to reduce round trips.
- `RuntimeQueuePort` uses an advisory-lock claim pattern on the `runtime_commands`
  table: `claim_next` does `SELECT … FOR UPDATE SKIP LOCKED`.
- `EventBus` is `PostgresEventBus` (`runtime_api/sse/postgres_event_bus.py`):
  wraps Postgres `LISTEN`/`NOTIFY` so SSE adapters across multiple API pods are
  all woken by a worker notify.

---

## Schema and migrations

`agent_runtime/persistence/schema/`:

- `postgres.py` — `CREATE TABLE` DDL for all tables.
- `migrate.py` — lightweight migration runner (reads `migrations/MANIFEST.lock`).

Migration files live in `services/ai-backend/migrations/` as numbered SQL files.
Run via `alembic` or directly with `migrate.py` at service start (controlled by
`RUNTIME_AUTO_MIGRATE` env var).

---

## How to add a new adapter

1. Create a new directory under `runtime_adapters/` (e.g. `runtime_adapters/redis/`).
2. Implement every method of the port protocols you need
   (`PersistencePort`, `EventStorePort`, `RuntimeQueuePort`, plus any from
   `agent_runtime/persistence/ports.py`).
3. All methods must be `async def`.
4. Add a case to `runtime_adapters/factory.py` keyed on a new `RUNTIME_STORE_BACKEND`
   value.
5. Add integration tests using the new backend.

There is no partial implementation: if a port method is not implemented, `NotImplementedError`
should be raised, not silently skipped.

---

## Field-level encryption

`agent_runtime/persistence/encryption.py` wraps asyncpg to transparently encrypt
designated columns (message content, approval payloads) before write and decrypt on read.
The KMS client (`_aws_kms_client.py`) is pluggable. In local dev, a no-op
`LocalDevEncryption` adapter is used. `RUNTIME_ENCRYPTION_BACKEND` selects the adapter.
