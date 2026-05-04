# PR 14 — B4: Daily Rollups and /v1/usage/\* Read Endpoints

**Spec ID:** B4 | **Track:** Token Usage | **Wave:** 3 (Parallel) | **Estimated effort:** L
**Depends on:** B1 (run usage), B3 (cost), C2
**Required for:** B5 (/context), B6 (/usage UI), B7 (budget shows current spend)

---

## 1. Functional Specification

### 1.1 Goal

Read endpoints for usage and cost — `/v1/usage/me`, `/v1/usage/org`, etc. — backed by daily rollups so a "show me this month" query never scans millions of `runtime_run_usage` rows.

### 1.2 User-visible behavior

- **End user:** in B6, sees today/7d/30d/month usage broken down by model and conversation.
- **Org admin:** sees per-user, per-model, per-conversation breakdowns.
- **Operator:** can run rollup backfill from CLI.

### 1.3 Out of scope

- UI rendering (B6).
- `/context` (B5).
- Budget enforcement (B7).
- Materialized views (we use explicit tables; idempotent UPSERT avoids concurrent-refresh foot-guns).

---

## 2. Technical Specification

### 2.1 Architecture

- Two daily rollup tables: per-user and per-org. Refreshed by a worker loop, NOT materialized views.
- Loop recomputes the last 2 days every N minutes — yesterday continues to update for late-arrival window, then is finalized.
- Endpoints prefer rollup tables; cold-start fallback queries `runtime_run_usage` directly with a 30-day cap.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0006_usage_daily_rollups.sql`:

```sql
CREATE TABLE runtime_usage_daily_user (
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    day                 DATE NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    runs_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, user_id, day, model_provider, model_name)
);
CREATE INDEX idx_runtime_usage_daily_user_org_day
    ON runtime_usage_daily_user (org_id, day DESC);

CREATE TABLE runtime_usage_daily_org (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    runs_count          INTEGER NOT NULL,
    distinct_users      INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, model_provider, model_name)
);
CREATE INDEX idx_runtime_usage_daily_org_day
    ON runtime_usage_daily_org (org_id, day DESC);
```

### 2.3 Endpoints

All under `/v1/usage/*`. Tenant-scoped via `RuntimeApiRoutes.scoped_identity`.

- `GET /v1/usage/me?period={today|7d|30d|month}` →
  ```ts
  {
    period: { start: ISO, end: ISO },
    total: { input, output, cached_input, total, cost_micro_usd | null, runs_count },
    by_day: [{ day: "2026-05-04", total: ..., cost_micro_usd | null }],
    by_model: [{ provider, model, total: ..., cost_micro_usd | null }]
  }
  ```
- `GET /v1/usage/me/conversations?period=&limit=10` — top conversations by total_tokens.
- `GET /v1/usage/runs/{run_id}` — single-run breakdown (joins B1 + B2).
- `GET /v1/usage/conversations/{conversation_id}` — per-conversation totals.
- `GET /v1/usage/org?period=&group_by=user|model|conversation` — admin scope only (RBAC; A10).

Mirrored on facade.

### 2.4 Code changes

**New schemas** `services/ai-backend/src/runtime_api/schemas/usage.py`:

- `UsageTotals`, `UsageDailyRow`, `UsageModelRow`, `UsageMeResponse`, `UsageOrgResponse`, `RunUsageBreakdown`, `ConversationUsageResponse`.
- `cost_micro_usd: int | None` — never floats. Currency code `"USD"` returned alongside.

**New service** `services/ai-backend/src/agent_runtime/api/usage_service.py` — `UsageQueryService` with period-to-date-range translation, shared between API and rollup loop.

**New repository methods** in [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py):

- `query_user_daily(org_id, user_id, range)`, `query_org_daily(org_id, range, group_by)`, `query_run_breakdown(org_id, run_id)`, `query_top_conversations(org_id, user_id, range, limit)`, `refresh_daily_rollups(start_day, end_day)`.

**New routes** in [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py) — `UsageApiRoutes`, registered at `/v1/usage`.

**New rollup loop** `services/ai-backend/src/runtime_worker/usage_rollup_loop.py`:

- Long-running task launched by `__main__.py`.
- Every `USAGE_ROLLUP_INTERVAL_SECONDS` (default 600) recomputes the last 2 days via UPSERT.
- Yesterday's rows are finalized once `now() > yesterday_end + USAGE_LATE_ARRIVAL_WINDOW_MINUTES`.
- Idempotent — running twice for the same day yields one row.

**Cold-start fallback:** when rollups empty for a requested range, query `runtime_run_usage` directly with a 30-day cap, log a warning ("rollups not warm").

**Facade:**

- [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py) — forwarding handlers for `/v1/usage/*`.
- [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts) — mirror response types.

### 2.5 Trust model & failure semantics

- All routes go through scoped_identity; `org_id` comes from verified token.
- `/v1/usage/org` requires admin scope (RBAC enforcement in A10; until A10, soft-check via roles header).
- Cold-start fallback caps at 30 days to prevent accidental full-table scans.

### 2.6 Tenant isolation

- Endpoints filter by `org_id`.
- `/v1/usage/me` filters by both `org_id` and `user_id`.
- Cross-tenant test required: user in org_a calling `/v1/usage/runs/<org_b_run>` → 404.

### 2.7 Observability

- Metrics: `usage_endpoint_request_total{endpoint,outcome}`, `usage_rollup_refresh_seconds`, `usage_rollup_lag_seconds`.
- Cold-start fallback logs `usage_rollup_cold_start org_id=… range=… cap=30d`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `GET /v1/usage/me?period=month` returns expected shape.
- [ ] Rollup is exact: SUM over rollup rows for an org+day equals SUM over `runtime_run_usage` for the same range.
- [ ] Rollup is idempotent — second run produces same rows.
- [ ] Tenant scoping enforced at SQL and endpoint level.
- [ ] Cost columns return `null` when unseeded; UI side handles this (B6).

### 3.2 Test plan

**Unit:**

- Period parsing: today, 7d, 30d, month — all UTC.
- Rollup arithmetic correctness for fixture data.
- `pii_purged_at IS NOT NULL` rows excluded from per-user rollup, included in per-org totals.

**Integration:**

- Insert 1k synthetic runs across 5 days for 3 users → rollup → endpoint returns expected shape.
- Tenant isolation: user_a's `/me` excludes user_b's data even within same org? (User-level filter ensures yes.)
- Cross-tenant: org_a's user attempting `/v1/usage/runs/<org_b_run>` → 404.

**Performance:**

- Synthetic 1M-row `runtime_run_usage` table → `/v1/usage/me?period=month` finishes < 50ms (rollup table only).

### 3.3 Compliance evidence produced

- Per-user, per-org usage queryable for billing audit.
- Aggregations preserve org isolation.

### 3.4 Rollout plan

- Tables ship empty.
- Worker loop, on first start, backfills historical days (one-shot then incremental).
- Endpoints fall back to direct query during cold-start (capped + logged).

### 3.5 Backout plan

Stop the rollup loop; drop the rollup tables. Endpoints fall back to direct query (slow but correct).

### 3.6 Definition of done

- [ ] Migration 0006 applied.
- [ ] Rollup loop running.
- [ ] Endpoints + facade forwarding live.
- [ ] api-types updated.
- [ ] Performance test demonstrates < 50ms p99 with rollups warm.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0006_usage_daily_rollups.sql` (+ rollback)
- New: `services/ai-backend/src/runtime_api/schemas/usage.py`
- New: `services/ai-backend/src/agent_runtime/api/usage_service.py`
- New: `services/ai-backend/src/runtime_worker/usage_rollup_loop.py`
- Modify: [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py)
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — query methods
- Modify: [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py) — forward routes
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
- Modify: [services/ai-backend/src/runtime_worker/**main**.py](../../services/ai-backend/src/runtime_worker/__main__.py) — launch rollup loop
