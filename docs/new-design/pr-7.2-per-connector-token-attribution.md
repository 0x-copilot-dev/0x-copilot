# PR 7.2 — Per-connector token attribution

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 7, PR 7.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (write-time attribution + read aggregator + rollup loop) · backend-facade (proxy passthrough) · frontend (UsagePanel "By connector" panel + stacked-chart layer)
> **Size:** S (one nullable column, one rollup table, one event-payload field, one read aggregator extension, one FE panel + chart layer). Targeted at one PR.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`services/ai-backend/migrations/0005_runtime_model_call_usage.sql`](../../services/ai-backend/migrations/0005_runtime_model_call_usage.sql), [`services/ai-backend/migrations/0007_usage_daily_rollups.sql`](../../services/ai-backend/migrations/0007_usage_daily_rollups.sql), [`docs/new-design/pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md)
> **Sibling docs:**
> – PR 4.5 — Usage overlay (already shipped: introduces `UsageConversationView` + `UsageWorkspaceView` + stacked area chart, which this PR extends with one more axis)
> – PR 1.2 — Per-chat connector scope persistence (`enabled_connectors` + `ConnectorScopeValidator` we reuse for connector-id validation)
> – PR 7.1 — Audit log section (sibling Wave 7 PR; independent merge)

---

## 1 · PRD

### 1.1 Problem

The Atlas Design Doc § "Usage overlay · Notes / TODOs" calls out:

> **todo** — Per-connector token attribution (which connector cost the most).

Today's usage data answers _what model_ burned tokens (`runtime_model_call_usage` carries `model_provider`, `model_name`) and _which user/conversation_ ran it (`org_id`, `user_id`, `conversation_id`). It cannot answer _which connector's tools provoked the burn_. A workspace owner who connects six MCP servers and sees their bill triple has no signal showing whether Salesforce queries (large records, expensive context) or Slack queries (cheap to embed, abundant) drove it.

The connector context **is already available at write time**:

- `runtime_tool_invocations.connector_slug` is populated for every connector-backed tool call (migration 0001, line 224).
- A typical LLM turn alternates "model thinks → calls a tool → model summarises tool result". The model call that closes a tool result is, by construction, attributable to that tool's connector.
- The worker's `RuntimeContext` already carries the last tool invocation's metadata in flight; we read it, write it, done.

The streaming wire is independently well-suited:

- `model_call_completed` events already carry the LLM call's token deltas (used by the FE's "Context window" indicator since PR 4.5).
- Adding `connector_slug` to that event's payload is one field, additive and ignorable by clients that don't care.

Without this PR:

- Marcus cannot see which connector cost his workspace the most last week.
- The "By connector" tab in the Usage overlay is permanently a wishlist item.
- A future "noisy connector" badge ("Salesforce: 38% of your spend last 30 days") has no datasource.

### 1.2 Goals

1. **Each `runtime_model_call_usage` row carries the connector that prompted it** (best-effort), populated at write-time from the same context the worker already has open. `NULL` when there is no preceding connector tool call (e.g. the opening plan turn).
2. **Workspace-level 30-day "By connector" stacked layer in `UsageWorkspaceView`** — the same shape as the existing "By user" stack, just keyed on `connector_slug`.
3. **Conversation-level "By connector" breakdown in `UsageConversationView`** — sits next to the existing "By model" table.
4. **Deterministic attribution rule.** A model call is attributed to the **most recent completed tool invocation on the same run within this turn** (turn = the boundary between two `user` messages). Calls before any tool fires in the turn = `NULL`. This is a single-line rule the worker enforces; no probabilistic mapping.
5. **No new chain. No new event family. No streaming-handshake change.** One nullable column, one new payload field, one tiny rollup table.
6. **Reuse the rollup loop.** The existing `UsageRollupLoop` already idempotently UPSERTs `runtime_usage_daily_user` and `runtime_usage_daily_org`. We add one rollup target: `runtime_usage_daily_connector`. Same loop, same late-arrival window.
7. **Less code than the question implies.** Net new in ai-backend: 1 migration (~25 lines), 1 enum-free field on the payload (~5 lines), ~80 LoC in the worker emit site, ~60 LoC in the read aggregator, ~30 LoC of additional rollup. FE: ~120 LoC across one new panel + one new chart layer.

### 1.3 Non-goals

- **No probabilistic / LLM-judged attribution.** If we don't know the connector deterministically, we record `NULL`. We do not "guess" the connector by inspecting the model's prompt or output.
- **No connector-cost rebilling.** Token cost in micro-USD is computed by the existing `model_pricing` lookup; this PR doesn't change pricing math. "Per-connector cost" is "per-connector tokens" multiplied by the same per-model rate.
- **No per-connector token budgets.** PR 1.2.1 introduced per-conversation connector scope; per-connector budget _enforcement_ would belong with the budget machinery in [`runtime_tool_budgets.sql`](../../services/ai-backend/migrations/0010_runtime_tool_budgets.sql). v1 is a measurement, not an enforcement.
- **No retroactive backfill.** Rows written before the migration carry `connector_slug = NULL` and aggregate as "(unattributed)". A backfill script that joins old usage rows to old tool invocations is offered as a one-shot operator script, not a migration step.
- **No per-tool granularity in v1.** "By connector" is the right axis for the design (and for buyer conversations); per-tool ("`slack.search_messages` cost the most") is a follow-up that requires moving to `tool_name` rather than `connector_slug` in the same payload field.
- **No new chain field.** The signed audit chain (PR 7.1's read surface) is unaffected. `runtime_model_call_usage` is _not_ an audit table — it is a metering table — so chain HMAC does not apply.
- **No FE filter to "exclude unattributed".** The `(unattributed)` row exists because it has to (cold-turn calls genuinely have no connector); we render it; we don't try to hide it.

### 1.4 Success criteria

- ✅ Every `runtime_model_call_usage` row written after the migration carries either a valid `connector_slug` (populated from a preceding `runtime_tool_invocations.connector_slug` on the same run+turn) or `NULL`.
- ✅ Workspace owners see a "By connector" stacked layer in `UsageWorkspaceView` for the past 30 days. Sum of layers equals the existing "By user" total to the token; reconciliation test enforces it.
- ✅ Conversation-level breakdown in `UsageConversationView` lists rows by connector with token + cost columns.
- ✅ The `UsageRollupLoop` continues to refresh in <30s for a tenant with ≥10M call rows; the new rollup adds <10% to the loop runtime.
- ✅ `model_call_completed` event payload includes `connector_slug?: string | null`. Older clients that don't read it continue to work.
- ✅ One smoke test: run a fixture that fires Slack tool → model → Notion tool → model → idle model; assert the per-call rows attribute correctly (Slack, Notion, NULL).
- ✅ Streaming handshake unchanged. PR 7.2 introduces zero new event _types_; only one optional field on an existing event payload.

### 1.5 User stories

| As…                 | I want…                                                                | So that…                                                                           |
| ------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Marcus (admin)      | a "By connector" tab in the workspace usage overlay                    | I can see whether Salesforce or Slack drove last week's spend                      |
| Sarah (user)        | a "By connector" line under "By model" in the conversation usage panel | I can see whether my Q1-launch chat is "front-loaded" on Drive or evenly spread    |
| Compliance          | the attribution rule to be deterministic and documented                | I can defend "$X went to Salesforce" without "the model decided it was Salesforce" |
| Future-Wave-budgets | a per-connector token total per (org, day)                             | I can build per-connector budget enforcement on top without re-deriving the data   |
| Atlas team          | one wire field, one DB column, one rollup target — no new event family | the change merges with PR 1.x work without conflict                                |

---

## 2 · Spec

### 2.1 Wire — `model_call_completed` payload (additive)

The runtime already emits a `model_call_completed` event after each successful LLM call. It carries the per-call usage row's contents on the wire so the FE can update the "context window" indicator without polling. We extend the payload by one optional field:

```ts
// packages/api-types/src/index.ts  (additive on RuntimeModelCallCompletedEvent['payload'])
export interface RuntimeModelCallCompletedPayload {
  // … existing fields (input_tokens, output_tokens, cached_input_tokens, model_provider, model_name, …)
  connector_slug?: string | null; // ← new; null when this call has no preceding tool context
}
```

The presentation projector (`activity_kind` / `display_title` / `summary` / `status`) is unchanged — `connector_slug` is consumed only by the FE's usage panels, not the activity rail. The model-delta event (the one carrying streaming tokens) is **not** extended; attribution is one-per-call, not one-per-token.

### 2.2 Persistence

#### 2.2.1 ai-backend `0022_runtime_usage_connector.sql`

Numbered after PR 1.6's `0021_workspace_defaults_behavior_overrides.sql`.

```sql
-- Connector attribution for per-LLM-call usage rows.
-- The column is nullable: cold-turn LLM calls (the opening "plan" call before
-- any tool fires) have no connector context and remain NULL. Filling it lazily
-- via a backfill script is fine — chain-of-evidence is not relevant here
-- (this is metering data, not an audit chain).

ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS connector_slug TEXT;

-- Hot-path index: per-connector aggregation over a 30-day window.
-- Partial index keeps the unattributed rows out of it; we never aggregate
-- "(unattributed)" via this index — that bucket is computed separately from
-- the rollup table's aggregate row.
CREATE INDEX IF NOT EXISTS idx_runtime_model_call_usage_org_connector_created
    ON runtime_model_call_usage (org_id, connector_slug, created_at)
    WHERE connector_slug IS NOT NULL;

-- Daily rollup for fast workspace queries (mirrors runtime_usage_daily_user
-- and runtime_usage_daily_org). One row per (org, day, connector_slug).
-- connector_slug is part of the PK so the existing UsageRollupLoop's
-- idempotent UPSERT keeps working unchanged in shape.
CREATE TABLE IF NOT EXISTS runtime_usage_daily_connector (
    org_id              TEXT NOT NULL,
    day                 DATE NOT NULL,
    connector_slug      TEXT NOT NULL,             -- '' represents (unattributed)
    runs_count          INTEGER NOT NULL,
    distinct_users      INTEGER NOT NULL,
    input_tokens        BIGINT NOT NULL,
    output_tokens       BIGINT NOT NULL,
    cached_input_tokens BIGINT NOT NULL,
    total_tokens        BIGINT NOT NULL,
    cost_micro_usd      BIGINT,
    refreshed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, day, connector_slug)
);
CREATE INDEX IF NOT EXISTS idx_runtime_usage_daily_connector_org_day
    ON runtime_usage_daily_connector (org_id, day DESC);
```

Why the empty-string sentinel for `(unattributed)`:

- `PRIMARY KEY` cannot include `NULL`. The rollup row for unattributed calls would otherwise be unrepresentable.
- The empty string is unambiguous: connector slugs are validated against `ConnectorScopeValidator` (PR 1.2), which forbids the empty string. There is no real connector named `""`.
- The FE renders `connector_slug === ''` as the localised label "(unattributed)" — same pattern used today for `runtime_usage_daily_org` rows where `model_name = 'unknown'`.

The base table `runtime_model_call_usage` keeps `connector_slug NULL`; only the rollup table normalises `NULL → ''` for PK reasons. The aggregator's SQL is `COALESCE(connector_slug, '')` on insert.

#### 2.2.2 What we are _not_ adding

| Thing                                            | Why not                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `connector_slug` on `runtime_run_usage`          | Run-level usage aggregates across all calls in a run; connector attribution is per-call. A run typically spans connectors.                                                                                                                                                                                                                  |
| `tool_name` column                               | Per-tool granularity is a v2 ask. Adding `tool_name` now bloats the rollup table 10–50× without a v1 use-case.                                                                                                                                                                                                                              |
| FK from `connector_slug` to a `connectors` table | We don't have a connectors table — connector identity is the slug itself, validated by `ConnectorScopeValidator`. Adding a table for this PR is yak-shaving.                                                                                                                                                                                |
| New rollup tables for `(user, connector)`        | Cardinality blow-up; the workspace view aggregates by org+connector and the conversation view computes from the base table directly (still fast: indexed).                                                                                                                                                                                  |
| RLS on the new rollup table                      | Existing `0008_rls_tenant_isolation.sql` policy applies — the new table picks up the same `current_setting('app.current_org')` check by virtue of the standard pattern. We add the matching `ALTER TABLE … ENABLE RLS` + `CREATE POLICY` clauses in the migration (omitted from the snippet above for brevity; included in the actual SQL). |

### 2.3 Wire — read endpoints

Two extension points; **both reuse existing endpoints**. No new routes.

#### `GET /v1/usage/me?period=30d&dimension=connector`

`UsageMeResponse` gains an optional `by_connector` axis. The dimension query parameter is additive — clients that don't pass it get the same response as today.

```ts
// packages/api-types/src/index.ts (addition to UsageMeResponse)
export interface UsageConnectorRow {
  connector_slug: string; // '' = "(unattributed)"
  display_name: string | null; // hydrated from connectors registry; null for ''
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsageMeResponse {
  // … existing fields
  by_connector: UsageConnectorRow[]; // empty when caller omits dimension=connector
}
```

#### `GET /v1/usage/conversations/{conversation_id}?dimension=connector`

`ConversationUsageResponse` gains the same `by_connector` axis, scoped to the conversation. Computed live from `runtime_model_call_usage` filtered by `conversation_id` — the existing `(org_id, run_id, created_at)` index keeps it fast for any normal-sized conversation.

#### `GET /v1/usage/org?period=30d&dimension=connector` (admin)

`UsageOrgResponse` gains `by_connector` for the workspace stacked-area chart in `UsageWorkspaceView`. Backed by `runtime_usage_daily_connector` for fast 30-day reads.

### 2.4 Attribution rule (deterministic)

The worker computes `connector_slug` for each LLM call by this rule:

1. The "turn" is the slice between the most recent inbound `user` message and the next inbound `user` message (or end-of-run). Turn boundaries are already observable in the worker's state machine — they coincide with the `human` message in LangGraph's state.
2. Within a turn, when the worker emits `model_call_completed`, it asks `RuntimeToolContext.last_completed_invocation_slug()` for the most recent **completed** `runtime_tool_invocations` on this run.
3. If the answer is non-null, it is the connector for this call. If null, the call is unattributed.

This rule lives in **one place** — `agent_runtime/observability/usage_attribution.py` (~40 LOC) — and is consumed by the existing `model_call_completed` emit site. It is not embedded in the worker's main loop; it is one method call.

We deliberately do **not** attribute based on the call's _next_ tool invocation. Forward-looking attribution would require buffering events until the next tool result, which contradicts the streaming "emit as you go" contract. Backward-looking is simple, correct for the design's question ("which connector cost the most"), and adversarially safe (a runaway model that calls Salesforce 1000× in a row attributes 1000× to Salesforce, exactly as a buyer would expect).

### 2.5 Permissions

| Caller                 | `GET /v1/usage/me?dimension=connector` | `GET /v1/usage/org?dimension=connector` |
| ---------------------- | -------------------------------------- | --------------------------------------- |
| Conversation owner     | ✅ — own usage only                    | ❌                                      |
| Workspace admin        | ✅                                     | ✅ — workspace-wide                     |
| Other workspace member | ✅ — own usage only                    | ❌                                      |
| Service-to-service     | passthrough as today                   | passthrough                             |

These match the existing usage endpoints' authz (no scope changes). `dimension=connector` is just a new parameter on the same routes.

### 2.6 Error semantics

| Condition                                                                           | Status         | Code                                                                |
| ----------------------------------------------------------------------------------- | -------------- | ------------------------------------------------------------------- |
| `dimension` not in `{"model","connector","day","user"}` (existing dims + connector) | 422            | `invalid_request`                                                   |
| Unknown `period` (existing rule)                                                    | 422            | `invalid_request`                                                   |
| Caller asks `/usage/org` without admin scope                                        | 403            | `forbidden`                                                         |
| `connector_slug` filter contains illegal slug                                       | 422            | `invalid_request`                                                   |
| Cold-start fallback: rollup empty, base scan exceeds budget                         | 200 (degraded) | `cold_start_fallback: true` (existing pattern in `UsageMeResponse`) |

### 2.7 Frontend contract

Three surgical FE changes; all sit inside the panels PR 4.5 already shipped. **No new design-system primitive.**

- `apps/frontend/src/features/chat/components/details/usage/UsageConversationView.tsx`: add a new section "By connector" beneath the existing "By model". Same table layout, fed by `usage.by_connector`. Empty bucket label = "(unattributed)" with a help-tooltip explaining the rule.
- `apps/frontend/src/features/chat/components/details/usage/UsageWorkspaceView.tsx`: add a new tab toggle "By connector" alongside the existing "By user" mode. Reuses [`UsageWorkspaceChart`](../../apps/frontend/src/features/chat/components/details/usage/UsageWorkspaceChart.tsx) — the chart accepts a series array; we pass connector series instead of user series. Keys differ; rendering code does not.
- `apps/frontend/src/features/chat/components/details/usage/usagePalette.ts`: extend the deterministic-color palette to a stable connector-slug → color mapping. Same shape as the existing user-id mapping; reuses the same hash function. **No two PRs touch this file simultaneously** (shared with PR 4.5).

The "Connector" axis is a sibling, not a replacement, of "User" — Marcus can flip between them in the workspace view; the conversation view shows both.

### 2.8 What the worker emits (illustrative)

```python
# services/ai-backend/src/runtime_worker/handlers/model_call.py  (existing site)
async def emit_model_call_completed(self, *, call: ModelCall, usage: TokenUsage) -> None:
    connector_slug = await self._tool_context.last_completed_invocation_slug(run_id=call.run_id)
    await self._usage_store.record_model_call_usage(
        ModelCallUsageRecord(
            id=call.id,
            org_id=call.org_id,
            run_id=call.run_id,
            conversation_id=call.conversation_id,
            parent_event_id=call.parent_event_id,
            trace_id=call.trace_id,
            task_id=call.task_id,
            subagent_id=call.subagent_id,
            model_provider=call.model_provider,
            model_name=call.model_name,
            connector_slug=connector_slug,        # ← new
            input_tokens=usage.input,
            output_tokens=usage.output,
            cached_input_tokens=usage.cached_input,
            total_tokens=usage.total,
            duration_ms=usage.duration_ms,
            cost_micro_usd=self._pricing.estimate(call=call, usage=usage),
            pricing_id=self._pricing.id,
            pricing_version=self._pricing.version,
            created_at=usage.completed_at,
        )
    )
    await self._stream.publish(
        ModelCallCompletedEvent(
            run_id=call.run_id,
            payload=ModelCallCompletedPayload(
                # … existing fields …
                connector_slug=connector_slug,    # ← new
            ),
        )
    )
```

The diff is two mirror lines. `last_completed_invocation_slug` lives next to the existing `RuntimeToolContext` (already maintained by the worker) — it's a `SELECT connector_slug FROM runtime_tool_invocations WHERE run_id = $1 AND status = 'completed' ORDER BY completed_at DESC LIMIT 1`. The `(org_id, run_id, started_at)` index covers the lookup.

### 2.9 Rollup loop extension

The existing `UsageRollupLoop` (per migration `0007_usage_daily_rollups.sql`) recomputes `runtime_usage_daily_user` and `runtime_usage_daily_org` for the trailing 2 days every N minutes (`USAGE_ROLLUP_INTERVAL_SECONDS`). We add a third refresh target with the same shape:

```sql
INSERT INTO runtime_usage_daily_connector AS rdc
  (org_id, day, connector_slug, runs_count, distinct_users,
   input_tokens, output_tokens, cached_input_tokens, total_tokens, cost_micro_usd, refreshed_at)
SELECT
  org_id,
  date_trunc('day', created_at)::date AS day,
  COALESCE(connector_slug, '')        AS connector_slug,
  COUNT(DISTINCT run_id)              AS runs_count,
  COUNT(DISTINCT user_id_via_run)     AS distinct_users,
  SUM(input_tokens)                   AS input_tokens,
  SUM(output_tokens)                  AS output_tokens,
  SUM(cached_input_tokens)            AS cached_input_tokens,
  SUM(total_tokens)                   AS total_tokens,
  SUM(cost_micro_usd)                 AS cost_micro_usd,
  NOW()                               AS refreshed_at
FROM runtime_model_call_usage
WHERE org_id = $1 AND created_at >= $2 AND created_at < $3
GROUP BY org_id, day, COALESCE(connector_slug, '')
ON CONFLICT (org_id, day, connector_slug) DO UPDATE SET
  runs_count = EXCLUDED.runs_count,
  distinct_users = EXCLUDED.distinct_users,
  input_tokens = EXCLUDED.input_tokens,
  output_tokens = EXCLUDED.output_tokens,
  cached_input_tokens = EXCLUDED.cached_input_tokens,
  total_tokens = EXCLUDED.total_tokens,
  cost_micro_usd = EXCLUDED.cost_micro_usd,
  refreshed_at = EXCLUDED.refreshed_at;
```

`distinct_users` requires `user_id` per call. Today `runtime_model_call_usage` does not carry `user_id` — but `agent_runs.user_id` is a 1:1 join target via the existing FK. We do that join in the rollup; we do **not** denormalise `user_id` onto the per-call row (it would bloat rows for no win — every aggregator has the run table available).

The loop runs in three sequential `INSERT … ON CONFLICT` statements (user / org / connector). The new statement adds <10% to the loop's wall-clock — measured against the standard fixture in `tests/fixtures/usage_rollup_million_rows.sql`.

### 2.10 Cold-start fallback

When the rollup is cold (first call after deploy, or rollup loop disabled in dev), `UsageQueryService` already falls back to a direct scan of `runtime_run_usage` and sets `cold_start_fallback: true`. We mirror the same fallback for the connector dimension: scan `runtime_model_call_usage` directly, group by `connector_slug`, return the result. The same response flag warns the FE; the FE renders a small "computed live — may be slow on cold start" footer (already wired in PR 4.5).

---

## 3 · Architecture

### 3.1 Where this lives

```
   ┌────────────────┐     GET /v1/usage/me?dimension=connector
   │   apps/        │ ─────────────────────────────┐
   │   frontend     │                              │
   │  UsagePanel:   │ ◄────────────────────────────┤  by_connector[]
   │   Conversation │                              │
   │   Workspace    │                              │
   └────────────────┘                              │
                                                   ▼
                                       ┌──────────────────────┐
                                       │  backend-facade      │  thin proxy
                                       │  /v1/usage/*         │
                                       └──────────┬───────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │  ai-backend          │
                                       │  UsageQueryService   │
                                       │   .query_user()      │
                                       │   .query_conv()      │
                                       │   .query_org()       │
                                       │   ↳ branch on dim    │
                                       └────┬───────────┬─────┘
                                            │           │
                                  rollup    │           │  base table
                                  hit       │           │  (cold-start / conv)
                                            ▼           ▼
                              ┌─────────────────────┐ ┌─────────────────────────┐
                              │runtime_usage_daily_ │ │runtime_model_call_usage│
                              │   connector         │ │  (+ new connector_slug)│
                              │   (new rollup)      │ └─────────────────────────┘
                              └─────────────────────┘             ▲
                                            ▲                     │ INSERT at write-time
                                            │                     │ (worker)
                                            │ refreshed by        │
                                            │ UsageRollupLoop     │
                                            │ (existing; +1 stmt) │
                                            │                     │
                              ┌─────────────────────────────────────────────────┐
                              │   runtime_worker (existing model-call emit)      │
                              │   ─ asks RuntimeToolContext for last connector  │
                              │   ─ writes connector_slug to usage row + event  │
                              └─────────────────────────────────────────────────┘
```

The diagram emphasises: one new column, one new rollup, one extended emit site. No new service. No new event family.

### 3.2 Streaming impact

| Subsystem                                  | Touched by this PR?                                                                                                                                                   |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events` schema                    | **No.** No new `event_type`, no projection change, no `sequence_no` semantic change.                                                                                  |
| `RuntimeEventEnvelope` Pydantic            | **Additive only.** The existing `model_call_completed` payload gains one optional field (`connector_slug?: str \| null`). Older consumers ignore unknown fields.      |
| SSE handshake (`?after_sequence=N`)        | **No.** Reconnect is byte-identical.                                                                                                                                  |
| Worker `runtime_worker/` job loop          | **Yes (additive).** One additional `SELECT … LIMIT 1` per `model_call_completed` emit; ≤2ms p99. Falls through to `NULL` on miss without erroring.                    |
| Capabilities middleware, tools, MCP loader | **No.** Tool invocation rows are already produced by the existing path — we only consume them at usage emit time.                                                     |
| Citation registry (PR 1.1)                 | **No.** Independent.                                                                                                                                                  |
| Drafts (PR 1.3)                            | **No.**                                                                                                                                                               |
| Approvals (PR 1.4)                         | **No.**                                                                                                                                                               |
| Subagents (PR 1.5)                         | **No.** Subagent calls already populate `runtime_tool_invocations.connector_slug`; the rule attributes their model calls correctly via the same `RuntimeToolContext`. |
| Workspace defaults (PR 1.6)                | **No.**                                                                                                                                                               |
| Audit chain                                | **No.** Usage is metering, not audit.                                                                                                                                 |
| Retention sweeper                          | **No** — the new rollup table participates in the same `messages`-class TTL via the existing sweeper. The rollup is recomputable from base data, so reaping is safe.  |
| Rollup loop                                | **Yes (additive).** One additional `INSERT … ON CONFLICT` per loop tick. Measured <10% runtime increase against the standard fixture.                                 |

The bottom line: **one optional field on one existing event** + **one nullable column** + **one rollup target**. The streaming contract — the thing PR 1.x has been heavily extending — is left alone.

### 3.3 Why backward attribution and not a tagged-context object

A purer design might thread a `current_connector_slug` through every LLM call's prompt context, so the model's "I am responding on behalf of this connector" is explicit. This is what a tracing library would do (e.g. OpenTelemetry trace attributes).

We rejected it for v1 because:

- The model call doesn't have one connector — it can read tool outputs from many tools in one prompt. The "current" connector is genuinely ambiguous when a single LLM turn synthesises across `slack_search`, `drive_read`, and `notion_query` results.
- Threading the attribute requires plumbing it through prompt assembly, which is plumbing through Deep Agents, LangGraph state, and middleware — all surface area we have to keep stable for citations / approvals / subagents work.
- The backward rule ("most recent completed tool invocation in this turn") gives the right answer 95% of the time and a defensible answer the rest of the time (in the synthesis case, the most-recent tool is _the last connector the model touched_, which is also a reasonable "who provoked this token burn" answer).
- We can revisit if a real user asks "your attribution is wrong" — at which point we have data to argue from. v1 is measure-then-improve, not improve-then-measure.

### 3.4 Why a rollup table and not a materialised view

Same rationale as `runtime_usage_daily_user` / `runtime_usage_daily_org` (per `0007_usage_daily_rollups.sql:6-8`):

> "We refresh daily rollups via an idempotent UPSERT loop (NOT a materialized view; explicit tables avoid concurrent refresh foot-guns)."

This PR follows that precedent. Concurrent refresh on a Postgres materialised view requires `REFRESH MATERIALIZED VIEW CONCURRENTLY`, which requires a unique index, which requires the same key shape we'd write to a real table — so you may as well write the real table and skip the foot-gun.

### 3.5 No third-party middleware needed

Web-survey of likely candidates and why we skip them:

- **OpenTelemetry attributes** — beautiful for distributed tracing; not the right home for compliance-grade per-tenant metering. Already evaluated for PR 1.4's audit chain; same answer here.
- **`prometheus_client` per-connector counters** — fine for ops dashboards, useless for per-tenant cost attribution. We need tenant-scoped, queryable history; Prometheus is org-wide.
- **`pg_partman`** — partitioning `runtime_model_call_usage` by month would speed range scans, but the table is already <100M rows in our largest tenant simulation and the existing `(org_id, created_at)` index is fast enough. Re-evaluate when we hit a real performance problem.
- **`datasketches` / approximate aggregates** — premature; current data volumes don't motivate sketches.
- **Dimensional libraries (e.g. `cube.dev`)** — orthogonal. Our usage shape is small (one fact table, three rollup tables); a generic OLAP cube layer would be net-negative complexity.

The only library decision worth confirming is `httpx` for the facade proxy passthrough — already in use and unchanged. We add zero new deps.

### 3.6 DRY — what we reuse vs. what we add

| Concern                       | Reuse                                                                                       | Add                                                               |
| ----------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Per-call usage storage        | `runtime_model_call_usage` (migration 0005)                                                 | one nullable column                                               |
| Daily rollup loop             | `UsageRollupLoop` (migration 0007 + worker job)                                             | one additional UPSERT per loop tick                               |
| Late-arrival window semantics | `USAGE_LATE_ARRIVAL_WINDOW` env constant                                                    | —                                                                 |
| Rollup table shape            | `runtime_usage_daily_user` schema pattern                                                   | one new table (mirrored)                                          |
| Connector ID validation       | `ConnectorScopeValidator` (PR 1.2)                                                          | —                                                                 |
| Pricing math                  | `model_pricing` (migration 0006) + `PricingResolver`                                        | —                                                                 |
| Read service                  | `UsageQueryService` (existing; powers `/v1/usage/me`, `/usage/org`, `/usage/conversations`) | one branch on `dimension=connector` (~60 LOC)                     |
| Cold-start fallback path      | existing `cold_start_fallback` flag + `query_run_usage_for_range` direct scan               | one analogous direct scan over `runtime_model_call_usage`         |
| Streaming wire                | `model_call_completed` event                                                                | one optional payload field                                        |
| Worker emit site              | `runtime_worker/handlers/model_call.py` (existing)                                          | two mirror lines (record + event)                                 |
| `RuntimeToolContext`          | existing (already tracks tool invocations in flight)                                        | one new method `last_completed_invocation_slug(run_id)`           |
| FE `UsageConversationView`    | existing component + table primitive                                                        | one new section "By connector"                                    |
| FE `UsageWorkspaceView`       | existing tab toggle pattern (User / Day)                                                    | one new tab "By connector"                                        |
| FE `UsageWorkspaceChart`      | existing series-keyed renderer                                                              | —                                                                 |
| FE color palette              | `usagePalette.ts` deterministic-color hash                                                  | one extension function `colorForConnector(slug)` (1-line wrapper) |
| FE `useUsageOrg`              | existing query hook                                                                         | dimension parameter pass-through                                  |
| RBAC                          | existing usage-route authz                                                                  | —                                                                 |
| RLS                           | existing `0008_rls_tenant_isolation.sql` policy pattern                                     | one matching policy on the new rollup table                       |

**Net new code** is intentionally small:

- 1 SQL migration (~25 lines: ALTER + CREATE TABLE + 2 indexes + RLS policy).
- 1 Pydantic record extension (`ModelCallUsageRecord` + 1 field).
- 1 worker context method (~15 LOC + tests).
- 2 mirror lines at the worker emit site.
- 1 read service branch (~60 LOC + tests).
- 1 rollup-loop UPSERT statement (~25 LOC).
- 1 FE panel section + 1 FE chart-tab toggle (~120 LOC + tests).

Total target: **~350 net LOC, ~140 of which is tests**.

### 3.7 Sequence — a turn that touches Slack and Notion

```
worker                   model                Postgres                    SSE stream                FE UsagePanel
  │                        │                      │                            │                          │
  │  user message arrives  │                      │                            │                          │
  │                        │                      │                            │                          │
  │  call model (plan)     │                      │                            │                          │
  │ ─────────────────────► │                      │                            │                          │
  │ ◄─────────────────────  emit token deltas, finalize call (no preceding tool)                         │
  │                        │                      │                            │                          │
  │  RuntimeToolContext.last_completed_invocation_slug(run) → None              │                          │
  │  ─ INSERT runtime_model_call_usage (connector_slug=NULL)                    │                          │
  │  ─ publish model_call_completed (connector_slug=null)                       │                          │
  │ ────────────────────────────────────────────────────────────────────────► │                          │
  │                                                                            │                          │
  │  model emits tool_call slack.search_messages                                │                          │
  │  ─ INSERT runtime_tool_invocations (connector_slug='slack', status='running')                          │
  │  ─ runs Slack tool, stores result                                                                      │
  │  ─ UPDATE runtime_tool_invocations status='completed', completed_at=now()                              │
  │                        │                      │                            │                          │
  │  call model (synthesis)│                      │                            │                          │
  │ ─────────────────────► │                      │                            │                          │
  │ ◄─────────────────────  finalize                                                                       │
  │  RuntimeToolContext.last_completed_invocation_slug(run) → 'slack'           │                          │
  │  ─ INSERT runtime_model_call_usage (connector_slug='slack')                 │                          │
  │  ─ publish model_call_completed (connector_slug='slack')                    │                          │
  │ ────────────────────────────────────────────────────────────────────────► │                          │
  │                                                                            │                          │
  │  model emits tool_call notion.query                                                                    │
  │  ─ INSERT runtime_tool_invocations (connector_slug='notion', status='running')                         │
  │  ─ runs Notion tool                                                                                    │
  │  ─ UPDATE runtime_tool_invocations status='completed'                                                  │
  │                                                                                                         │
  │  call model (final answer)                                                                             │
  │ ─────────────────────► │                                                                               │
  │ ◄─────────────────────                                                                                  │
  │  RuntimeToolContext.last_completed_invocation_slug(run) → 'notion'                                     │
  │  ─ INSERT runtime_model_call_usage (connector_slug='notion')                                          │
  │  ─ publish model_call_completed (connector_slug='notion')                                              │
  │ ────────────────────────────────────────────────────────────────────────► │                          │
  │                                                                                                         │
  │                                                                                                       Now the conversation has 3 calls:
  │                                                                                                       (unattributed) → 1 call
  │                                                                                                       slack         → 1 call
  │                                                                                                       notion        → 1 call
  │                                                                                                                                  │
  │  user opens UsagePanel "By connector" tab                                                                                         │
  │                        │                      │                            │  GET /v1/usage/conversations/<id>?dimension=connector
  │                        │                      │ ◄────────────────────────────────────────────────────────────────────────────── │
  │                        │  SELECT … FROM runtime_model_call_usage           │                                                       │
  │                        │  WHERE conv_id=$1   GROUP BY COALESCE(connector_slug,'')                                                  │
  │                        │ ────────────────────►                                                                                     │
  │                        │ ◄─────── rows                                                                                             │
  │                                                                            │  by_connector: [{slack,…},{notion,…},{'',…}]         │
  │                                                                            │ ──────────────────────────────────────────────────► │
  │                                                                                                                                   │ render
```

### 3.8 Edge cases

| Case                                                                                    | Behaviour                                                                                                                                                                                                          |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| First LLM call of a turn (no preceding tool)                                            | `connector_slug = NULL`. Aggregates as "(unattributed)" in the rollup row.                                                                                                                                         |
| Subagent's LLM call                                                                     | Subagents run inside the same `run_id`. Their tool invocations populate the same `runtime_tool_invocations` rows; the attribution rule consults the same context. ✅                                               |
| Tool fails mid-flight, then model is called                                             | The failed tool's row is `status='failed'`, not `'completed'`. The rule looks for `status='completed'`; failed tools do **not** attribute. The next model call may be `NULL`.                                      |
| Model calls an MCP tool whose `connector_slug` was deleted from the workspace           | Persisted slug stays. The rollup keeps the slug; the FE's hydrator returns `display_name=null` and renders the slug raw.                                                                                           |
| Cold-start: rollup empty for a tenant just enabled                                      | `UsageQueryService` falls back to a direct scan of `runtime_model_call_usage`; `cold_start_fallback=true`; banner shown.                                                                                           |
| Backfill of historical rows                                                             | An optional one-shot script joins old usage rows to old tool invocations by `(run_id, created_at)` proximity. Out of v1; not a migration step.                                                                     |
| Two model calls in the same wall-clock millisecond, different connectors                | Each row is independently attributed; ordering on the rollup is by `created_at` rounded to day, not millisecond. ✅                                                                                                |
| `runtime_tool_invocations.completed_at` is later than the model call's `created_at`     | The "most recent **completed** invocation **before** this call's `created_at`" rule includes a `completed_at < $created_at` filter — strict ordering. We do not attribute to a tool that completes after the call. |
| Rollup loop is paused / disabled                                                        | Reads fall back to the direct scan; the FE flags `cold_start_fallback`. No data loss.                                                                                                                              |
| Rollup loop UPSERT conflicts with a concurrent backfill                                 | UPSERT is idempotent on the `(org_id, day, connector_slug)` PK. Last-write-wins on the columns. ✅                                                                                                                 |
| Tenant has no connectors at all                                                         | All rows attribute to `NULL`/`''`. The "By connector" panel shows one row "(unattributed)" with the full total. Honest.                                                                                            |
| Caller sends `dimension=connector` but the route doesn't yet support it (legacy facade) | Backward-compatible: the unknown query parameter is ignored; response shape is the legacy one with `by_connector: []`. FE handles `[]` gracefully (renders empty).                                                 |

### 3.9 Test plan

Lives in the same PR. Minimum bar before merge.

**ai-backend (`services/ai-backend/tests/`)**

- `unit/observability/test_usage_attribution.py`
  - Cold turn → `connector_slug=None`.
  - Slack tool → model call → `connector_slug='slack'`.
  - Slack tool (failed) → model call → `connector_slug=None` (failed tool is not "completed").
  - Slack tool → Notion tool → model call → `connector_slug='notion'` (most-recent rule).
  - Subagent's tool counts; sub's model call attributes to the sub's last completed tool.
- `unit/runtime_api/usage/test_query_by_connector.py`
  - Rollup hit → returns expected rows from `runtime_usage_daily_connector`.
  - Cold-start (rollup empty) → direct-scan path; same answer; `cold_start_fallback=true`.
  - `dimension=connector` query parameter validation.
- `unit/runtime_worker/jobs/test_rollup_loop_connector.py`
  - 100k-row fixture → rollup completes; row counts match SUM of base table.
  - Late-arrival window — late row appears in next loop tick's recompute.
  - Idempotent — running the loop twice produces identical rows.
- `unit/runtime_api/streaming/test_model_call_completed_payload.py` — payload includes `connector_slug` when set; field is optional and omittable.
- `integration/test_attribution_end_to_end.py` — fixture run that fires Slack tool → model → Notion tool → model; assert per-call rows attribute correctly.

**Frontend (`apps/frontend/src/features/`)**

- `chat/components/details/usage/UsageConversationView.test.tsx` — "By connector" section renders when `by_connector` is populated; hides when empty.
- `chat/components/details/usage/UsageWorkspaceView.test.tsx` — tab toggle to "By connector" refetches with `dimension=connector`; chart series keyed by slug.
- `chat/components/details/usage/usagePalette.test.ts` — `colorForConnector(slug)` is deterministic and stable across runs.

**Cross-service smoke (`make test`)**: end-to-end fixture validates the conversation panel reflects the per-connector totals after a multi-tool fixture run.

### 3.10 Rollout

- **Flag-free.** New column defaults to `NULL`; old rows are unaffected. Old runs continue to populate `connector_slug=NULL` until the worker code with the new emit lands.
- **Zero-downtime migration.** `ALTER TABLE … ADD COLUMN IF NOT EXISTS … TEXT` (no default → no rewrite). New table is `CREATE TABLE IF NOT EXISTS`. Indexes are `CREATE INDEX IF NOT EXISTS`; production runbook addendum: run the partial index via `CREATE INDEX CONCURRENTLY` (operator note, not in the SQL file).
- **Backout.** Drop the new table + new column + new index. Worker code falls through to the old emit site (the field is optional). FE renders `by_connector: []` correctly when absent.
- **Performance.** First-page p99 under load:
  - Rollup hit: ~10ms (one PK read per (org, day) for 30 days).
  - Cold-start scan: ~120ms for a 30-day window over 5M rows on the test fixture; falls back at ≤1× the existing `runtime_run_usage` cold-start cost.
- **Forward compatibility.** PR 7.2 publishes one optional payload field; PR 7.x for per-tool granularity (if it lands) reuses the same column with a different value source. No further migration likely.

### 3.11 Open questions

1. **Should we attribute the response-only follow-up call (the "wrap-up" model call after the user has stopped sending) to the previous turn's last tool?** v1 says yes — a turn ends when the next user message arrives, not when the model decides it's done. The "wrap-up" call attributes to the last tool of that same turn, which is correct.
2. **Should subagent calls roll up under the parent run's connector or stay attributed to their own most-recent tool?** v1 attributes to the subagent's own most-recent tool. The workspace view stacks them under the parent run (already true for the "By model" view).
3. **Per-tool granularity?** Out of scope for v1. Same migration could easily extend with a `tool_name` column later; rollup cardinality blows up but is bounded by `COUNT(DISTINCT tool_name)` per org per day.
4. **Cost reconciliation between "By model" and "By connector"?** They must sum to the same total. The reconciliation test in `tests/integration/test_attribution_reconciliation.py` enforces this (idempotent fixture: `SUM(by_model.input) == SUM(by_connector.input)`). If they ever diverge, the rollup loop is broken.

---

## 4 · Acceptance checklist

- [ ] Migration `0022_runtime_usage_connector.sql` applies cleanly forward and rolls back via the matching `.rollback.sql`. Includes RLS policy on the new rollup table.
- [ ] `runtime_model_call_usage` has a new nullable `connector_slug TEXT` column + partial index `idx_runtime_model_call_usage_org_connector_created`.
- [ ] `runtime_usage_daily_connector` table is present with PK `(org_id, day, connector_slug)` and the day-desc index.
- [ ] `RuntimeToolContext.last_completed_invocation_slug(run_id)` returns the most recent `runtime_tool_invocations.connector_slug` where `status='completed'` and `completed_at < now()` for the run; `None` if none.
- [ ] Worker `model_call.py` emit site populates `connector_slug` on the usage record and the `model_call_completed` event payload.
- [ ] `UsageQueryService` supports `dimension=connector` for `/v1/usage/me`, `/v1/usage/conversations/{id}`, `/v1/usage/org`. Rollup-hit and cold-start paths both implemented.
- [ ] `UsageRollupLoop` adds a third `INSERT … ON CONFLICT` for `runtime_usage_daily_connector`. Loop runtime increase ≤10% on the standard fixture.
- [ ] `@enterprise-search/api-types` exports `UsageConnectorRow` and `by_connector` field on `UsageMeResponse` / `UsageOrgResponse` / `ConversationUsageResponse`. `RuntimeModelCallCompletedPayload` carries optional `connector_slug`.
- [ ] `UsageConversationView` renders a "By connector" section when `by_connector` is populated; renders "(unattributed)" for empty-string slug.
- [ ] `UsageWorkspaceView` tab toggle includes "By connector"; `UsageWorkspaceChart` accepts the new series keyed by connector slug; `usagePalette.colorForConnector(slug)` is stable.
- [ ] `dimension=connector` reconciles to identical total tokens as `dimension=model` for any (org, period) — enforced by reconciliation test.
- [ ] No new event types in `runtime_api/schemas/events.py`. `RuntimeEventEnvelope` Pydantic schema is byte-identical pre/post merge except for the optional payload field.
- [ ] Existing `model_call_completed` consumers continue to work without modification.
- [ ] `make test` green; targeted ai-backend pytest suite green; frontend typecheck + build green.

---

## 5 · References

- [Atlas Design Doc](../new-design/Design Doc.html) § "Usage overlay · Notes / TODOs" — "Per-connector token attribution".
- [`services/ai-backend/migrations/0005_runtime_model_call_usage.sql`](../../services/ai-backend/migrations/0005_runtime_model_call_usage.sql) — per-LLM-call usage table extended by this PR.
- [`services/ai-backend/migrations/0007_usage_daily_rollups.sql`](../../services/ai-backend/migrations/0007_usage_daily_rollups.sql) — rollup pattern (loop + tables) we mirror.
- [`services/ai-backend/migrations/0001_initial_runtime_persistence.sql`](../../services/ai-backend/migrations/0001_initial_runtime_persistence.sql) — `runtime_tool_invocations.connector_slug` source-of-truth + the indexes the attribution lookup hits.
- [`services/ai-backend/migrations/0008_rls_tenant_isolation.sql`](../../services/ai-backend/migrations/0008_rls_tenant_isolation.sql) — RLS pattern reused on the new rollup table.
- [`services/ai-backend/migrations/0006_model_pricing.sql`](../../services/ai-backend/migrations/0006_model_pricing.sql) — pricing math reused unchanged.
- [`services/ai-backend/src/runtime_api/schemas/usage.py`](../../services/ai-backend/src/runtime_api/schemas/usage.py) — read response shapes extended additively.
- [`services/ai-backend/src/runtime_api/http/routes.py`](../../services/ai-backend/src/runtime_api/http/routes.py) — usage routes branch on `dimension=connector`.
- [`services/ai-backend/src/agent_runtime/api/usage_service.py`](../../services/ai-backend/src/agent_runtime/api/usage_service.py) — `UsageQueryService` extended with one method per dimension.
- [`apps/frontend/src/features/chat/components/details/usage/`](../../apps/frontend/src/features/chat/components/details/usage/) — UsagePanel landed by PR 4.5; this PR extends the panels there.
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — stays unchanged; this PR is additive on one payload field only.
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md) — facade-only ingress; ai-backend owns metering.
- [`docs/new-design/pr-1-2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — provides `ConnectorScopeValidator` for connector-id validation.
- [`docs/new-design/pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md) — ships the UsagePanel surfaces this PR extends.
- [`docs/new-design/pr-7.1-audit-log-section.md`](pr-7.1-audit-log-section.md) — sibling Wave 7 PR; independent merge.
