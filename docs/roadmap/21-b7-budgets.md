# PR 21 — B7: Per-org and Per-user Budget Enforcement (Atomic CAS)

**Spec ID:** B7 | **Track:** Token Usage | **Wave:** 5 (Usage UX + Budgets) | **Estimated effort:** L
**Depends on:** B1, B3, B4, C3 (optimistic-lock pattern)
**Required for:** none (capstone of usage track)

---

## 1. Functional Specification

### 1.1 Goal

Enforce per-org and per-user spend caps. Soft caps emit warnings; hard caps prevent runs from starting. Atomicity matters: two concurrent runs must not both pass a check that allows only one.

### 1.2 User-visible behavior

- **End user:** sees a `BUDGET_WARNING` event when over soft cap; sees a clear "budget exceeded" error when hard cap hits, distinct from generic failures.
- **Org admin:** can create/update/delete budgets; can see current spend vs limit.
- **Operator:** sees `RUN_REJECTED` events instead of `RUN_FAILED` when refused for budget.

### 1.3 Out of scope

- Mid-run interruption when budget crosses threshold (we check pre-run only).
- Feature-level budgets (e.g. "this skill costs more").
- Pre-paid credits / wallet model.

---

## 2. Technical Specification

### 2.1 Architecture

- Two tables: `usage_budgets` (config) and `usage_budget_state` (current period state).
- Compare-and-swap on `row_version` AND `last_charged_run_id IS DISTINCT FROM $run_id` ensures idempotency (same run never double-charges) AND concurrency safety.
- Pre-run check uses `BudgetEnforcer.preflight(...)` returning `Allow`/`Warn(budget)`/`Deny(budget, reason)`.
- For concurrency safety where reservations matter, optional `usage_budget_reservations` table with TTL reaper holds pre-flight estimates so two concurrent runs don't both pass.
- New event types `BUDGET_WARNING` and `RUN_REJECTED`.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0008_usage_budgets.sql`:

```sql
CREATE TABLE usage_budgets (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    user_id             TEXT,                          -- NULL = org-scope
    scope               TEXT NOT NULL CHECK (scope IN ('org','user')),
    period              TEXT NOT NULL CHECK (period IN ('day','month')),
    enforcement         TEXT NOT NULL CHECK (enforcement IN ('soft','hard')),
    limit_micro_usd     BIGINT,                         -- nullable; if NULL, limit_tokens used
    limit_tokens        BIGINT,                         -- token-only fallback for cost-disabled deploys
    status              TEXT NOT NULL CHECK (status IN ('active','disabled')),
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    created_by_user_id  TEXT NOT NULL,
    UNIQUE (org_id, COALESCE(user_id, '<org>'), scope, period)
);

CREATE TABLE usage_budget_state (
    budget_id                  TEXT NOT NULL REFERENCES usage_budgets(id),
    period_start               DATE NOT NULL,
    period_end                 DATE NOT NULL,
    current_spend_micro_usd    BIGINT NOT NULL DEFAULT 0,
    current_spend_tokens       BIGINT NOT NULL DEFAULT 0,
    row_version                INTEGER NOT NULL DEFAULT 1,
    last_charged_run_id        TEXT,
    updated_at                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (budget_id, period_start)
);

CREATE TABLE usage_budget_reservations (
    reservation_id     TEXT PRIMARY KEY,
    budget_id          TEXT NOT NULL REFERENCES usage_budgets(id),
    period_start       DATE NOT NULL,
    run_id             TEXT NOT NULL,
    reserved_micro_usd BIGINT NOT NULL,
    reserved_tokens    BIGINT NOT NULL,
    expires_at         TIMESTAMPTZ NOT NULL,
    consumed_at        TIMESTAMPTZ
);
CREATE INDEX idx_usage_budget_reservations_active
    ON usage_budget_reservations (budget_id, period_start) WHERE consumed_at IS NULL;
CREATE INDEX idx_usage_budget_reservations_expiring
    ON usage_budget_reservations (expires_at) WHERE consumed_at IS NULL;
```

### 2.3 Events

New `RuntimeApiEventType`:

- `BUDGET_WARNING` payload `{budget_id, scope, period, current_micro_usd, limit_micro_usd, severity}`.
- `RUN_REJECTED` payload `{reason, budget_id, period, ...}`. **Distinct from `RUN_FAILED`** so the UI shows "budget exceeded" rather than generic error.

### 2.4 Endpoints

- `GET /v1/budgets` (admin) — list org budgets.
- `POST /v1/budgets` (admin) — create.
- `PATCH /v1/budgets/{budget_id}` (admin).
- `DELETE /v1/budgets/{budget_id}` (admin).
- `GET /v1/budgets/me` — what budgets currently apply to me + remaining.

### 2.5 Code changes

**New port methods** in adapters:

- `lookup_budgets_for_run(org_id, user_id) -> list[BudgetWithState]`
- `reserve_budget(budget_id, period_start, run_id, reserved_micro_usd, reserved_tokens, ttl_seconds) -> bool`
- `charge_budget(budget_id, period_start, delta_micro_usd, delta_tokens, run_id) -> ChargeResult` — CAS-based:

```sql
UPDATE usage_budget_state
SET current_spend_micro_usd = current_spend_micro_usd + $delta,
    current_spend_tokens    = current_spend_tokens + $delta_tokens,
    row_version             = row_version + 1,
    last_charged_run_id     = $run_id,
    updated_at              = now()
WHERE budget_id = $budget AND period_start = $period_start
  AND row_version = $expected
  AND last_charged_run_id IS DISTINCT FROM $run_id   -- idempotency
RETURNING row_version, current_spend_micro_usd
```

If 0 rows: re-read, retry. If after N retries (default 5) still mismatched, fail and let caller decide.

**New module** `services/ai-backend/src/agent_runtime/budgets/`:

- `enforcer.py` — `BudgetEnforcer.preflight(org_id, user_id, model, request_options)` returns `Allow|Warn(budget)|Deny(budget, reason)`.
- `estimator.py` — pre-run estimate (use `request_options.max_output_tokens` + token-counted prompt; conservative when output is unknown).
- `period.py` — period start/end calculation in UTC (day = UTC midnight; month = first of UTC month).
- `reservations.py` — reserve, release, consume. Reaper task purges expired.

**Worker hook** in [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py) `handle()`:

- New step at top, immediately after run loaded, BEFORE `update_run_status(RUNNING)`:
  - `BudgetEnforcer.preflight(...)`.
  - `Deny` → `update_run_status(FAILED)` with `safe_error_code='budget_exceeded'`, append `RUN_REJECTED` event, return.
  - `Warn` → append `BUDGET_WARNING` event and continue.
- After PR 11's `record_run_usage`: look up matching budgets, call `charge_budget(...)` for each. Idempotent on `last_charged_run_id`.

**New cron** in [services/ai-backend/src/runtime_worker/usage_rollup_loop.py](../../services/ai-backend/src/runtime_worker/usage_rollup_loop.py) (extend) — at UTC day/month boundary, INSERT new `(budget_id, new_period_start)` rows.

**Facade** + **api-types** — `BUDGET_WARNING` and `RUN_REJECTED` event variants; `/v1/budgets/*` endpoints.

### 2.6 Trust model & failure semantics

- Atomicity by CAS + reservation table.
- Idempotency via `last_charged_run_id`: same run can never double-charge.
- Single-tenant deploy with no budgets: `BudgetEnforcer.preflight` short-circuits → `Allow` immediately, zero added latency.
- If pricing not seeded but budget is in micro-USD: budget check returns `Allow` and logs "no pricing for model X; cannot enforce budget". Operator's call.
- Reservation reaper TTL: default 60s — covers worst-case pre-flight-to-completion delay.

### 2.7 Tenant isolation

- `usage_budgets` carries `org_id`; reads filter by `org_id`.
- `/v1/budgets` admin scope required.

### 2.8 Observability

- Audit: `budget.created`, `budget.updated`, `budget.deleted`, `budget.warned`, `budget.deny`.
- Metrics: `budget_check_total{outcome}`, `budget_charge_total{outcome}`, `budget_reservation_active`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Set per-user $1 daily hard budget → next run that would exceed → `RUN_REJECTED` event, no model call made.
- [ ] Soft cap → `BUDGET_WARNING` event, run proceeds.
- [ ] Two concurrent runs with budget remaining = $1 cost, each estimating $0.60 → exactly one admitted, the other denied.
- [ ] Same run cannot be double-charged on retry.
- [ ] `GET /v1/budgets/me` returns active budgets + remaining.
- [ ] Single-tenant deploy without budgets: zero added latency on run handle.

### 3.2 Test plan

**Unit:**

- Estimator never under-estimates input (worst-case token count).
- CAS retry succeeds after stale.
- Idempotency: same run_id charges once even on N replays.
- Period boundary: day rolls at UTC midnight.

**Concurrency (integration):**

- Two parallel runs as described above — exactly one admitted.
- Soft cap emits warning, doesn't block.
- Hard cap blocks before any model call.

### 3.3 Compliance evidence produced

- Per-tenant cost control demonstrably enforced.
- `RUN_REJECTED` event distinguishable from `RUN_FAILED` in audit log.

### 3.4 Rollout plan

Forward-only; budgets default off per org. Per-org admin enables.

### 3.5 Backout plan

Set all budgets `status='disabled'`. Worker preflight short-circuits to Allow.

### 3.6 Definition of done

- [ ] Migration 0008 (ai-backend) applied.
- [ ] Pre-run check + post-run charge wired.
- [ ] CAS + reservation atomicity test passes.
- [ ] api-types updated.
- [ ] Admin endpoints + facade forwarding live.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0008_usage_budgets.sql` (+ rollback)
- New: `services/ai-backend/src/agent_runtime/budgets/{enforcer,estimator,period,reservations}.py`
- Modify: persistence ports + adapters
- Modify: [services/ai-backend/src/runtime_worker/handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py) — preflight + charge hooks
- Modify: [services/ai-backend/src/runtime_worker/usage_rollup_loop.py](../../services/ai-backend/src/runtime_worker/usage_rollup_loop.py) — period roll-over + reservation reaper
- Modify: `services/ai-backend/src/runtime_api/schemas/common.py` — event types
- Modify: [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py)
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
