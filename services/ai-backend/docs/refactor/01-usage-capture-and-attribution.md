# Refactor PRD — Usage Capture & Attribution (Phase 3 / P11.7, pre-P12)

**Status:** Draft (2026-05-11)
**Author:** architecture audit, May 2026
**Tracks:** capture-side prerequisite for [P12 pricing-from-LiteLLM](01-pricing-from-litellm.md); follow-on to [P11 redaction subsystem](01-redaction-subsystem.md); coordinates with [P13 OTel coverage hardening](01-otel-adoption.md)

> **Why this PRD exists.** P12 swaps the pricing _source_ (in-house seed catalog → LiteLLM rows). The pricing source is a multiplier; the captured token rows are the multiplicand. An audit on 2026-05-11 found the multiplicand is the weaker side: token kinds are under-extracted, subagent identity is hardcoded `None`, parallel subagents mis-attribute, context-compression LLM calls drop on the floor, the connector resolver is a time-based heuristic with a known wrong-edge, and the rollup tables drop dimensions the raw rows do carry. Shipping a better multiplier on top of a lossy multiplicand makes pricing _less_ accurate for reasoning and cached-prompt workloads, not more. This PRD lands the capture-side hardening **before** P12.

---

## 1. Problem

### 1.1 Concrete defects in the current capture path

Verified in code on 2026-05-11. Each row links to file + line.

| #   | Defect                                                                                                                                                                                                                                                                                                                                                                      | Evidence                                                                                                                                                          | Impact                                                                                                  |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| D1  | **Token kinds extracted is a least-common-denominator subset.** Only `input / output / total / cached_input` survive. Missing: `reasoning_tokens` (OpenAI o-series, Anthropic extended thinking), `cache_creation_input_tokens` (Anthropic prompt-cache writes — often >50% of cached-request cost), `audio_input_tokens` / `audio_output_tokens` (OpenAI Responses voice). | [`run_metrics.py:282-294`](../../src/runtime_worker/run_metrics.py)                                                                                               | P12 prices these distinctly. Without extraction, we multiply zero. **Hard blocker for accurate P12.**   |
| D2  | **`subagent_id` is hardcoded `None`.** The column exists on `RuntimeModelCallUsageRecord` and on the api-types contract; the writer never sets it.                                                                                                                                                                                                                          | [`run_metrics.py:373`](../../src/runtime_worker/run_metrics.py); shaped-for-future in [`api-types/src/index.ts:958`](../../../../packages/api-types/src/index.ts) | Cross-run "what did `researcher` cost last week" requires task-registry joins; not surfaced on the row. |
| D3  | **Parallel subagents mis-attribute every LLM token.** When two subagent tasks are active on the same run, `next(iter(active_subagent_tasks))` picks one task arbitrarily and stamps every chunk with that task_id.                                                                                                                                                          | [`streaming_executor.py:151-152`](../../src/runtime_worker/streaming_executor.py)                                                                                 | Per-task rollups are silently wrong under parallelism.                                                  |
| D4  | **Context-compression LLM calls are never persisted to a usage row.** `summarization.py` invokes an LLM; the usage never reaches the recorder.                                                                                                                                                                                                                              | [`context/memory/summarization.py:55-100`](../../src/agent_runtime/context/memory/summarization.py)                                                               | Silent token leak. Org rollups understate cost.                                                         |
| D5  | **Connector resolution is a time-based heuristic via post-hoc DB read.** `UsageAttributionResolver.resolve` issues a SQL query at every model-call-completed emit, looking for the most recent `runtime_tool_invocations` row with `completed_at < emit_time`.                                                                                                              | [`observability/usage_attribution.py:31`](../../src/agent_runtime/observability/usage_attribution.py)                                                             | (a) wrong when an LLM call interleaves a mid-flight tool; (b) extra DB read per LLM call.               |
| D6  | **No `tool_name` on usage rows.** Per-tool attribution is structurally impossible — `jira_search` and `drive_search` both collapse to `"atlassian"` / `"google"`.                                                                                                                                                                                                           | [`persistence/records/telemetry.py:50-83`](../../src/agent_runtime/persistence/records/telemetry.py)                                                              | "How much did jira_search cost last quarter" is unanswerable.                                           |
| D7  | **Connector rollup drops the model dimension.** `UsageDailyConnectorRow` is keyed `(org, day, connector_slug)` only; the `model_name` column is absent.                                                                                                                                                                                                                     | [`usage_rollup_loop.py`](../../src/runtime_worker/usage_rollup_loop.py); [`api-types/src/index.ts:918-926`](../../../../packages/api-types/src/index.ts)          | "GPT-5 cost for jira" requires raw `runtime_model_call_usage` scans; rollup is unusable for that cut.   |
| D8  | **No subagent rollup table.** Run-level + connector-level rollups exist; no daily subagent table.                                                                                                                                                                                                                                                                           | [`usage_rollup_loop.py`](../../src/runtime_worker/usage_rollup_loop.py)                                                                                           | Operations dashboards must scan raw rows for per-subagent cost reporting.                               |

### 1.2 The deeper architectural smell

Each of D1–D8 is locally fixable. The reason they all exist is a **layered design where attribution is _reconstructed_ after the fact rather than _carried_ with the call.** The current path:

```
provider chunk
    ↓
streaming_executor reads in-line
    ↓
active_subagent_tasks (worker-local set, mis-attributes when |set| > 1)
    ↓
UsageAttributionResolver.resolve(...) ── reads runtime_tool_invocations from DB
    ↓
run_metrics builds record with partially-filled dimensions
    ↓
handlers/run.py writes
```

The same problem produces D2 (subagent identity not threaded), D3 (parallel-task arbitration done worker-side), D5 (connector reconstructed via SQL heuristic), and D4 (a totally separate LLM call site doesn't even reach the path). When attribution is reconstructed downstream of the call, every new dimension requires a new heuristic.

**The fix is to _carry_ attribution.** Every LLM call has a well-defined attribution context at the moment it's made — the same code that decides which model to call, with which tools, on whose behalf, also knows whether this is a subagent task, which subagent, which originating tool, what the call is _for_. We just don't pass it cleanly today.

### 1.3 Behaviors that must NOT change

Per the user's standing rule ("preserve behaviors, not just delete files"):

- Cost is stamped at write time in integer micro-USD with banker's rounding; rows write through `_record_per_call_usage` / `_record_run_usage`. ([`pricing/calculator.py:62-67`](../../src/agent_runtime/pricing/calculator.py))
- `pricing_id` / `pricing_version` snapshotted on the row — retroactive price changes can't mutate history.
- `RuntimeRunUsageRecord` (run-level aggregate) shape is part of api-types contract; FE consumes via `/v1/usage/me`, `/v1/usage/org`, `/v1/usage/me/conversations`.
- `PresentationGenerator` is fully deterministic post-Phase-4 polish removal. **No LLM in the presentation path.** This PRD does NOT reintroduce one; it only proves the path stays cost-zero by asserting in tests.
- Rollup loop cadence (600s, trailing 2 days, 30-day cold-start backfill) is operational behavior — UPSERT semantics preserved.

---

## 2. Goal and non-goals

### 2.1 Goal

A capture path where:

1. **Every LLM call** in the codebase (runtime invoke + subagent + context compression + any future site) routes through one `UsageRecorder` Protocol.
2. **Attribution is carried, not looked up.** Each call site builds a `UsageAttributionContext` value object and hands it to the recorder. No DB heuristic.
3. **Token usage is normalized once at the provider boundary** into a `NormalizedTokenUsage` value object with explicit fields for every kind we price (input, output, cached_input, cache_creation_input, reasoning, audio_input, audio_output). Downstream code is provider-agnostic.
4. **The persisted row is the single source of truth for attribution.** Every dimension we want to slice cost by (org, user, run, conversation, subagent, originating tool, connector, model, purpose) is a typed column. Rollups derive from the row; new rollups are pure SQL.
5. **P12 can swap pricing source** by changing only the `CostCalculator` input shape — no capture-side changes.

### 2.2 Non-goals

- Re-architect `PresentationGenerator`. It stays deterministic.
- Replace the pricing catalog (that's P12).
- Change rollup cadence or backfill window.
- Add LLM calls. We're auditing what's there, not adding new ones.
- Retire `UsageAttributionResolver` immediately on the boundary code's behalf — sub-PRD §3 retires it in lockstep with carry-context propagation; the class is deletable once no caller remains.
- Migrate historical usage rows. New columns are nullable for pre-migration rows; rollups treat null as "unknown" rather than backfilling.

### 2.3 Success criteria

- One `UsageRecorder` Protocol; one record schema; one write path.
- `UsageAttributionContext` is required (not optional) at the recorder boundary — calling without one fails type-check.
- `NormalizedTokenUsage` has fields for every token kind LiteLLM prices.
- `UsageAttributionResolver` is deleted; no DB read happens at LLM-call-completed emit time.
- Context-compression LLM calls land on usage rows tagged `purpose=context_compression`.
- Parallel subagents under the same run produce distinct `task_id` + `subagent_slug` stamps on their respective LLM calls; a test pins this.
- Connector rollup carries `model_name`. A subagent rollup table exists.
- Pinned test: `PresentationGenerator` paths emit zero LLM calls — by passing a `UsageRecorder` fake that fails the test if `record()` is called from any presentation code path.

---

## 3. Architecture

### 3.1 The four contracts

```
┌─────────────────────────────────────────────────────────────────────┐
│ provider response / stream chunk (OpenAI / Anthropic / Google / …)  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
                  ┌────────────────────────┐
                  │   TokenUsageExtractor   │  Protocol — one impl per provider
                  │   .extract(chunk)       │  returns NormalizedTokenUsage | None
                  └────────────────────────┘
                              ↓
                  ┌────────────────────────┐
                  │ NormalizedTokenUsage    │  frozen Pydantic value object
                  │ input / output /        │  every kind we price; defaults 0
                  │ cached_input /          │
                  │ cache_creation_input /  │
                  │ reasoning /             │
                  │ audio_input/output      │
                  └────────────────────────┘
                              +
                  ┌────────────────────────┐
                  │ UsageAttributionContext │  frozen Pydantic value object
                  │ org_id, user_id,        │  carried from call site —
                  │ run_id, conv_id,        │  NEVER reconstructed
                  │ trace_id,               │
                  │ task_id?,               │
                  │ subagent_slug?,         │
                  │ parent_task_id?,        │
                  │ originating_tool_call_id│
                  │ originating_tool_name?, │
                  │ connector_slug?,        │
                  │ purpose                 │  enum: MAIN | SUBAGENT |
                  │                         │        CONTEXT_COMPRESSION | …
                  └────────────────────────┘
                              ↓
                  ┌────────────────────────┐
                  │   UsageRecorder         │  Protocol — one prod impl,
                  │   .record(context,      │  one fake-for-test, one
                  │           usage,        │  null-recorder-for-dev
                  │           model_call)   │
                  └────────────────────────┘
                              ↓
                  ┌────────────────────────┐
                  │ persistence write +     │  CostCalculator multiplies on
                  │ CostCalculator stamp    │  the same value object the row
                  └────────────────────────┘  is built from
```

The four contracts are:

1. **`TokenUsageExtractor`** — Protocol, one per provider. Normalizes chunks to a `NormalizedTokenUsage`. The _only_ provider-aware code on the usage path.
2. **`NormalizedTokenUsage`** — frozen Pydantic value object. Explicit fields for every token kind we price. Missing kinds default to 0 (not None) so pricing math is total.
3. **`UsageAttributionContext`** — frozen Pydantic value object. Built at the LLM call site, propagated through model-invocation wrappers, handed to the recorder. **No optional org_id / run_id** — those are required. Subagent / tool / connector / purpose fields are optional with documented semantics.
4. **`UsageRecorder`** — Protocol. Single boundary every LLM call site goes through. One method:
   ```python
   async def record(
       self,
       *,
       context: UsageAttributionContext,
       usage: NormalizedTokenUsage,
       model: ModelIdentity,
       duration_ms: int,
       message_id: str | None,
   ) -> RuntimeModelCallUsageRecord: ...
   ```
   The recorder owns: pricing lookup, cost stamping, row persistence, run-level aggregation. Callers don't know about pricing.

### 3.2 What this kills

| Kills                                                                                          | Why                                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `UsageAttributionResolver` (`observability/usage_attribution.py`)                              | Connector is now on `UsageAttributionContext`, stamped at the originating tool call. No DB read at emit time.                                                           |
| `active_subagent_tasks` set arbitration in `streaming_executor.py:151-152`                     | The LangGraph chunk carries the namespace; the namespace identifies the active subagent task. Carry, don't reconstruct.                                                 |
| `_MessageIdExtractor.extract` provider-coupling inside `streaming_executor.py`                 | Moves to provider-specific `TokenUsageExtractor` implementations. The streaming executor calls one method, gets a normalized object.                                    |
| Two parallel write paths in `handlers/run.py` (`_record_run_usage` + `_record_per_call_usage`) | `UsageRecorder.record` owns both. Per-call is the row of record; run-level is an aggregation projected from per-call rows (or a parallel idempotent UPSERT — see §3.5). |
| `subagent_id=None` hardcode at `run_metrics.py:373`                                            | The recorder requires a `UsageAttributionContext` whose `purpose=SUBAGENT_WORK` implies `subagent_slug` is set. Type-level guarantee.                                   |
| Silent leak in `summarization.py`                                                              | Compression flows through the same recorder with `purpose=CONTEXT_COMPRESSION`.                                                                                         |

### 3.3 What this preserves

| Preserves                                                        | How                                                                                                                                                  |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeModelCallUsageRecord` Pydantic class                     | Add columns (reasoning_tokens, cache_creation_input_tokens, audio_input/output, subagent_slug, tool_call_id, tool_name, purpose). No column removed. |
| `RuntimeRunUsageRecord` aggregate                                | Unchanged. Run-level rollup math unchanged.                                                                                                          |
| `/v1/usage/*` API contracts                                      | New fields added optionally to api-types; existing fields unchanged. FE renders new fields when present.                                             |
| Cost-stamped-at-write-time, integer micro-USD, banker's rounding | `CostCalculator` unchanged. The `UsageRecorder` calls it on the same data path.                                                                      |
| `pricing_id` / `pricing_version` snapshot on row                 | Unchanged.                                                                                                                                           |
| Rollup cadence + UPSERT semantics in `usage_rollup_loop.py`      | Unchanged. New dimensions added to rollup tables in a discrete sub-PRD (§4.5).                                                                       |
| `PresentationGenerator` deterministic, no LLM                    | Pinned by a test that fails if the presentation code path ever calls `UsageRecorder.record`.                                                         |

### 3.4 Design principles enforced

- **Carry, don't look up.** Attribution context is a value-object input to every recorder call. No SQL heuristic on the emit path.
- **Single source of truth.** One record schema. One recorder. One extractor Protocol. No parallel writers.
- **Substitution.** `UsageRecorder` is a Protocol; production impl writes to Postgres, dev impl writes to in-memory, test fake counts calls. Same interface.
- **Make impossible states unrepresentable.** The recorder signature requires `UsageAttributionContext` — callers cannot record without one. `Purpose=SUBAGENT_WORK` is enforced to imply `subagent_slug is not None` via a model-level validator (Pydantic root-validator).
- **DRY.** Provider-specific code lives in extractors. Everything else is provider-agnostic.
- **No new heuristics.** If a dimension can't be carried from the call site, it stays null on the row — better honest-null than a wrong heuristic.

### 3.5 One open trade-off: per-call rows vs run-level aggregate

Today the run-level aggregate (`RuntimeRunUsageRecord`) is its own row UPSERTed in lockstep with per-call rows. Two ways forward:

- **Option A (keep both):** the recorder writes per-call AND UPSERTs run-level. Two writes per LLM call, mirror invariant guarded by tests. Today's behavior, no FE change.
- **Option B (project run from per-call):** rollup loop or a view derives run-level from per-call SUM. One write per LLM call. Cheaper but FE reads need to use the view, and any read happening between LLM call and rollup tick sees stale run totals.

§9 — confirm with ops. **Default: Option A** (preserve current behavior).

---

## 4. Phasing — sub-PRDs (4)

Four sub-PRDs, each one PR. No feature flags — each PR is a direct cutover. The old code path is deleted in the same PR that lands the new one.

| Sub-PRD                                                                                                  | Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Risk        | Depends on |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ---------- |
| **[01a — Normalized token shape](01a-usage-normalized-token-shape.md)** ✅ shipped 2026-05-11            | Add `NormalizedTokenUsage` value object. Add `ProviderTokenUsageExtractor` Protocol with per-provider implementations (OpenAI, Anthropic, Gemini). Add columns to `RuntimeModelCallUsageRecord` for `reasoning_tokens`, `cache_creation_input_tokens`, `audio_input_tokens`, `audio_output_tokens`, all defaulting to 0. Alembic migration 0027. **Deleted** the in-line `TokenUsageExtractor` in `run_metrics.py` and its provider-coupled walker — `streaming_executor` gates emit through `metrics.chunk_has_usage(source)`. No attribution changes yet.                                                                                                                                                                                                                                 | Low         | —          |
| **[01b — Carry attribution; delete heuristics](01b-usage-attribution-context.md)** ✅ shipped 2026-05-11 | Define `UsageAttributionContext` + `Purpose` enum (`MAIN / TOOL_PLANNING / TOOL_INTERPRETATION / SUBAGENT_WORK / CONTEXT_COMPRESSION` with deterministic `Purpose.derive`). Build the context at every LLM call site via `_AttributionBuilder` reading chunk namespace + supervisor_task_call_id metadata + tool ledger pop. Add columns: `purpose`, `originating_tool_call_id`, `originating_tool_name` (subagent_id + connector_slug already existed; now populated). **Deleted `UsageAttributionResolver`** + its tests + the `query_last_completed_tool_connector_slug` Protocol method + both adapter implementations. **Deleted `active_subagent_tasks` set arbitration** (still kept as boolean signal for citation/delta gating, but no longer drives attribution). Migration 0028. | Medium-High | 01a        |
| **[01c — Single UsageRecorder boundary](01c-usage-recorder.md)**                                         | Define `UsageRecorder` Protocol; one `PostgresUsageRecorder` impl, one `InMemoryUsageRecorder` for tests. Collapse `_record_run_usage` + `_record_per_call_usage` in `handlers/run.py` into the recorder. Wire `summarization.py` through the recorder with `purpose=CONTEXT_COMPRESSION` (kills D4). **Delete** the two parallel writers.                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Medium      | 01b        |
| **[01d — Rollup expansion](01d-usage-rollup-expansion.md)**                                              | Add `model_name` column to `UsageDailyConnectorRow` (extends PK). Add `UsageDailySubagentRow`. Add `UsageDailyPurposeRow`. Add `/v1/usage/me/subagents`, `/v1/usage/org/subagents`, `/v1/usage/me/purpose`, `/v1/usage/org/purpose` endpoints + api-types contracts + FE rollup panel sections. Backfill via the rollup loop's existing 30-day cold-start window.                                                                                                                                                                                                                                                                                                                                                                                                                           | Medium      | 01c        |

### 4.1 Why this order

- **01a first** because P12 hard-blocks on the missing token kinds. If P12 ships before 01a, prices for reasoning and prompt-cache writes are wrong. Once 01a lands, the rest can land in any order; P12 can ship in parallel.
- **01b before 01c** because the recorder Protocol's signature requires the attribution context. Defining the context first means the Protocol shape is final the first time it lands.
- **01c before 01d** because the recorder owning per-call writes is what guarantees `subagent_slug` + `purpose` are populated on every row — which is what makes subagent / purpose rollups meaningful.

### 4.2 No feature flags

This project's standing position is no feature flags. Each sub-PRD is a direct cutover:

- Old code is **deleted** in the same PR that lands the new code.
- Risk is managed by (a) pinned tests on preserved behaviors, (b) Alembic migrations are additive-only (new columns default to safe values), (c) PR boundaries are scoped so a revert is a single git revert.
- No `RUNTIME_USE_*` env switches. No legacy/new branches in code. No "shadow write."

---

## 5. Schema changes (consolidated)

All changes are additive. No column dropped. No row migrated.

### 5.1 `runtime_model_call_usage` (per-call row)

| Column                        | Type          | Default  | Added by |
| ----------------------------- | ------------- | -------- | -------- |
| `reasoning_tokens`            | int4 NOT NULL | 0        | 01a      |
| `cache_creation_input_tokens` | int4 NOT NULL | 0        | 01a      |
| `audio_input_tokens`          | int4 NOT NULL | 0        | 01a      |
| `audio_output_tokens`         | int4 NOT NULL | 0        | 01a      |
| `subagent_slug`               | text NULL     | NULL     | 01b      |
| `tool_call_id`                | text NULL     | NULL     | 01b      |
| `tool_name`                   | text NULL     | NULL     | 01b      |
| `purpose`                     | text NOT NULL | `'main'` | 01b      |

`task_id` and `connector_slug` are existing columns; their semantics are tightened (no longer reconstructed) in 01b/01d but the column itself doesn't change.

### 5.2 `runtime_usage_daily_connector` (connector rollup)

| Column       | Type | Added by |
| ------------ | ---- | -------- |
| `model_name` | text | 01e      |

Primary key extends from `(org_id, day, connector_slug)` to `(org_id, day, connector_slug, model_name)`.

### 5.3 New: `runtime_usage_daily_subagent`

| Column                        | Type                                                 |
| ----------------------------- | ---------------------------------------------------- |
| `org_id`                      | text                                                 |
| `subagent_slug`               | text                                                 |
| `day`                         | date                                                 |
| `provider`                    | text                                                 |
| `model_name`                  | text                                                 |
| `input_tokens`                | int8                                                 |
| `output_tokens`               | int8                                                 |
| `cached_input_tokens`         | int8                                                 |
| `cache_creation_input_tokens` | int8                                                 |
| `reasoning_tokens`            | int8                                                 |
| `audio_input_tokens`          | int8                                                 |
| `audio_output_tokens`         | int8                                                 |
| `total_tokens`                | int8                                                 |
| `cost_micro_usd`              | int8                                                 |
| PK                            | `(org_id, subagent_slug, day, provider, model_name)` |

Added by 01e.

### 5.4 New: `runtime_usage_daily_purpose`

Same shape as `runtime_usage_daily_subagent` but keyed on `purpose` instead. Lets ops answer "how much did context compression cost org X last week" without scanning raw rows. Added by 01e.

---

## 6. The Pydantic surface (the contract shapes)

Pinned shapes for implementing agents to follow. Field-level only — full PRDs in sub-files.

### 6.1 `NormalizedTokenUsage` (01a)

```python
class NormalizedTokenUsage(BaseModel):
    """Provider-agnostic token-usage value object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0

    @computed_field  # type: ignore[misc]
    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.audio_input_tokens
            + self.audio_output_tokens
        )
```

`cached_input_tokens` and `cache_creation_input_tokens` are NOT additional to `input_tokens` — they're subsets that get distinct pricing. Pricing math:

```
cost =   (input - cached - cache_creation) * price_input
       + cached                            * price_cached_input
       + cache_creation                    * price_cache_creation
       + output                            * price_output
       + reasoning                         * price_reasoning
       + audio_in                          * price_audio_input
       + audio_out                         * price_audio_output
```

(See P12 PRD §4 for the LiteLLM key names this maps to.)

### 6.2 `UsageAttributionContext` (01b)

```python
class Purpose(StrEnum):
    """What this LLM call is for. Drives both attribution and pricing buckets.

    Determined deterministically from the call's input messages + output
    (see :meth:`derive`). One Purpose per row. Reports group by Purpose
    when answering "what did context compression cost", "how much of the
    LLM bill is tool-result interpretation", etc.
    """

    MAIN = "main"
    """Orchestrator planning. No ToolMessage in input, no tool_calls in
    output. The cost of "thinking about what to do next" without a
    tool-result in context."""

    TOOL_PLANNING = "tool_planning"
    """No ToolMessage in input; output contains one or more tool_calls.
    The cost of "deciding to use tool X." Apportioned across all
    tool_calls in the output."""

    TOOL_INTERPRETATION = "tool_interpretation"
    """Input contains at least one ToolMessage. The cost of "making
    sense of tool X's output." Dominant Purpose when an LLM call both
    interprets prior results AND plans the next tool — the
    interpretation is the user-facing semantic, so it wins."""

    SUBAGENT_WORK = "subagent_work"
    """Any LLM call inside a delegated subagent task. Subagent rollups
    key on (subagent_slug, task_id); cross-subagent phase analysis
    isn't a current product need — collapsed to one bucket."""

    CONTEXT_COMPRESSION = "context_compression"
    """``summarization.py`` path. The cost of context-window squeeze
    after long conversations."""

    @classmethod
    def derive(
        cls,
        *,
        input_has_tool_message: bool,
        output_has_tool_calls: bool,
        is_subagent: bool,
        is_compression: bool,
    ) -> "Purpose":
        """Single source of truth for Purpose classification.

        Precedence (top wins):
        1. ``is_compression`` → CONTEXT_COMPRESSION
        2. ``is_subagent``    → SUBAGENT_WORK
        3. ``input_has_tool_message`` → TOOL_INTERPRETATION
        4. ``output_has_tool_calls``  → TOOL_PLANNING
        5. otherwise          → MAIN

        Order matters. A subagent's tool-interpretation call collapses
        to SUBAGENT_WORK (subagent slug is the dominant attribution
        cut). A main-loop call that both interprets and plans
        collapses to TOOL_INTERPRETATION.
        """

        if is_compression:
            return cls.CONTEXT_COMPRESSION
        if is_subagent:
            return cls.SUBAGENT_WORK
        if input_has_tool_message:
            return cls.TOOL_INTERPRETATION
        if output_has_tool_calls:
            return cls.TOOL_PLANNING
        return cls.MAIN


class UsageAttributionContext(BaseModel):
    """Carried with every LLM call. Built at the call site; never reconstructed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    user_id: str
    run_id: str
    conversation_id: str
    trace_id: str
    purpose: Purpose

    task_id: str | None = None
    parent_task_id: str | None = None
    subagent_slug: str | None = None

    originating_tool_call_id: str | None = None
    originating_tool_name: str | None = None
    connector_slug: str | None = None

    @model_validator(mode="after")
    def _purpose_invariants(self) -> "UsageAttributionContext":
        if self.purpose == Purpose.SUBAGENT_WORK and self.subagent_slug is None:
            raise ValueError("subagent_slug required when purpose=subagent_work")
        if self.purpose == Purpose.TOOL_INTERPRETATION and self.originating_tool_call_id is None:
            raise ValueError(
                "originating_tool_call_id required when purpose=tool_interpretation"
            )
        if self.subagent_slug is not None and self.task_id is None:
            raise ValueError("task_id required whenever subagent_slug is set")
        return self
```

Invariants enforced by Pydantic at construction time — the runtime never carries a partially-attributed row:

- `Purpose.SUBAGENT_WORK` ⇒ `subagent_slug != None`
- `Purpose.TOOL_INTERPRETATION` ⇒ `originating_tool_call_id != None`
- `subagent_slug != None` ⇒ `task_id != None`

### 6.3 `UsageRecorder` (01c)

```python
@runtime_checkable
class UsageRecorder(Protocol):
    """Single boundary for persisting LLM token usage.

    Production: PostgresUsageRecorder — writes per-call row, computes cost,
    UPSERTs run-level aggregate.
    Tests: InMemoryUsageRecorder — counts calls, exposes captured records.
    Dev (no-cost mode): NullUsageRecorder — accepts and discards.
    """

    async def record(
        self,
        *,
        context: UsageAttributionContext,
        usage: NormalizedTokenUsage,
        model: ModelIdentity,
        duration_ms: int,
        message_id: str | None,
    ) -> RuntimeModelCallUsageRecord: ...
```

One method. Substitutable. Test fakes never have to model partial writers.

### 6.4 `TokenUsageExtractor` (01a)

```python
@runtime_checkable
class TokenUsageExtractor(Protocol):
    """Per-provider normalizer. The ONLY provider-aware code on the usage path."""

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class OpenAITokenUsageExtractor:
    """Reads `chunk.response_metadata.token_usage` + Responses-API
    `chunk.response_metadata.usage` (reasoning_tokens, cached_tokens)."""

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class AnthropicTokenUsageExtractor:
    """Reads `chunk.usage` with cache_creation_input_tokens
    + cache_read_input_tokens (Anthropic-specific names)."""

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class GoogleTokenUsageExtractor:
    """Reads `chunk.usage_metadata` (Gemini)."""

    def extract(self, chunk: object) -> NormalizedTokenUsage | None: ...


class TokenUsageExtractorRegistry:
    """Resolve extractor by provider tag on the model identity."""

    @classmethod
    def for_provider(cls, provider: str) -> TokenUsageExtractor: ...
```

Provider-specific keys all collapse into the same value object.

---

## 7. Behaviors preserved (consolidated)

| Behavior                                                                | How preserved                                                                                                            |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Per-call row written for every model call that returns usage            | `PostgresUsageRecorder.record` is called from the same emit boundary that `_record_per_call_usage` is called from today. |
| Run-level aggregate UPSERTed in lockstep                                | Recorder does both writes (Option A in §3.5). Tests pin parity.                                                          |
| Cost stamped at write time, integer micro-USD, banker's rounding        | `CostCalculator` unchanged; called from the recorder.                                                                    |
| `pricing_id` / `pricing_version` snapshot on row                        | Unchanged.                                                                                                               |
| `RuntimeRunUsageRecord` shape                                           | Unchanged. New columns only on `RuntimeModelCallUsageRecord` and on new rollup tables.                                   |
| `/v1/usage/me`, `/v1/usage/org`, `/v1/usage/me/conversations` contracts | Unchanged. New endpoints in 01e are additive.                                                                            |
| Rollup cadence (600s) + cold-start backfill (30 days)                   | Unchanged.                                                                                                               |
| `PresentationGenerator` deterministic, zero LLM                         | A pinned test in 01c fails if a recorder call is ever attributed to the presentation path.                               |
| Pricing miss → row stays `cost_micro_usd=NULL`                          | Unchanged.                                                                                                               |

---

## 8. Risks

| Risk                                                                                                                | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Provider extractor misreads a new chunk shape (e.g. OpenAI Responses-API change)                                    | Medium     | Medium | Each extractor has a snapshot-fixture test with a representative chunk from each provider. Adding a new model that emits a new shape lands a fixture before code.                                                                                                                         |
| `UsageAttributionContext` construction at every call site is invasive                                               | Medium     | Medium | Sub-PRD 01b enumerates every LLM-invocation call site up front (§3 file inventory below). One PR, one cutover. The audit confirmed three call sites total (streaming_executor, summarization, the runtime invoke wrappers in `execution/runtime.py`) — small enough for atomic migration. |
| Pydantic validator on `Purpose.SUBAGENT_WORK ⇒ subagent_slug != None` fires in production due to a missed call site | Low        | High   | Validator surfaces with a typed `AgentRuntimeError`, caught by the recorder, logged at ERROR, falls back to `purpose=MAIN`. Fail-soft — no token row dropped. Errors tracked in metrics.                                                                                                  |
| Subagent slug isn't available at call time (e.g. the runtime context wasn't told which subagent it's in)            | Medium     | Medium | Sub-PRD 01b explicitly traces every subagent dispatch path; the slug becomes part of `AgentRuntimeContext`. Subagent dispatch without a slug fails at construction.                                                                                                                       |
| Adding columns to a high-write table (`runtime_model_call_usage`) takes a lock                                      | Low        | Medium | Defaults on all new columns. Alembic migration runs `ALTER TABLE ... ADD COLUMN ... DEFAULT 0 NOT NULL`; Postgres 11+ does this without rewriting the table.                                                                                                                              |
| Rollup loop processes a row mid-migration where new columns are NULL                                                | Low        | Low    | New rollup math sums new columns with `COALESCE(col, 0)`. Pre-migration rows contribute 0 to new-token-kind totals.                                                                                                                                                                       |
| `summarization.py` LLM call has no `conversation_id` / `user_id` because compression runs in a worker job context   | Medium     | Low    | Compression is always called inside a `RuntimeRunHandler` scope — the run handler builds the context from `AgentRuntimeContext`. If a future async compression path runs outside a run, the recorder rejects the call (no `run_id`) and an audit-trail event records the orphan tokens.   |

---

## 9. Decisions (resolved 2026-05-11)

- **Run-level aggregate:** Option A — recorder writes per-call AND UPSERTs run-level in the same call. Preserves FE behavior.
- **Tool attribution granularity:** both `originating_tool_call_id` and `originating_tool_name` columns, set together.
- **`Purpose` enum:** expanded to five values — `MAIN / TOOL_PLANNING / TOOL_INTERPRETATION / SUBAGENT_WORK / CONTEXT_COMPRESSION`. Classification is deterministic via `Purpose.derive(...)`; precedence documented inline (§6.2).
- **`UsageAttributionContext` propagation:** explicit argument through model-invocation wrappers. No ContextVar — invisible state hides attribution bugs and breaks substitution.
- **Rollup endpoints:** three discrete endpoints (`/subagents`, `/purpose`, `/connector` already keyed on connector_slug + model). No flexible `/breakdown?dimension=` — narrow contracts beat query-shape sprawl on the FE.
- **No feature flags.** Each sub-PRD deletes old code in the PR that adds new code. Direct cutover. Risk managed by tests, not toggles.

---

## 10. Test requirements (per sub-PRD)

Each sub-PRD has its own test section; what follows is the cross-cutting set the parent PRD enforces.

- **No SQL read at emit time** (01d). A `BlockingQueueAdapter` test wraps the persistence port; if any `SELECT` query lands on it during the LLM stream loop, the test fails.
- **Presentation cost = 0** (01c). The recorder receives a `purpose` field; an integration test runs a full `f1-single-turn` flow and asserts no recorder call has `originating_tool_name` set to a presentation marker and no call has `purpose=presentation` (the enum has no such value).
- **Parallel subagent attribution** (01d). Two subagent tasks running concurrently on one run; each emits LLM chunks; assert `task_id` + `subagent_slug` on the resulting rows partition cleanly.
- **Context-compression captured** (01c). Triggering a compression event produces a usage row with `purpose=context_compression`.
- **Token-kind extraction pinned per provider** (01a). Snapshot fixtures for OpenAI Responses-API o-series (reasoning), Anthropic prompt-cache write, Google Gemini, plus the existing OpenAI streaming chunks. Each snapshot maps to an expected `NormalizedTokenUsage`.
- **Pydantic invariant enforcement** (01b). `UsageAttributionContext(purpose=SUBAGENT_WORK, subagent_slug=None)` raises at construction.
- **P12 hand-off contract** (parent). Cross-cutting test that proves `NormalizedTokenUsage` + `RuntimeModelCallUsageRecord` carries every dimension P12's LiteLLM pricing rows price on. If P12 lands and `ModelPricingCatalog` references a token kind not on the record, this test fails.

---

## 11. Rollout / rollback

### 11.1 Rollout

Direct cutover per sub-PRD. No flags, no shadow writes.

1. **01a.** Alembic migration adds new columns (NOT NULL DEFAULT 0). Provider extractors land + the registry replaces `_MessageIdExtractor`. New token kinds begin landing on every row in the same deploy.
2. **01b.** `UsageAttributionContext` lands. Every LLM call site builds one. `UsageAttributionResolver` and `active_subagent_tasks` arbitration are deleted in the same PR. New columns (`subagent_slug`, `originating_tool_call_id`, `originating_tool_name`, `purpose`) populated by every emit from this point forward.
3. **01c.** `UsageRecorder` lands. `handlers/run.py` parallel writers are deleted. `summarization.py` wired. Run-level UPSERT semantics preserved by the recorder.
4. **01d.** New rollup tables + endpoints. FE panel updated. The rollup loop's existing 30-day cold-start window backfills new tables from raw rows.

### 11.2 Rollback

Each PR is a single git revert. Alembic migrations are additive-only — a revert leaves new columns in place; no `DROP COLUMN`. The previous code reads the columns it knows.

- **01a:** revert. New columns remain on the table; old extraction path restored (provider-coupled in-line extraction). No data loss.
- **01b:** revert. Resolver + arbitration code restored. New attribution columns remain on the table; they go back to NULL for future rows. Old rows keep their populated values.
- **01c:** revert. Old `_record_*` writers restored. Recorder Protocol becomes an orphaned import (deletable in a follow-up clean-up).
- **01d:** revert. New rollup tables stop being written; the loop resumes the smaller schema. FE panel re-renders without the new sections.

Backfill story for rolled-back columns: if 01b is reverted then re-landed, rows written during the reverted window carry NULL for attribution columns. Acceptable — those rows are reported as `purpose=unknown` / `subagent_slug=NULL` in rollups, which matches their actual lossy state.

---

## 12. Done definition

- All four sub-PRDs landed and `Status: Shipped`.
- `UsageAttributionResolver` deleted.
- `_MessageIdExtractor` provider-coupling deleted.
- `active_subagent_tasks` set arbitration deleted.
- `summarization.py` LLM calls land on usage rows with `purpose=context_compression`.
- Parallel-subagent attribution test green.
- "No SQL at emit time" test green.
- `NormalizedTokenUsage` shape pinned to a public surface that P12 can target.
- No feature flags introduced.
- Roadmap status row flipped.
- This PRD `Status: Shipped`.

---

## Appendix A — relationship to other Phase-3 PRDs

- **P11 (redaction):** independent. Usage row's `tool_name` / `originating_tool_name` strings are domain tags, never user content — no redaction interaction.
- **P12 (pricing-from-LiteLLM):** capture-side prerequisite. P12 plugs into `NormalizedTokenUsage` (the column shape) and `ModelIdentity` (the lookup key). With this PRD landed, P12 is a `ModelPricingCatalog` swap — no capture-side change.
- **P13 (OTel coverage hardening):** independent. Cross-process trace propagation puts trace_id on usage rows that already had it; no contract change.

## Appendix B — file inventory of work

**Touched in 01a:**

- `agent_runtime/observability/token_usage.py` (new — `NormalizedTokenUsage`, `TokenUsageExtractor`, per-provider impls, `TokenUsageExtractorRegistry`)
- `agent_runtime/persistence/records/telemetry.py` (new token-kind columns)
- `runtime_worker/run_metrics.py` (delete `_MessageIdExtractor`; call the registry)
- `runtime_worker/streaming_executor.py` (use registry)
- Alembic migration: `runtime_model_call_usage` new columns

**Touched in 01b:**

- `agent_runtime/observability/attribution.py` (new — `UsageAttributionContext`, `Purpose`, `Purpose.derive`)
- `agent_runtime/execution/runtime.py` (build context, pass through)
- `agent_runtime/context/contracts.py` or equivalent — propagate `subagent_slug` on `AgentRuntimeContext` when entering a subagent
- `runtime_worker/handlers/run.py`
- `runtime_worker/streaming_executor.py` (delete `active_subagent_tasks` arbitration; read slug from chunk namespace)
- `agent_runtime/delegation/` (subagent dispatch sets subagent_slug)
- **Delete:** `agent_runtime/observability/usage_attribution.py`
- Alembic migration: `subagent_slug`, `originating_tool_call_id`, `originating_tool_name`, `purpose` columns

**Touched in 01c:**

- `agent_runtime/observability/usage_recorder.py` (new — `UsageRecorder` Protocol, `PostgresUsageRecorder`, `InMemoryUsageRecorder`)
- `runtime_worker/handlers/run.py` (delete `_record_run_usage` + `_record_per_call_usage`; delegate to recorder)
- `agent_runtime/context/memory/summarization.py` (wire through recorder)
- `runtime_worker/run_metrics.py` (collapsed into recorder)

**Touched in 01d:**

- `runtime_worker/usage_rollup_loop.py` (new rollup queries)
- `agent_runtime/api/usage_service.py` (subagent + purpose endpoints)
- `agent_runtime/persistence/records/telemetry.py` (new rollup record classes)
- `packages/api-types/src/index.ts`
- `apps/frontend/src/features/chat/components/details/UsagePanel.tsx`
- Alembic migrations: `runtime_usage_daily_connector` add `model_name`; new `runtime_usage_daily_subagent` + `runtime_usage_daily_purpose` tables
