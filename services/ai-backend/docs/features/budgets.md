# Budgets

How token/cost budgets are enforced at run-start, monitored during a run, and
charged at run-completion. Includes per-run tool invocation caps.

See also:

- [features/usage-metrics.md](usage-metrics.md) — usage recording and rollup
- [features/thinking-reasoning.md](thinking-reasoning.md) — reasoning token billing

---

## What it does

Budgets prevent runaway spend by org or user. Before a run starts, the system
reserves an estimated amount from the available budget. During the run, the worker
tracks actual token usage. After the run, `BudgetCharger` applies the true cost
to the budget rows using CAS retries to avoid double-charging.

A separate per-run tool budget cap limits the total number of tool invocations
within one run (default 5).

---

## Key modules

| File                                                   | Role                                                                           |
| ------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `agent_runtime/budgets/enforcer.py`                    | `BudgetEnforcer` — pre-flight check at run-start; denies or warns              |
| `agent_runtime/budgets/estimator.py`                   | `BudgetEstimator` — estimates cost from model config and message length        |
| `agent_runtime/budgets/reservations.py`                | `BudgetReservationManager` — creates/releases temporary budget holds           |
| `agent_runtime/budgets/charger.py`                     | `BudgetCharger` — applies actual cost post-run (CAS, idempotent on retry)      |
| `agent_runtime/budgets/period.py`                      | `BudgetPeriod` — daily/monthly period helpers                                  |
| `agent_runtime/capabilities/tool_budget_guard.py`      | `ToolBudgetGuard` — per-run tool invocation counter (ContextVar-bound)         |
| `agent_runtime/capabilities/tool_budget_middleware.py` | `ToolBudgetMiddleware` — wraps every tool call; raises `BudgetExceeded` at cap |
| `agent_runtime/pricing/calculator.py`                  | `CostCalculator` — converts token counts to micro-USD                          |
| `agent_runtime/pricing/catalog.py`                     | `ModelPricingCatalog` — in-memory pricing rows refreshed from LiteLLM + DB     |
| `runtime_api/schemas/budgets.py`                       | `BudgetRecord`, `BudgetReservationRecord`                                      |

---

## Budget pre-flight

`BudgetEnforcer.check_preflight(run_request, context)` runs before the worker
claims the run:

1. `BudgetEstimator.estimate(model_config, message_count, message_tokens)` — returns
   a rough upper-bound micro-USD cost.
2. `PersistencePort.lookup_budgets_for_run(org_id, user_id)` — fetches org and
   user budget rows.
3. If estimated cost exceeds the remaining budget: returns `BudgetPreflightDeny`.
4. If within a warning threshold: returns `BudgetPreflightWarn` (the run continues
   but a `BUDGET_WARNING` event is emitted).
5. Otherwise: returns `BudgetPreflightAllow`.

On `BudgetPreflightDeny`, the run is transitioned to `FAILED` with a `BUDGET_DENIED`
event and a safe user-facing message.

---

## Budget reservation

`BudgetReservationManager.reserve(run_id, estimated_cost)`:

- Creates a `BudgetReservationRecord` row with status `RESERVED`.
- Deducts the reservation from the available budget so concurrent runs don't
  over-commit.

On run completion, the reservation is released and replaced with the actual charge.

---

## `BudgetCharger`

`agent_runtime/budgets/charger.py`

Post-run charging:

1. `CostCalculator.compute(usage_records, catalog)` — converts token counts to
   micro-USD with banker's rounding. Uses `reasoning_per_1m_micro_usd` for reasoning
   tokens if set.
2. `PersistencePort.charge_budget(org_id, user_id, amount)` — CAS-updates the budget
   row. On CAS conflict (optimistic lock failure), retries via `with_optimistic_retry()`.
3. Releases the `BudgetReservationRecord`.

Idempotent: if the worker crashes after charging but before marking the queue claim
complete, re-running the handler reads the existing `RuntimeRunUsageRecord` and skips
re-charging (the charge is keyed on `run_id`).

---

## Per-run tool budget cap

`ToolBudgetGuard` is a `ContextVar`-bound counter per run.
`ToolBudgetMiddleware` wraps every tool invocation:

```python
guard.decrement()          # raises BudgetExceeded if count == 0
result = await tool(args)  # tool executes
```

`BudgetExceeded` is caught by `StreamOrchestrator`, which:

1. Emits a `BUDGET_WARNING` event (user-visible, safe message).
2. Returns a synthetic tool error result to the model so it can conclude gracefully.

The cap is configurable per workspace. Default is 5 per run.

---

## Pricing catalog

`agent_runtime/pricing/catalog.py`

`ModelPricingCatalog` holds pricing rows in memory:

- `input_per_1m_micro_usd` — cost per 1M input tokens
- `output_per_1m_micro_usd` — cost per 1M output tokens
- `reasoning_per_1m_micro_usd` — cost per 1M reasoning tokens (optional)
- `cached_input_per_1m_micro_usd` — cost per 1M cached input tokens (optional)

`agent_runtime/pricing/refresh_loop.py` refreshes the catalog periodically from
LiteLLM's pricing source and DB overrides. A stale catalog does not block runs —
it uses the last known prices.

---

## Budget record shape (`runtime_api/schemas/budgets.py`)

| Field                | Type          | Notes                                            |
| -------------------- | ------------- | ------------------------------------------------ |
| `budget_id`          | `str`         | UUID                                             |
| `org_id`             | `str`         | Org scope                                        |
| `user_id`            | `str \| None` | User scope (optional; None = org-level cap only) |
| `period_start`       | `date`        | Daily or monthly period                          |
| `period_kind`        | `str`         | `daily` / `monthly`                              |
| `limit_micro_usd`    | `int`         | Hard cap                                         |
| `used_micro_usd`     | `int`         | Accumulated spend                                |
| `reserved_micro_usd` | `int`         | In-flight reservations                           |
| `version`            | `int`         | CAS version (optimistic lock)                    |
