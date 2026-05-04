# PR 11 — B1: Denormalized Run Usage Table

**Spec ID:** B1 | **Track:** Token Usage | **Wave:** 3 (Parallel) | **Estimated effort:** M
**Depends on:** C2 (migrations)
**Required for:** B2, B3, B4, B5, B6, B7

---

## 1. Functional Specification

### 1.1 Goal

Token usage _is_ extracted today by [services/ai-backend/src/runtime_worker/run_metrics.py](../../services/ai-backend/src/runtime_worker/run_metrics.py) and emitted on `RUN_COMPLETED` events — but it lives only inside JSONB event payloads. Aggregating "tokens used by user X this month" requires scanning `runtime_events` and parsing JSON, which doesn't scale past tens of thousands of runs. This PR adds a single denormalized row per run, keyed by `run_id`, populated by the worker.

### 1.2 User-visible behavior

- **End user:** none yet (UI surfaces in B5/B6).
- **Operator:** can run `SELECT sum(total_tokens) FROM runtime_run_usage WHERE org_id=...` in milliseconds.
- **Backfill operator:** can run a one-off script to populate historical rows from `runtime_events`.

### 1.3 Out of scope

- Per-LLM-call breakdown (B2).
- Cost (B3).
- Rollups + read endpoints (B4).
- UI commands (B5, B6).
- Budget enforcement (B7).

---

## 2. Technical Specification

### 2.1 Architecture

- One row per assistant run, written by the worker at `RUN_COMPLETED` time.
- Worker write is idempotent (`INSERT ... ON CONFLICT (run_id) DO NOTHING`) so re-handling the same run never double-charges.
- Retention is _decoupled_ from messages. User-history deletion sets `pii_purged_at` rather than deleting the row, so billing/audit aggregates remain intact while the user-attributable PII (conversation_id, user_id) is severed when needed.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0003_runtime_run_usage.sql`:

```sql
CREATE TABLE runtime_run_usage (
    id                    TEXT PRIMARY KEY,            -- equals run_id; one row per run
    org_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    conversation_id       TEXT NOT NULL REFERENCES agent_conversations(id),
    run_id                TEXT NOT NULL UNIQUE REFERENCES agent_runs(id),
    assistant_id          TEXT,
    model_provider        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens         INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cached_input_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    total_tokens          INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    chunk_count           INTEGER NOT NULL DEFAULT 0,
    first_token_ms        INTEGER,
    duration_ms           INTEGER NOT NULL DEFAULT 0,
    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ NOT NULL,
    status                TEXT NOT NULL,                -- mirrors final agent_runs.status
    schema_version        INTEGER NOT NULL DEFAULT 1,
    retention_until       TIMESTAMPTZ,
    pii_purged_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_runtime_run_usage_org_user_completed
    ON runtime_run_usage (org_id, user_id, completed_at DESC);
CREATE INDEX idx_runtime_run_usage_org_conversation_completed
    ON runtime_run_usage (org_id, conversation_id, completed_at DESC);
CREATE INDEX idx_runtime_run_usage_org_completed
    ON runtime_run_usage (org_id, completed_at DESC);
CREATE INDEX idx_runtime_run_usage_org_model_completed
    ON runtime_run_usage (org_id, model_provider, model_name, completed_at DESC);
CREATE INDEX idx_runtime_run_usage_retention
    ON runtime_run_usage (retention_until) WHERE pii_purged_at IS NULL;
```

### 2.3 Endpoints

None. Read endpoints come in B4.

### 2.4 Code changes

**New record** in [services/ai-backend/src/agent_runtime/persistence/records/telemetry.py](../../services/ai-backend/src/agent_runtime/persistence/records/telemetry.py):

```python
class RuntimeRunUsageRecord(RuntimeContract):
    id: str   # = run_id
    org_id: str
    user_id: str
    conversation_id: str
    run_id: str
    assistant_id: str | None = None
    model_provider: str
    model_name: str
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    chunk_count: NonNegativeInt = 0
    first_token_ms: NonNegativeInt | None = None
    duration_ms: NonNegativeInt = 0
    started_at: datetime
    completed_at: datetime
    status: str
    schema_version: int = 1
    retention_until: datetime | None = None
    pii_purged_at: datetime | None = None
    created_at: datetime
```

**New port method** on the persistence-write port (`services/ai-backend/src/agent_runtime/persistence/ports.py`):

```python
async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None: ...
```

**Adapter implementations:**

- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — INSERT ... ON CONFLICT (run_id) DO NOTHING.
- In-memory adapter mirrors the call signature.

**Worker hook** in [services/ai-backend/src/runtime_worker/handlers/run.py:271-287](../../services/ai-backend/src/runtime_worker/handlers/run.py#L271-L287) — after `_append_lifecycle(RUN_COMPLETED, ...)` succeeds, build the record from `metrics_payload` and call `self.persistence.record_run_usage(...)`. Use the same `completed.completed_at` timestamp.

**Constructor helper** in [services/ai-backend/src/runtime_worker/run_metrics.py](../../services/ai-backend/src/runtime_worker/run_metrics.py) — `AssistantRunMetrics.to_usage_record(run, model_config) -> RuntimeRunUsageRecord` so the handler doesn't manually shape the payload.

**Backfill script** `services/ai-backend/scripts/usage/backfill_run_usage.py`:

- Reads `runtime_events` rows where `event_type='run_completed'` ordered by `(org_id, created_at)`.
- Extracts `payload_json_redacted.performance_metrics.usage`.
- INSERT ... ON CONFLICT DO NOTHING.
- Cursor-based; resumable; rate-limited.
- Operator-runnable; not on app startup.

### 2.5 Trust model & failure semantics

- Worker write is best-effort: if the INSERT fails for any reason other than ON CONFLICT, log error + emit metric, continue. The run completion event is the source of truth; usage table is a derived aggregate.
- Retention sweeper (C8) eventually purges or PII-redacts rows past `retention_until`.

### 2.6 Tenant isolation

- `org_id` on the row; all reads filter by `org_id`.
- Backfill script preserves `org_id` from the source event.

### 2.7 Observability

- Metric: `runtime_run_usage_writes_total{outcome=inserted|conflict|error}`.
- Lag metric: `runtime_run_usage_lag_seconds` (median delay between RUN_COMPLETED and row written).

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] After a run completes, exactly one `runtime_run_usage` row exists for it.
- [ ] Re-handling the same run (worker retry) does NOT create a duplicate row.
- [ ] Cancelled run with partial usage still records a row.
- [ ] Run with zero usage records a row with all token fields = 0.
- [ ] Backfill script produces correct rows for historical events; re-running is a no-op.

### 3.2 Test plan

**Unit:**

- `test_run_handler_records_usage` — handler writes a row with the right tokens after RUN_COMPLETED.
- `test_runtime_run_usage_record_validation` — Pydantic; `total_tokens` fallback when provider didn't supply.
- `test_in_memory_record_run_usage_idempotent`.

**Integration (postgres):**

- Concurrent runs each write exactly one row.
- Double-handle of the same run emits one row only.
- Cross-tenant filter excludes other org rows.
- Cancelled run records partial usage.

**Backfill:**

- `test_backfill_run_usage` — given fixture events, produces expected rows; re-run no-op.

### 3.3 Compliance evidence produced

- Foundation for billing/audit aggregates that survive per-conversation deletion.
- `pii_purged_at` semantics documented for legal/compliance review.

### 3.4 Rollout plan

Schema goes live empty. Backfill is operator-run. No production behavior change beyond the worker write hook.

### 3.5 Backout plan

Drop the table via migration rollback. Worker write becomes a no-op when adapter is reverted.

### 3.6 Definition of done

- [ ] Migration 0003 (ai-backend) applied.
- [ ] Record + adapter + worker hook + helper land.
- [ ] Backfill script tested.
- [ ] All unit + integration tests pass.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0003_runtime_run_usage.sql` (+ rollback)
- Modify: [services/ai-backend/src/agent_runtime/persistence/records/telemetry.py](../../services/ai-backend/src/agent_runtime/persistence/records/telemetry.py)
- Modify: `services/ai-backend/src/agent_runtime/persistence/ports.py`
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py)
- Modify: in-memory adapter under `services/ai-backend/src/runtime_adapters/in_memory/`
- Modify: [services/ai-backend/src/runtime_worker/handlers/run.py:271-287](../../services/ai-backend/src/runtime_worker/handlers/run.py#L271-L287)
- Modify: [services/ai-backend/src/runtime_worker/run_metrics.py](../../services/ai-backend/src/runtime_worker/run_metrics.py)
- New: `services/ai-backend/scripts/usage/backfill_run_usage.py`
