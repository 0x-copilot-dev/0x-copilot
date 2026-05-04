# Spec: B8 — Code-enforced per-tool budget

**Roadmap PR:** [docs/roadmap/22-b8-tool-budget.md](../../../../../docs/roadmap/22-b8-tool-budget.md).
**Wave:** 5. **Depends on:** B2 (per-call usage), B7 (budget infrastructure).

Replaces the prompt-only "be polite, please don't call too many tools" suffix in `deep_agent_builder.py` with a **hard, code-enforced** call-count and input-token cap per tool per run.

## Architecture

```
agent calls tool ─┐
                  ├─→ ToolBudgetMiddleware.before(tool_name, args) ─┐
                  │       ├─ count_used = ledger.charged_calls(tool_name)
                  │       ├─ if count_used + 1 > budget.max_calls_per_run AND hard:
                  │       │     return ToolOutcome.REJECTED(TOOL_BUDGET_EXCEEDED)
                  │       ├─ if estimate_input_tokens(args) > budget.max_input_tokens_per_call AND hard:
                  │       │     return ToolOutcome.REJECTED(TOOL_BUDGET_EXCEEDED)
                  │       └─ pass through
                  ├─→ tool execution
                  └─→ ToolBudgetMiddleware.after(tool_name, args, observed_tokens)
                          └─ ledger.record(call, input_tokens=observed_tokens)
```

The existing `ToolCallLedger` (created during tool-lifecycle work) already tracks per-run in-flight calls. We extend it with `input_tokens: int | None` and add `charged_calls(tool_name)` / `total_input_tokens(tool_name)` accessors.

## Module boundaries

- Modify `runtime_worker/tool_call_ledger.py`:
  - `ToolCallEntry.input_tokens: int | None = None`
  - `ToolCallLedger.charged_calls(tool_name) -> int`
  - `ToolCallLedger.total_input_tokens(tool_name) -> int`
  - `ToolCallLedger.record_input_tokens(call_id, tokens) -> None`
- New `agent_runtime/capabilities/tool_budget_middleware.py`:
  - `ToolBudgetPolicy` (resolved per-org budget table → flat dict).
  - `ToolBudgetMiddleware` — pre-call check, post-call accounting, returns `ToolOutcome.REJECTED` with `TOOL_BUDGET_EXCEEDED` on hard violation.
- Extend `agent_runtime/execution/tool_outcomes.py`:
  - `ToolErrorCode.TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"`
- Extend `AsyncPersistencePort`:
  - `lookup_tool_budgets(org_id) -> Sequence[ToolBudgetRecord]`
  - `upsert_tool_budget(record) -> ToolBudgetRecord` (admin-side; minimal CRUD)
- Modify `agent_runtime/execution/deep_agent_builder.py`:
  - `WEB_SUBAGENT_CHECKPOINT_SUFFIX` becomes a `format_web_subagent_suffix(tool_call_budget: int)` callable. The literal "5" becomes `{tool_call_budget}` interpolation. The supervisor prompt builder calls it with `RuntimeSettings.tool_call_budget`.
- Modify `runtime_worker/handlers/run.py`:
  - Load org tool budgets once per run via the new port method.
  - Inject `ToolBudgetMiddleware` into `RuntimeDependencies` so the existing tool execution path sees it.

## Schema (migration `0010_runtime_tool_budgets.sql`)

> The roadmap names this `0009`. Since B7 takes 0009 in this branch, B8 is **0010**.

```sql
CREATE TABLE IF NOT EXISTS runtime_tool_budgets (
    id                          TEXT PRIMARY KEY,
    org_id                      TEXT,
    tool_name                   TEXT NOT NULL,
    max_calls_per_run           INTEGER NOT NULL CHECK (max_calls_per_run >= 1),
    max_input_tokens_per_call   INTEGER,
    max_input_tokens_per_run    INTEGER,
    enforcement                 TEXT NOT NULL CHECK (enforcement IN ('soft','hard')),
    created_at                  TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL,
    UNIQUE (COALESCE(org_id, '<global>'), tool_name)
);

INSERT INTO runtime_tool_budgets
    (id, org_id, tool_name, max_calls_per_run, enforcement, created_at, updated_at)
VALUES
    ('seed_default', NULL, '*', 6, 'hard', now(), now())
ON CONFLICT DO NOTHING;
```

Same RLS pattern as the rest of the runtime tables (added to the C5 list at migration time).

## Pydantic contracts

```python
class ToolBudgetEnforcement(StrEnum):
    SOFT = "soft"
    HARD = "hard"

class ToolBudgetRecord(RuntimeContract):
    id: str
    org_id: str | None  # None = global default
    tool_name: str       # "*" = all tools
    max_calls_per_run: PositiveInt
    max_input_tokens_per_call: PositiveInt | None = None
    max_input_tokens_per_run: PositiveInt | None = None
    enforcement: ToolBudgetEnforcement
    created_at: datetime
    updated_at: datetime
```

The middleware resolves `(org_id, tool_name)` against the budget set with **most-specific wins** rules:

1. Exact `(org_id, tool_name)` match.
2. Else `(org_id, '*')`.
3. Else `(None, tool_name)`.
4. Else `(None, '*')`.
5. Else no enforcement (allow).

## Edge cases

- **Token estimation**: we use a cheap `len(str(args)) // 4` heuristic for the input estimate; the post-call observed-tokens path is best-effort and only used to enforce the per-run input-token cap on subsequent calls. This is intentionally conservative — the spec calls for "cheap tiktoken-style estimator OR provider API if available", and we avoid pulling tiktoken into base requirements.
- **Subagent tool calls** count toward the same per-run budget (the ledger is per-run, not per-agent).
- **Soft enforcement**: the call executes but emits a `BUDGET_WARNING` event with `severity: 'soft_cap'` and `tool_name` in payload. Useful for dial-in before flipping to hard.
- **Reject path**: returns `ToolOutcome.REJECTED` with `safe_error_code='tool_budget_exceeded'`. The model sees the safe error string in its tool-result message and can decide to continue or finalize.
- **Prompt suffix consistency**: the literal `5` in `WEB_SUBAGENT_CHECKPOINT_SUFFIX` becomes `{tool_call_budget}`. We prefer 1-source-of-truth via formatter to "drift on rename" risk.

## Security

- Same RLS guarantees as B7. Admin CRUD gated behind `admin:budgets` scope.
- Tool args may contain user PII — we tokenize for length only, never log args at the middleware boundary.

## Observability

- Counter: `tool_budget_violation_total{tool, enforcement, kind=count|input_tokens}`.
- Reuse B7's audit emission for budget config changes.

## Tests

- **Unit**:
  - Default budget `(*, 6, hard)` blocks the 7th call.
  - Per-org `(web_search, 3, hard)` blocks the 4th `web_search` even when global is 6.
  - Per-call input-token cap rejects oversized argument blob.
  - Soft enforcement: 7th call executes + emits warning.
  - Most-specific-wins resolution.
- **Integration**:
  - Rejected tool call surfaces as `TOOL_CALL_COMPLETED` with `safe_error_code='tool_budget_exceeded'`.
  - Concurrent tool calls within one run: ledger consistent, only within-budget calls execute.
- **Prompt regression**:
  - `format_web_subagent_suffix(6)` includes `"6 invocations"`.
  - `format_web_subagent_suffix(3)` includes `"3 invocations"`.
  - Existing snapshot/golden tests for the supervisor prompt updated.

## What we deliberately skip

- Per-tool **cost** budget (B7 covers cost; per-tool input-token cap is the concrete proxy).
- Cross-run tool budgets (per-run only — fits in the in-memory ledger).
- Allowing the model to request a budget waiver.
