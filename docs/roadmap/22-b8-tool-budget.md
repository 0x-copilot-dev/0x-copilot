# PR 22 — B8: Code-Enforced Per-tool Token Budget

**Spec ID:** B8 | **Track:** Token Usage | **Wave:** 5 (Usage UX + Budgets) | **Estimated effort:** M
**Depends on:** B2 (per-call usage), B7 (budget infrastructure)
**Required for:** none

---

## 1. Functional Specification

### 1.1 Goal

Replace the prompt-only per-tool budget at [services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:41-72](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72) with code enforcement. Today the model is _asked_ to limit tool calls to N per task. With B8, the runtime enforces it. Adds per-tool _input-token_ budget on top of the call-count cap.

### 1.2 User-visible behavior

- **Model:** receives a safe error message when it exceeds a tool's call-count or input-token cap; can adapt and continue.
- **End user:** sees the rejected tool call as a failed step (not a hung "Running" card).
- **Org admin:** can configure per-tool budgets per org.

### 1.3 Out of scope

- Per-tool _cost_ budget (input-token cap is the concrete proxy).
- Cross-run tool budgets (per-run only).
- Allowing the model to request a budget waiver.

---

## 2. Technical Specification

### 2.1 Architecture

- New `runtime_tool_budgets` table seeded with the existing `RUNTIME_TOOL_CALL_BUDGET` value as `(org_id NULL, tool_name='*', max_calls_per_run=N)`.
- Existing [services/ai-backend/src/runtime_worker/tool_call_ledger.py](../../services/ai-backend/src/runtime_worker/tool_call_ledger.py) extended with `input_tokens` field per entry.
- New middleware wraps tool execution; checks ledger + budget; rejects with `ToolOutcome.REJECTED` on hard violation, emits warning on soft.
- Prompt suffix at [deep_agent_builder.py:41-72](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72) is preserved (it's a hint to the model that complements the hard cap) but references the actual configured value, not a magic 5.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0009_runtime_tool_budgets.sql`:

```sql
CREATE TABLE runtime_tool_budgets (
    id                          TEXT PRIMARY KEY,
    org_id                      TEXT,                          -- NULL = global default
    tool_name                   TEXT NOT NULL,                  -- '*' = all tools
    max_calls_per_run           INTEGER NOT NULL CHECK (max_calls_per_run >= 1),
    max_input_tokens_per_call   INTEGER,
    max_input_tokens_per_run    INTEGER,
    enforcement                 TEXT NOT NULL CHECK (enforcement IN ('soft','hard')),
    created_at                  TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL,
    UNIQUE (COALESCE(org_id, '<global>'), tool_name)
);

-- Seed default from current RUNTIME_TOOL_CALL_BUDGET
INSERT INTO runtime_tool_budgets (id, org_id, tool_name, max_calls_per_run, enforcement, created_at, updated_at)
VALUES ('seed_default', NULL, '*', 6, 'hard', now(), now());
```

### 2.3 Events

- New error code `ToolErrorCode.TOOL_BUDGET_EXCEEDED`.
- Existing `TOOL_CALL_COMPLETED` carries `safe_error_code='tool_budget_exceeded'` when applicable.

### 2.4 Code changes

**Modify** [services/ai-backend/src/runtime_worker/tool_call_ledger.py](../../services/ai-backend/src/runtime_worker/tool_call_ledger.py):

- Extend `ToolCallEntry` with `input_tokens: int | None = None`.
- New methods: `charged_calls(tool_name) -> int`, `total_input_tokens(tool_name) -> int`.

**New module** `services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py`:

- Wraps tool execution.
- Pre-execution check: `count_used = ledger.charged_calls(tool_name)`. If `count_used + 1 > budget.max_calls_per_run` and `enforcement='hard'` → return `ToolOutcome.REJECTED` with `TOOL_BUDGET_EXCEEDED`.
- Pre-execution input-token check: tokenize args (use a cheap tiktoken-style estimator OR provider API if available). If exceeds `max_input_tokens_per_call` → reject.
- Post-execution: `ledger.record(call, input_tokens=...)`.
- On soft violation: execute but emit `BUDGET_WARNING`-style event scoped to the run.

**Modify** [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py):

- Load org tool budgets once per run; pass to middleware via deps.

**Modify** [services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:41-72](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72):

- Replace the literal "5" with the actual configured cap (string-format the prompt suffix from config).

**Modify** [services/ai-backend/src/agent_runtime/execution/tool_outcomes.py](../../services/ai-backend/src/agent_runtime/execution/tool_outcomes.py):

- Add `TOOL_BUDGET_EXCEEDED` enum value.

**New** YAML seed `services/ai-backend/src/agent_runtime/budgets/seeds/tool-budgets.yaml` for additional defaults (e.g. tighter caps for specific tools like `web_search`).

**api-types:** mirror new error code.

### 2.5 Trust model & failure semantics

- Hard violation → `ToolOutcome.REJECTED`; model sees the safe error string; can choose to continue or finish.
- Soft violation → execute but log + warn; useful for dial-in before flipping to hard.
- If multiple budgets match (e.g. global `*` and org-specific `web_search`), most-specific wins.
- Concurrency: ledger is per-run in-memory; concurrent tools within one run go through the run's executor, which serializes ledger access.

### 2.6 Tenant isolation

Budget rows are per-org or global; lookup filters appropriately.

### 2.7 Observability

- Metric: `tool_budget_violation_total{tool, enforcement, kind=count|input_tokens}`.
- Audit: budget creation/update via admin endpoints (covered by B7's audit pattern).

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Default global budget `(*, max_calls_per_run=6, hard)` blocks the 7th call within a run.
- [ ] Per-org override for `web_search` to `max_calls_per_run=3` blocks 4th `web_search` even if `*` budget is 6.
- [ ] Per-call input-token cap rejects oversized arguments before invocation.
- [ ] Rejected tool call surfaces as `TOOL_CALL_COMPLETED` with `safe_error_code='tool_budget_exceeded'`.
- [ ] Soft enforcement executes but emits a warning event.
- [ ] Prompt suffix references actual configured cap, not literal 5.

### 3.2 Test plan

**Unit:**

- Middleware blocks 7th call when limit is 6.
- Per-call input-token cap rejects oversized.
- Most-specific budget wins.
- Soft enforcement: executes + warns.

**Integration:**

- Rejected tool surfaces as `TOOL_CALL_COMPLETED` with safe_error_code.
- Model receives the safe error string in tool result and can proceed.
- Concurrency: two parallel tool calls within one run; only within-budget execute; ledger consistent.

### 3.3 Compliance evidence produced

- Hard cap on tool calls per run, code-enforced (not just prompt-suggested).
- Per-org per-tool configurability for high-cost tools.

### 3.4 Rollout plan

Forward-only. The prompt-injected setting still works for orgs without rows (seed default covers).

### 3.5 Backout plan

Set seed default `enforcement='soft'` → no rejections, only warnings.

### 3.6 Definition of done

- [ ] Migration 0009 applied with seed.
- [ ] Middleware enforces caps.
- [ ] Ledger tracks input tokens.
- [ ] Prompt suffix dynamically reflects cap.
- [ ] All tests pass; concurrency test included.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0009_runtime_tool_budgets.sql` (+ rollback)
- Modify: [services/ai-backend/src/runtime_worker/tool_call_ledger.py](../../services/ai-backend/src/runtime_worker/tool_call_ledger.py)
- New: `services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py`
- Modify: [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py)
- Modify: [services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:41-72](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L41-L72)
- Modify: [services/ai-backend/src/agent_runtime/execution/tool_outcomes.py](../../services/ai-backend/src/agent_runtime/execution/tool_outcomes.py)
- New: `services/ai-backend/src/agent_runtime/budgets/seeds/tool-budgets.yaml`
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
