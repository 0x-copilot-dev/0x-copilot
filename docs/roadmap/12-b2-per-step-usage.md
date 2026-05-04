# PR 12 — B2: Per-step Usage Events and Per-LLM-call Usage Table

**Spec ID:** B2 | **Track:** Token Usage | **Wave:** 3 (Parallel) | **Estimated effort:** M
**Depends on:** B1 (run usage table), C2
**Required for:** B5 (/context shows where tokens went), B7 (subagent attribution for budgets)

---

## 1. Functional Specification

### 1.1 Goal

Currently usage is aggregated only at run-end. To answer "where did the tokens go?" — main supervisor vs each subagent vs each LLM call — we need per-LLM-call rows. This is what powers Claude-Code-style `/context` showing the breakdown by call and subagent.

### 1.2 User-visible behavior

- **End user:** none yet (B5 surfaces it).
- **Developer:** can subscribe to a new `MODEL_CALL_COMPLETED` SSE event with usage metrics.
- **Operator:** can SQL `SELECT sum(input_tokens) FROM runtime_model_call_usage WHERE task_id = ?` to attribute cost to a subagent.

### 1.3 Out of scope

- Cost on the per-call row (B3 adds `cost_micro_usd`).
- Per-tool-call usage (tools don't consume LLM tokens; the _next_ model call after a tool result does).
- Streaming chunk-level usage (provider-dependent and rarely useful).

---

## 2. Technical Specification

### 2.1 Architecture

- New `MODEL_CALL_COMPLETED` event emitted at each AIMessage completion (the same boundary that today triggers `metrics.record_usage_from(chunk)` at [services/ai-backend/src/runtime_worker/streaming_executor.py:69](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L69)).
- New `runtime_model_call_usage` table; one row per LLM call. Worker writes it at `MODEL_CALL_COMPLETED` time.
- Existing `SUBAGENT_COMPLETED` payload extended with optional `usage: AssistantSubagentUsageRollup` (sum of the subagent's calls).
- `AssistantRunMetrics` refactored to a `PerCallTokenAccumulator` keyed by AIMessage id so subagent and main-graph calls bucket separately.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0004_runtime_model_call_usage.sql`:

```sql
CREATE TABLE runtime_model_call_usage (
    id                    TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL,
    run_id                TEXT NOT NULL REFERENCES agent_runs(id),
    conversation_id       TEXT NOT NULL REFERENCES agent_conversations(id),
    parent_event_id       TEXT,            -- the model_call_started or subagent_started this rolls under
    trace_id              TEXT NOT NULL,
    task_id               TEXT,            -- non-null when produced inside a subagent
    subagent_id           TEXT,
    model_provider        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens          INTEGER NOT NULL DEFAULT 0,
    duration_ms           INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL,
    schema_version        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_runtime_model_call_usage_org_run
    ON runtime_model_call_usage (org_id, run_id, created_at);
CREATE INDEX idx_runtime_model_call_usage_org_trace
    ON runtime_model_call_usage (org_id, trace_id);
CREATE INDEX idx_runtime_model_call_usage_org_task
    ON runtime_model_call_usage (org_id, task_id) WHERE task_id IS NOT NULL;
```

No FK to `runtime_async_tasks(task_id)` — async tasks may be reaped before usage queries.

### 2.3 Events / contracts

**New event type** in `services/ai-backend/src/runtime_api/schemas/common.py` `RuntimeApiEventType`:

```python
MODEL_CALL_COMPLETED = "model_call_completed"
```

**New schema** in [services/ai-backend/src/runtime_api/schemas/events.py](../../services/ai-backend/src/runtime_api/schemas/events.py):

```python
class AssistantSubagentUsageRollup(RuntimeContract):
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    call_count: NonNegativeInt = 0
```

`SUBAGENT_COMPLETED` payload extended with `usage: AssistantSubagentUsageRollup | None = None`.

`MODEL_CALL_COMPLETED` payload carries existing `AssistantPerformanceMetrics` (with `usage` populated).

### 2.4 Code changes

**Refactor** [services/ai-backend/src/runtime_worker/run_metrics.py](../../services/ai-backend/src/runtime_worker/run_metrics.py):

- Add `PerCallTokenAccumulator` keyed by AIMessage id (`message.id`).
- `AssistantRunMetrics.record_usage_from(value)` continues to bump the run-level total AND stamps to the per-call slot when the source is identifiable.
- New `AssistantRunMetrics.subagent_rollup(task_id) -> AssistantSubagentUsageRollup`.

**Modify** [services/ai-backend/src/runtime_worker/streaming_executor.py:69](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L69):

- When an AIMessage with usage closes (the same boundary we already detect), emit a `MODEL_CALL_COMPLETED` event via `event_producer.append_api_event`.
- Idempotency: dedupe by (run_id, message.id) — same AIMessage seen twice (rare but possible in retries) emits once.

**Modify** `services/ai-backend/src/runtime_worker/stream_subagents.py`:

- When a subagent task closes, attach its rollup to `SUBAGENT_COMPLETED`.

**Modify** [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py):

- On each `MODEL_CALL_COMPLETED` (worker-side, immediately after emit), write a `runtime_model_call_usage` row via new port method `record_model_call_usage(record)`.
- INSERT (no ON CONFLICT — rows are unique by id).

**New port method** + adapter implementations.

**Modify** [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts):

- Add `MODEL_CALL_COMPLETED` to the SSE event-envelope union.
- Add `usage` to `SUBAGENT_COMPLETED` payload mirror.

### 2.5 Trust model & failure semantics

- Best-effort write: if the INSERT fails, log + metric, continue (run-level row from B1 is the durable aggregate).
- Old SSE clients tolerate the new event type by skipping unknown event types (already the contract).

### 2.6 Tenant isolation

- `org_id` on row; reads filter by `org_id`.
- task_id index is partial `WHERE task_id IS NOT NULL` so per-subagent queries stay fast.

### 2.7 Observability

- Metric: `model_call_usage_writes_total{outcome}`.
- Per-run row count visible in `/v1/agent/runs/{run_id}/context` (B5).

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] A run with two LLM calls + one subagent (with two more calls) writes 4 rows in `runtime_model_call_usage`.
- [ ] Sum of per-call rows equals the run-level `runtime_run_usage` total (reconciliation invariant).
- [ ] `MODEL_CALL_COMPLETED` emitted exactly once per LLM call.
- [ ] `SUBAGENT_COMPLETED` carries the subagent's rollup.
- [ ] Old SSE clients (no MODEL_CALL_COMPLETED handling) don't error.

### 3.2 Test plan

**Unit:**

- `test_per_call_accumulator_buckets_independently` — two AIMessage ids, two slots.
- `test_subagent_rollup_sums_only_its_calls`.
- `test_model_call_completed_emitted_once_per_message`.

**Integration:**

- A run with subagent writes rows; sum reconciles to run-level total.
- SSE backwards-compat: client subscribing without handling MODEL_CALL_COMPLETED receives stream without errors.

### 3.3 Compliance evidence produced

- Per-call attribution enables auditable cost-per-feature analysis.

### 3.4 Rollout plan

Forward-only. Backfill of historical events is impractical (provider message ids weren't preserved).

### 3.5 Backout plan

- Stop emitting MODEL_CALL_COMPLETED + stop writing the table → no impact.
- Drop table via rollback.

### 3.6 Definition of done

- [ ] Migration 0004 applied.
- [ ] Per-call accumulator + emit + write all wired.
- [ ] Reconciliation test (sum-of-calls = run-total) passes.
- [ ] api-types updated.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0004_runtime_model_call_usage.sql` (+ rollback)
- Modify: `services/ai-backend/src/runtime_api/schemas/common.py` — `RuntimeApiEventType`
- Modify: [services/ai-backend/src/runtime_api/schemas/events.py](../../services/ai-backend/src/runtime_api/schemas/events.py)
- Modify: [services/ai-backend/src/runtime_worker/run_metrics.py](../../services/ai-backend/src/runtime_worker/run_metrics.py)
- Modify: [services/ai-backend/src/runtime_worker/streaming_executor.py:69](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L69)
- Modify: `services/ai-backend/src/runtime_worker/stream_subagents.py`
- Modify: [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py)
- Modify: persistence ports + adapters
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
