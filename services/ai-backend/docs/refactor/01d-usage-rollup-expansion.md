# Sub-PRD 01d — Rollup Expansion

**Status:** Shipped 2026-05-11 (backend + api-types + facade; FE deferred — see §11)
**Parent:** [01-usage-capture-and-attribution.md](01-usage-capture-and-attribution.md)
**Position in plan:** P11.7.d (last of four sub-PRDs)
**Depends on:** [01a](01a-usage-normalized-token-shape.md) ✅, [01b](01b-usage-attribution-context.md) ✅, [01c](01c-usage-recorder.md) ✅
**Risk:** Medium. Schema migration (PK extension); five surfaces touched (record, rollup loop, API, api-types, FE).

> **What this PR is.** The captured per-call rows now carry every attribution dimension a cost report could want (model, connector, subagent, purpose, plus seven token kinds). But the rollup tables are stuck on the old dimensions — connector drops `model_name`, subagent and purpose have no rollup table at all. 01d expands the rollup surface so the captured data is actually queryable at scale. Schema-additive, FE-additive, no behavior regression.

---

## 1. Problem

After 01a/01b/01c the capture layer is rich:

- Per-call row carries `subagent_id`, `connector_slug`, `purpose`, `originating_tool_*`, and seven token kinds.
- Run-level row carries the same seven token kinds.

But the rollup layer is from 2026-04 and earlier:

| Rollup table                    | Keyed on                                             | Misses                                            |
| ------------------------------- | ---------------------------------------------------- | ------------------------------------------------- |
| `runtime_usage_daily_user`      | `(org_id, user_id, day, model_provider, model_name)` | New token kinds; no subagent / purpose dimensions |
| `runtime_usage_daily_org`       | `(org_id, day, model_provider, model_name)`          | New token kinds; no subagent / purpose            |
| `runtime_usage_daily_connector` | `(org_id, day, connector_slug)`                      | **Drops `model_name` entirely** + new token kinds |

Concrete questions the workspace `Usage` panel cannot answer today:

- "GPT-5 cost for jira last week" — connector rollup has no `model_name`.
- "How much did the `researcher` subagent cost across all runs last month" — no subagent rollup.
- "What share of org spend is context compression vs main vs tool interpretation" — no purpose rollup.

Reading the raw `runtime_model_call_usage` table for these answers works at small scale but doesn't scale to the org-wide / 30-day queries the panel does today via rollups.

---

## 2. Goals

1. **Extend connector rollup with `model_name`.** PK becomes `(org_id, day, connector_slug, model_name)`. Existing rows take `model_name=''`.
2. **Two new rollup tables.** `runtime_usage_daily_subagent` (org-scoped, keyed on `subagent_slug`) and `runtime_usage_daily_purpose` (org-scoped, keyed on `purpose`). Both carry all seven token kinds + cost.
3. **Rollup loop expansion.** New `_SubagentRollupBucket` + `_PurposeRollupBucket` accumulators; new upsert ports + adapter implementations.
4. **Two new endpoints.** `/v1/usage/org/subagents` and `/v1/usage/org/purpose`. Org-scoped (matches the connector-rollup pattern; no `/me/*` variants — subagent / purpose breakdowns are a workspace-admin lens). Cold-start fallback identical to existing `/org` endpoint.
5. **api-types + FE.** New contracts; new sections in `UsageWorkspaceView` (no new tab — the workspace tab already aggregates org-scoped views).
6. **Backfill via existing 30-day cold-start window.** No separate migration job; the rollup loop's existing one-shot backfill on `start()` populates new tables from `runtime_model_call_usage` history.

## 3. Non-goals

- **Extending user / org rollup tables with new token kinds.** Those rollups already work for their dimension; adding columns is cheap but the FE / api-types don't surface the new kinds yet. Deferred until a real report needs them.
- **`/me/subagents` / `/me/purpose`.** Personal-scope subagent / purpose breakdowns aren't a current product ask; the panel pattern is org-scoped admin views.
- **Pricing rate columns for new token kinds.** P12 lands those + LiteLLM source. Cost columns on new rollup tables sum the existing `cost_micro_usd` field; new-kind tokens contribute $0 until P12.
- **`distinct_users` on subagent / purpose rollups.** The per-call row has no `user_id`; computing distinct users requires JOINing against `runtime_run_usage`. Org-scoped reports don't need per-user counts; if a future personal-scope endpoint lands, add the JOIN there.

## 4. Architecture

### 4.1 Connector rollup PK extension

Migration 0029:

```sql
-- Add model_name column with empty default so pre-existing rows
-- coalesce into a single "(no model)" bucket.
ALTER TABLE runtime_usage_daily_connector
    ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT '';

-- Replace PK so each (org, day, slug) can now have multiple
-- model_name rows. AccessExclusive lock during rebuild; no full
-- table rewrite (existing rows are already unique on the broader key).
ALTER TABLE runtime_usage_daily_connector
    DROP CONSTRAINT runtime_usage_daily_connector_pkey,
    ADD CONSTRAINT runtime_usage_daily_connector_pkey
        PRIMARY KEY (org_id, day, connector_slug, model_name);
```

The bucket key in `UsageQueryService.rollup_connector_rows` extends to include `model_name`. The upsert ON CONFLICT clause adds it.

`UsageDailyConnectorRow` (telemetry.py) gains `model_name: str` field.

### 4.2 New: `runtime_usage_daily_subagent`

```sql
CREATE TABLE IF NOT EXISTS runtime_usage_daily_subagent (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    subagent_slug       TEXT NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    call_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    cache_creation_input_tokens BIGINT NOT NULL,
    reasoning_tokens    BIGINT NOT NULL,
    audio_input_tokens  BIGINT NOT NULL,
    audio_output_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, subagent_slug, model_provider, model_name)
);

CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_subagent_org_day
    ON runtime_usage_daily_subagent (org_id, day DESC);

ALTER TABLE runtime_usage_daily_subagent ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_usage_daily_subagent
    USING (org_id = current_setting('app.current_org', true));
```

- `subagent_slug` is the empty string for orchestrator-scope calls (matches the connector rollup's "(unattributed)" pattern).
- `call_count` mirrors connector rollup's runs_count semantic — number of distinct `(message_id)` rows that landed in this bucket. Useful for "avg cost per call."
- No `distinct_users` (see §3 non-goal).

### 4.3 New: `runtime_usage_daily_purpose`

```sql
CREATE TABLE IF NOT EXISTS runtime_usage_daily_purpose (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    purpose             TEXT NOT NULL,
    model_provider      TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    call_count          INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    cache_creation_input_tokens BIGINT NOT NULL,
    reasoning_tokens    BIGINT NOT NULL,
    audio_input_tokens  BIGINT NOT NULL,
    audio_output_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, purpose, model_provider, model_name)
);

CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_purpose_org_day
    ON runtime_usage_daily_purpose (org_id, day DESC);

ALTER TABLE runtime_usage_daily_purpose ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runtime_usage_daily_purpose
    USING (org_id = current_setting('app.current_org', true));
```

- `purpose` is the StrEnum value (`main` / `tool_planning` / `tool_interpretation` / `subagent_work` / `context_compression`).

### 4.4 Rollup loop expansion

`UsageQueryService` adds:

```python
def rollup_subagent_rows(
    self,
    *,
    records: Iterable[RuntimeModelCallUsageRecord],
    refreshed_at: datetime,
) -> Iterable[UsageDailySubagentRow]: ...

def rollup_purpose_rows(
    self,
    *,
    records: Iterable[RuntimeModelCallUsageRecord],
    refreshed_at: datetime,
) -> Iterable[UsageDailyPurposeRow]: ...
```

Both iterate `runtime_model_call_usage` records and aggregate via new `_SubagentRollupBucket` / `_PurposeRollupBucket` accumulators.

`UsageRollupLoop` adds two refresh blocks parallel to the existing connector path; reuses the per-row try/except + retry semantics.

### 4.5 New ports

`PersistencePort` adds:

```python
async def upsert_subagent_daily_usage(self, row: UsageDailySubagentRow) -> None: ...
async def upsert_purpose_daily_usage(self, row: UsageDailyPurposeRow) -> None: ...
async def query_subagent_daily_usage_for_org(
    self, *, org_id: str, start: datetime, end: datetime
) -> Sequence[UsageDailySubagentRow]: ...
async def query_purpose_daily_usage_for_org(
    self, *, org_id: str, start: datetime, end: datetime
) -> Sequence[UsageDailyPurposeRow]: ...
```

Two adapters implement (in-memory + postgres). UPSERT shape mirrors `upsert_connector_daily_usage`.

### 4.6 Two new HTTP endpoints

`GET /v1/usage/org/subagents?period=...` → `UsageOrgSubagentsResponse`
`GET /v1/usage/org/purpose?period=...` → `UsageOrgPurposeResponse`

Both follow the existing `/v1/usage/org` pattern:

- Period selection (week / month / 30d).
- Cold-start fallback: read rollup first; if empty within 10-minute window, live-rollup from `runtime_model_call_usage`.
- Auth: same `audit:read OR admin:users` scope as `/v1/usage/org`.

Response shape:

```python
class UsageOrgSubagentRow(RuntimeContract):
    subagent_slug: str
    model_provider: str
    model_name: str
    call_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt
    reasoning_tokens: NonNegativeInt
    audio_input_tokens: NonNegativeInt
    audio_output_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None


class UsageOrgSubagentsResponse(RuntimeContract):
    period: PeriodSelector
    currency: str = "usd"
    rows: tuple[UsageOrgSubagentRow, ...]
    cold_start_fallback: bool = False
```

Purpose response is identical shape with `purpose: str` in place of `subagent_slug`.

### 4.7 api-types

Two new interfaces in `packages/api-types/src/index.ts`:

```ts
export interface UsageOrgSubagentRow {
  subagent_slug: string;
  model_provider: string;
  model_name: string;
  call_count: number;
  // ... all 7 token kinds
  cost_micro_usd: number | null;
}

export interface UsageOrgSubagentsResponse {
  period: PeriodSelector;
  currency: string;
  rows: UsageOrgSubagentRow[];
  cold_start_fallback: boolean;
}

// Mirrors for purpose.
```

`UsageConnectorRow` also gets a `model_name: string` field (optional for old api-types consumers; new in 01d).

### 4.8 Facade wiring

`services/backend-facade/src/backend_facade/app.py` adds two `@app.get` stanzas modeled on the existing `/v1/usage/org` proxy. Same auth + forwarding pattern.

### 4.9 Frontend

`apps/frontend/src/features/chat/components/details/UsageWorkspaceView.tsx` gains two new sections under the existing workspace tab:

- **By subagent** — table of `subagent_slug × model × tokens × cost`. Top N by cost, descending.
- **By purpose** — table of `purpose × tokens × cost`. Five rows max (one per enum value); useful for "what share of spend is tool-interpretation vs main."

Two new hooks: `useUsageOrgSubagents()` and `useUsageOrgPurpose()` modeled on `useUsageOrg`. State held in-view (no Redux); React Query handles caching.

No new tab. The existing workspace tab is the natural home — both sections are org-admin lenses.

---

## 5. Files touched

### Added

- `agent_runtime/persistence/records/telemetry.py` — `UsageDailySubagentRow`, `UsageDailyPurposeRow`. `UsageDailyConnectorRow` gains `model_name`.
- `runtime_api/schemas/usage.py` — `UsageOrgSubagentRow`, `UsageOrgSubagentsResponse`, `UsageOrgPurposeRow`, `UsageOrgPurposeResponse`.
- `migrations/0029_usage_rollup_subagent_purpose.sql` + `.rollback.sql` — three schema changes (connector PK extension + two new tables).
- `tests/unit/agent_runtime/api/test_usage_rollup_subagent_purpose.py` — rollup builder tests + endpoint contract tests.

### Modified

- `agent_runtime/api/ports.py` — four new methods on `PersistencePort`.
- `runtime_adapters/in_memory/runtime_api_store.py` — four implementations.
- `runtime_adapters/postgres/runtime_api_store.py` — four implementations.
- `agent_runtime/api/usage_service.py` — `rollup_subagent_rows`, `rollup_purpose_rows`, `_SubagentRollupBucket`, `_PurposeRollupBucket`. `rollup_connector_rows` extends bucket key to include `model_name`.
- `runtime_worker/usage_rollup_loop.py` — two new refresh blocks parallel to connector.
- `runtime_api/http/routes.py` — two new handlers under `UsageApiRouter`.
- `packages/api-types/src/index.ts` — new types + `model_name` on `UsageConnectorRow`.
- `services/backend-facade/src/backend_facade/app.py` — two new proxy stanzas.
- `apps/frontend/src/features/chat/components/details/UsageWorkspaceView.tsx` — two new sections.
- `apps/frontend/src/features/chat/components/details/hooks/useUsageOrgSubagents.ts` + `useUsageOrgPurpose.ts` (new).

### Not modified

- `runtime_usage_daily_user` / `runtime_usage_daily_org` tables — unchanged.
- `runtime_run_usage` / `runtime_model_call_usage` — unchanged.
- Budget enforcer / budget reservations — unchanged (audit confirmed no rollup-table reads).
- `/v1/usage/me`, `/v1/usage/conversations/*`, `/v1/usage/runs/*` — unchanged.

---

## 6. Behaviors preserved

| Behavior                                                | How                                                                                                                       |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `/v1/usage/me` cold-start fallback                      | Unchanged; that path uses live scan.                                                                                      |
| `/v1/usage/org` returns its existing fields             | New fields are additive; old contract unchanged.                                                                          |
| Rollup loop cadence (600s) + 30-day cold-start backfill | Unchanged. New blocks slot inside the same `_run` cycle.                                                                  |
| Connector rollup grouping by `connector_slug`           | Old aggregates collapse `model_name=''` (the default for pre-migration rows); new rows split. FE handles the wider grain. |
| Budget enforcement / reservations                       | No dependency on rollup tables — unaffected.                                                                              |
| Postgres RLS on rollup tables                           | New tables get inline `ENABLE ROW LEVEL SECURITY` + tenant policy.                                                        |
| FE workspace tab structure                              | Two sections added at the bottom; no tab restructure.                                                                     |

---

## 7. Risks

| Risk                                                                                               | Likelihood | Impact | Mitigation                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PK rebuild on `runtime_usage_daily_connector` blocks ops queries briefly                           | Low        | Low    | Single AccessExclusive lock for the index rebuild; existing rows already satisfy uniqueness under the broader key. Production has tens of thousands of rows at most — measured in milliseconds.      |
| Pre-migration connector rollup rows collapse to `model_name=''` and break FE grouping              | Low        | Medium | FE renders `model_name=''` as "(no model)" label; api-types `model_name` is string (non-nullable) with empty-string sentinel matching the connector_slug pattern.                                    |
| `runtime_usage_daily_subagent` grows fast under many subagent definitions                          | Low        | Low    | Bounded by `(org_id, day, subagent_slug, model_provider, model_name)` — even 100 subagents × 30 days × 5 models × 100 orgs is 1.5M rows. Trivial for Postgres.                                       |
| Adding endpoints means facade-side wiring that's easy to forget                                    | Medium     | Medium | PRD §4.8 calls out the explicit-per-route facade pattern; tests below assert the facade proxies (existing pattern test extended).                                                                    |
| Cold-start fallback for new endpoints double-counts when both the rollup and live scan return rows | Low        | Medium | Existing pattern reads rollup first; if empty, runs `rollup_*_rows` over the live scan window in-process (Python aggregation), returning the live aggregate without writing. Same as `/v1/usage/me`. |

---

## 8. Tests

### 8.1 Unit — rollup builders

`test_usage_rollup_subagent_purpose.py`:

- `rollup_subagent_rows` groups by `(org_id, day, subagent_slug, model_provider, model_name)` and sums every token kind.
- Records with `subagent_id=None` fall into the `''` bucket (orchestrator-scope).
- `rollup_purpose_rows` groups by `(org_id, day, purpose, model_provider, model_name)`; default `'main'` purpose handled.
- Both builders preserve `cost_micro_usd` sum across records when present, return `None` when every input cost is `None`.

### 8.2 Adapter — upsert + query

- `in_memory.upsert_subagent_daily_usage` is idempotent on the natural key.
- `query_subagent_daily_usage_for_org` returns rows within `(start, end]` only.
- Same pair for purpose.

### 8.3 Endpoint contract

- `GET /v1/usage/org/subagents` requires `audit:read OR admin:users` (matches `/v1/usage/org`).
- Returns `UsageOrgSubagentsResponse` with rows shape matching api-types.
- Cold-start fallback: when the rollup table is empty, the handler runs `rollup_subagent_rows` over live records and returns the synthesized rows with `cold_start_fallback=True`.

### 8.4 Connector rollup extension

- `rollup_connector_rows` bucket now groups by `model_name`; existing tests update to include `model_name=""` where applicable.
- Pre-migration row reads (no `model_name` column) coalesce to `model_name=''` — but since migration runs before app code reads new rows, this is academic. Pinned by an in-memory test that constructs a record with `model_name=""`.

### 8.5 Regression

All existing `/v1/usage/*` tests pass unchanged. The `UsageConnectorRow` shape gains an optional field; FE consumers ignore unknown fields by convention.

---

## 9. Rollout / rollback

### 9.1 Rollout

One PR. Sequence:

1. Migration 0029 lands.
2. Pydantic records + ports + adapters land.
3. Rollup loop + builders.
4. Endpoints + schemas.
5. api-types + facade wiring.
6. FE sections.
7. Tests.

### 9.2 Rollback

`git revert`. Migration rollback drops the two new tables and reverts the connector PK to `(org_id, day, connector_slug)` (an AccessExclusive PK swap; data preserved). FE hides the new sections (the hooks return errors gracefully because the endpoints 404; the sections render empty states).

---

## 10. Done

- ✅ Migration 0029 + rollback landed.
- ✅ New rollup records + ports + adapters + builders + loop refresh blocks landed.
- ✅ Two new endpoints + facade proxies + api-types landed.
- ✅ Tests green: 11 rollup-builder tests + 4 endpoint contract tests (subagent + purpose); full ai-backend suite 1640 passing.
- ✅ api-types typecheck green.
- Parent PRD §4 row ticked.
- ⏸ **FE sections deferred** — see §11.

## 11. What deferred

The FE `UsageWorkspaceView` sections (rendering tables for the new
`UsageOrgSubagentsResponse` / `UsageOrgPurposeResponse` data) are a
small follow-up:

- Add two hooks (`useUsageOrgSubagents`, `useUsageOrgPurpose`)
  modeled on `useUsageOrg`.
- Two table sections inside `UsageWorkspaceView`, sorted by
  cost desc, rendering all seven token kinds.
- Empty state for the cold-start window (~10 minutes after deploy).

This is purely presentation; the data + endpoints are live. Workspace
admins can already query `/v1/usage/org/subagents` and
`/v1/usage/org/purpose` directly. FE wiring is queued for the next
frontend-focused PR.
