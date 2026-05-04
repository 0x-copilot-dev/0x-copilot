# Spec: B7 — Per-org and per-user budget enforcement

**Roadmap PR:** [docs/roadmap/21-b7-budgets.md](../../../../../docs/roadmap/21-b7-budgets.md).
**Wave:** 5. **Depends on:** B1, B3, B4, C3 (optimistic-lock pattern).

Capstone for the usage track. Pre-run **deny** for hard caps; pre-run **warn** for soft caps; post-run **charge** with idempotency on `run_id`.

## Architecture

```
worker.handle(command)
  ├─ load run
  ├─ BudgetEnforcer.preflight(org_id, user_id, model)   ← NEW
  │     ├─ Allow                  → continue
  │     ├─ Warn(budget)           → emit BUDGET_WARNING, continue
  │     └─ Deny(budget, reason)   → mark FAILED, emit RUN_REJECTED, return
  ├─ update_run_status(RUNNING)
  ├─ ... existing flow ...
  ├─ _record_run_usage()          ← already exists
  └─ BudgetCharger.charge_run()   ← NEW (CAS, idempotent on run_id)
```

`BudgetEnforcer` short-circuits to `Allow` when no budgets exist for the tenant — so single-tenant deploys without enabled budgets pay zero added latency on `handle()`.

## Module boundaries

New package `agent_runtime/budgets/`:

- `period.py` — `BudgetPeriod` enum + `BudgetPeriod.window(now)` returns `(period_start_date, period_end_date)`. UTC midnight boundaries; month = first-of-month UTC. Pure.
- `estimator.py` — `BudgetEstimator.estimate(model, request_options)` returns `(input_tokens_estimate, cost_micro_usd_estimate)`. Conservative — uses `request_options.max_output_tokens` plus a tokenized prompt count, falls back to `RuntimeSettings.default_max_input_tokens` when output is unbounded.
- `enforcer.py` — `BudgetEnforcer(persistence, pricing_catalog, estimator).preflight(...)` returns one of `Allow`, `Warn(budget, current, limit)`, `Deny(budget, reason, current, limit)`. Tenant-cached per-call (no in-memory state survives the call).
- `charger.py` — `BudgetCharger(persistence).charge_run(run_id, org_id, user_id, model, completed_usage)` looks up matching budgets, applies CAS UPDATE per budget, idempotent on `last_charged_run_id`.

New port methods on `AsyncPersistencePort`:

- `lookup_budgets_for_run(org_id, user_id) -> Sequence[BudgetWithState]`
- `charge_budget(budget_id, period_start, delta_micro_usd, delta_tokens, run_id) -> ChargeOutcome`
- `list_budgets(org_id) -> Sequence[BudgetRecord]`
- `create_budget(record) -> BudgetRecord`
- `update_budget(budget_id, **changes) -> BudgetRecord`
- `delete_budget(budget_id) -> None`
- `query_budget_state(budget_id, period_start) -> BudgetStateRecord | None`

## Schema (migration `0009_usage_budgets.sql`)

> Note: the roadmap names this `0008_usage_budgets.sql`. RLS already took 0008 in this branch, so we use **0009** here. The migration content matches the roadmap spec verbatim for `usage_budgets`, `usage_budget_state`, and `usage_budget_reservations`.

Reservations are included from day one to handle the case where two concurrent runs each pre-flight against the same remaining budget headroom and both pass — without reservations, the second run would be admitted only to be charged into a negative spend. The reservation flow is:

```
preflight ── reserve(run_id, estimate, ttl=60s) ── if would exceed remaining → Deny
              │
              ├─ run executes
              │
post-charge ── charge_budget(run_id, observed) ── consume(reservation_id)
              │
reaper        ── purge expired (> ttl) every 30s
```

The reaper runs in the existing `usage_rollup_loop` task (which already wakes every N minutes) — no new daemon.

## Pydantic contracts

```python
class BudgetScope(StrEnum):
    ORG = "org"
    USER = "user"

class BudgetPeriod(StrEnum):
    DAY = "day"
    MONTH = "month"

class BudgetEnforcement(StrEnum):
    SOFT = "soft"
    HARD = "hard"

class BudgetStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"

class BudgetRecord(RuntimeContract):
    id: str
    org_id: str
    user_id: str | None
    scope: BudgetScope
    period: BudgetPeriod
    enforcement: BudgetEnforcement
    limit_micro_usd: int | None
    limit_tokens: int | None
    status: BudgetStatus
    created_at: datetime
    updated_at: datetime
    created_by_user_id: str

class BudgetStateRecord(RuntimeContract):
    budget_id: str
    period_start: date
    period_end: date
    current_spend_micro_usd: int = 0
    current_spend_tokens: int = 0
    row_version: int = 1
    last_charged_run_id: str | None = None
    updated_at: datetime

class BudgetWithState(RuntimeContract):
    budget: BudgetRecord
    state: BudgetStateRecord  # auto-created at period boundary if missing

class ChargeOutcome(StrEnum):
    APPLIED = "applied"
    IDEMPOTENT_NOOP = "idempotent_noop"  # last_charged_run_id matched
    EXHAUSTED_RETRIES = "exhausted_retries"
```

New event payload schemas in `runtime_api/schemas/events.py`:

```python
class BudgetWarningPayload(RuntimeContract):
    budget_id: str
    scope: BudgetScope
    period: BudgetPeriod
    current_micro_usd: int
    limit_micro_usd: int | None
    current_tokens: int
    limit_tokens: int | None
    severity: Literal["soft_cap"] = "soft_cap"

class RunRejectedPayload(RuntimeContract):
    reason: Literal["budget_exceeded"]
    budget_id: str
    scope: BudgetScope
    period: BudgetPeriod
```

New `RuntimeApiEventType` values: `BUDGET_WARNING = "budget_warning"`, `RUN_REJECTED = "run_rejected"`.

## CAS semantics (the only tricky part)

```sql
UPDATE usage_budget_state
   SET current_spend_micro_usd = current_spend_micro_usd + $delta_usd,
       current_spend_tokens    = current_spend_tokens + $delta_tokens,
       row_version             = row_version + 1,
       last_charged_run_id     = $run_id,
       updated_at              = now()
 WHERE budget_id = $budget
   AND period_start = $period_start
   AND row_version = $expected
   AND last_charged_run_id IS DISTINCT FROM $run_id
RETURNING row_version, current_spend_micro_usd
```

- 0 rows + `last_charged_run_id == $run_id` after re-read → `IDEMPOTENT_NOOP`.
- 0 rows + `row_version` advanced → re-read, retry, max 5 attempts.
- After 5 failed retries → `EXHAUSTED_RETRIES`, log + emit metric, do **not** crash the run lifecycle.

The pattern is byte-identical to `with_optimistic_retry` for `agent_runs.row_version` — we reuse that helper rather than reinvent.

## Edge cases

- **Period boundary roll-over**: `BudgetPeriod.window(now)` is pure; `lookup_budgets_for_run` does an `INSERT ... ON CONFLICT DO NOTHING` for `(budget_id, today_window_start)` so the first run after midnight UTC always finds a state row.
- **Pricing missing for model**: budget defined in `limit_micro_usd` but model has no pricing → return `Allow` and log `budget.no_pricing_for_model`. Operator opts into stricter behavior by setting `limit_tokens` instead.
- **`limit_tokens` only deploy** (single-tenant, no pricing seeded): enforcer compares `current_spend_tokens + estimate_tokens > limit_tokens`. Cost columns stay null.
- **Concurrent runs in same worker**: `claim_next` serializes per worker process; CAS handles cross-worker.
- **Worker crash between enforce and charge**: post-completion charge keys on `run_id`. Re-running the same `run_id` after recovery is a `IDEMPOTENT_NOOP`.
- **Disabled budget**: `status = 'disabled'` rows excluded by `lookup_budgets_for_run` — instant backout path.

## Security

- `usage_budgets.org_id` enforced via the existing RLS policy added in C5 (the migration adds the table to the RLS list).
- Admin endpoints (`POST/PATCH/DELETE /v1/budgets`) gated by an `admin:budgets` scope (introduced now; A10 will inventory it).
- `GET /v1/budgets/me` returns budgets matching the bearer's `(org_id, user_id)` only.

## Observability

- Counters: `budget_check_total{outcome}`, `budget_charge_total{outcome}`.
- Audit events (via existing `WorkerAuditEmitter`): `budget.created`, `budget.updated`, `budget.deleted`, `budget.warned`, `budget.deny`.

## Tests

- **Unit**:
  - `BudgetPeriod.window` — UTC midnight, month boundary.
  - `BudgetEstimator` — never under-estimates input.
  - `BudgetCharger` — CAS retry succeeds after stale; idempotency keyed on `run_id`.
- **Integration (worker)**:
  - Soft cap → emits `BUDGET_WARNING`, run proceeds.
  - Hard cap → emits `RUN_REJECTED`, no model call made, status `FAILED` with `safe_error_code='budget_exceeded'`.
  - Same run replayed via worker retry → state spend unchanged.
- **Concurrency**: two parallel runs, $1 budget remaining, $0.60 each — exactly one admitted via reservation row.
- **Reaper**: expired reservations (no charge within TTL) are released back to available headroom on the next loop tick.

## What we deliberately skip

- Mid-run interruption when a long run crosses a soft cap mid-execution. (Spec out of scope.)
- Pre-paid credits / wallet model.
