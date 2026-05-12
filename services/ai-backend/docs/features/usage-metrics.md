# Usage Metrics

How token usage is recorded during runs, rolled up into daily aggregates, and
surfaced via the `/context` slash command and the Usage page.

See also:

- [features/budgets.md](budgets.md) — cost charging from usage records
- [features/thinking-reasoning.md](thinking-reasoning.md) — reasoning token tracking
- [diagrams/flows/f9-usage-metrics.puml](../architecture/diagrams/flows/f9-usage-metrics.puml)

---

## What it does

Every model call during a run produces token counts (input, output, cached input,
reasoning). These are persisted as `RuntimeModelCallUsageRecord` rows. After a run,
a daily rollup loop aggregates per-call rows into `RuntimeRunUsageRecord` summaries.
Two query surfaces expose this data:

1. **`/context`** — per-conversation token headroom for the active conversation.
2. **`/v1/usage/*`** — per-user, per-org, per-connector rolled-up spend for the Usage page.

---

## Key modules

| File                                            | Role                                                                                         |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `agent_runtime/observability/usage_recorder.py` | `UsageRecorder`, `PostgresUsageRecorder` — writes per-call rows                              |
| `agent_runtime/observability/token_usage.py`    | `TokenUsageRecord` — normalised token shape across providers                                 |
| `agent_runtime/observability/attribution.py`    | `UsageAttributionContext`, `Purpose` — attribute usage to connector/skill                    |
| `runtime_worker/run_metrics.py`                 | `AssistantRunMetrics` — accumulates per-call counts across a run                             |
| `runtime_worker/usage_rollup_loop.py`           | Background loop: aggregate per-call rows → daily run rollup                                  |
| `agent_runtime/api/usage_service.py`            | `UsageQueryService`, `ConversationContextBuilder` — query + projection                       |
| `runtime_api/http/routes.py`                    | `/v1/agent/conversations/{id}/context` endpoint                                              |
| `runtime_api/schemas/usage.py`                  | `RuntimeRunUsageRecord`, `RuntimeModelCallUsageRecord`, `UsageResponse`, `ConnectorUsageRow` |

---

## Per-call usage recording

`agent_runtime/observability/usage_recorder.py`

On each `MODEL_CALL_COMPLETED` event (end of a model API call):

1. The stream adapter extracts `usage_metadata` from the final chunk
   (fields: `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`).
2. `AssistantRunMetrics.record_call(usage)` accumulates the counts.
3. `PostgresUsageRecorder.record(run_id, call_id, usage, attribution)` writes a
   `RuntimeModelCallUsageRecord` row.

**Attribution** (`UsageAttributionContext`):

- `connector_slug` — which MCP connector was active when the call was made.
- `purpose` — `MAIN` (user turn), `POLISH` (async polish), `SUBAGENT`, `SUMMARIZE`.

---

## Normalised token shape

`agent_runtime/observability/token_usage.py` — `TokenUsageRecord`:

| Field                 | Type  | Notes                                               |
| --------------------- | ----- | --------------------------------------------------- |
| `input_tokens`        | `int` | Prompt tokens sent to the provider                  |
| `output_tokens`       | `int` | Completion tokens returned                          |
| `cached_input_tokens` | `int` | Tokens served from provider cache (cheaper)         |
| `reasoning_tokens`    | `int` | Reasoning-only tokens (Anthropic / OpenAI thinking) |

All four fields are present on every record; unused fields are 0.

---

## Daily rollup

`runtime_worker/usage_rollup_loop.py`

Background loop runs every `RUNTIME_USAGE_ROLLUP_INTERVAL_SECONDS`:

1. Finds `RuntimeModelCallUsageRecord` rows not yet aggregated.
2. Groups by `(org_id, user_id, date, connector_slug, purpose)`.
3. Sums token counts and micro-USD cost (from `CostCalculator`).
4. Upserts into `RuntimeRunUsageRecord` (daily bucket rows).
5. Marks per-call rows as aggregated.

The rollup is idempotent: re-running it on already-aggregated rows is a no-op.

---

## `/context` endpoint

`GET /v1/agent/conversations/{id}/context`

`ConversationContextBuilder.build(telemetry, model_config)`:

1. Fetches the latest run's usage rows, compression events, and active `ModelPricingRecord`.
2. Sums `input_tokens + output_tokens` across all model calls in the conversation.
3. Computes `headroom_pct = (max_input_tokens - used_tokens) / max_input_tokens * 100`.
4. Returns `ConversationContextResponse`:
   - `context_window` — model's max_input_tokens
   - `used_tokens`
   - `available_tokens` / `headroom_pct`
   - `compression_events` — list of past summarisation events
   - `per_subagent_breakdown` — per-task token counts (collapsed to supervisor for display)

The builder is stateless and pure. It never calls the LLM.

---

## Usage page endpoints

| Path                                     | Description                                          |
| ---------------------------------------- | ---------------------------------------------------- |
| `GET /v1/usage/me?period=...`            | Per-user daily usage (token counts + micro-USD cost) |
| `GET /v1/usage/me/connectors?period=...` | Per-user usage broken down by MCP connector          |
| `GET /v1/usage/org?period=...`           | Org-wide usage (admin only; checked via RBAC)        |

**Period format:** ISO week (`2026-W18`) or date range (`2026-05-01,2026-05-07`).

`UsageQueryService`:

1. Parses the period.
2. Queries `RuntimeRunUsageRecord` rows for the window.
3. Aggregates into `_RollupBucket` objects per day.
4. Returns `UsageResponse` with daily series and totals.

Pricing changes do **not** retroactively rewrite history: cost is computed using the
`ModelPricingRecord` active at write time and stored on the usage row.

---

## Cost in micro-USD

All monetary values are stored as integer micro-USD (1 USD = 1,000,000 µUSD).
`CostCalculator` uses banker's rounding (Python `Decimal.ROUND_HALF_EVEN`) to
minimise accumulated rounding error across many calls.
