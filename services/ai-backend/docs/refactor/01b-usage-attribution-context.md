# Sub-PRD 01b — Carry Attribution; Delete Heuristics

**Status:** Shipped 2026-05-11
**Parent:** [01-usage-capture-and-attribution.md](01-usage-capture-and-attribution.md)
**Position in plan:** P11.7.b (second of four sub-PRDs)
**Depends on:** [01a — Normalized token shape](01a-usage-normalized-token-shape.md) ✅ shipped
**Risk:** Medium-High. Touches every LLM-call emit boundary and deletes a production module.

> **What this PR is.** The capture layer's "attribution is reconstructed downstream" smell ends here. A typed `UsageAttributionContext` value object is built at the LLM emit boundary from signals already present on the stream chunk + tool ledger. The 192-line `UsageAttributionResolver` (a time-based SQL heuristic) is deleted in the same PR. So is the `active_subagent_tasks` set arbitration that mis-attributes under parallelism. Net code delta: subtractive at the system level (resolver gone) and substitutive at the call site (one new value-object construction replaces three implicit lookups).

---

## 1. Problem

### 1.1 Resolver was reading a table nobody writes

`agent_runtime/observability/usage_attribution.py` is invoked at every `MODEL_CALL_COMPLETED` emit by `streaming_executor._maybe_emit_model_call_completed`. It queries `runtime_tool_invocations` for the most recent completed tool invocation < emit-time and returns its `connector_slug`. Audit on 2026-05-11 found:

> No code in `services/ai-backend/src` constructs `ToolInvocationRecord(...)` or writes to `runtime_tool_invocations`. The table is empty in production. **Today's connector attribution is a silent no-op.**

So the resolver does an extra DB read per LLM call to look up an empty table. Net cost: latency + a Pydantic round-trip for nothing.

### 1.2 Subagent identity is hardcoded `None`

[`run_metrics.py:373`](../../src/runtime_worker/run_metrics.py) writes `subagent_id=None` on every per-call usage row. The column shape supports it. The reason it's `None`: there's no place in the current code path that resolves a chunk to a subagent slug at emit time.

Meanwhile, [`stream_subagents.py:60`](../../src/runtime_worker/stream_subagents.py) maintains `_subagent_name_by_call_id: (run_id, call_id) → subagent_name`, [`stream_subagents.py:65`](../../src/runtime_worker/stream_subagents.py) maintains `_subagent_call_id_by_subgraph_id: (run_id, subgraph_task_id) → call_id`, and [`subagent_id_for_subgraph(...)`](../../src/runtime_worker/stream_subagents.py) already returns the slug. The data is one method call away from the emit boundary — the emit just doesn't ask.

### 1.3 Parallel subagents mis-attribute

[`streaming_executor.py:151-152`](../../src/runtime_worker/streaming_executor.py):

```python
current_task_id = (
    next(iter(active_subagent_tasks)) if active_subagent_tasks else None
)
```

`active_subagent_tasks` is a `set[str]`. When two subagents run in parallel, `next(iter(set))` returns whichever Python's set iteration happens to yield first. Every chunk on the run gets stamped with that arbitrary task_id, so per-task rollups are silently wrong under parallelism.

Meanwhile, every chunk already carries its own task_id via [`StreamPartParser.supervisor_task_call_id_for(part)`](../../src/runtime_worker/stream_parts.py) — backed by the metadata `atlas_task_tool` injects into each subagent's `RunnableConfig`. The deterministic answer is one method call away.

### 1.4 No `Purpose` classification on rows

Per-call rows today carry no signal for "what was this LLM call FOR" — orchestrator planning vs tool interpretation vs subagent work vs context compression. Cost reports can't split by purpose. The parent PRD §6.2 mandates a `Purpose` enum classified deterministically at emit time.

---

## 2. Goals

1. **Carry, don't look up.** Build a typed `UsageAttributionContext` value object at the LLM emit boundary from signals that already arrive with the chunk + ledger. Stamp it on the per-call slot; row builder reads from it.
2. **Deterministic parallel-subagent attribution.** Pull `task_id` and `subagent_slug` from the chunk's namespace + the existing `subagent_id_for_subgraph(...)` resolver. Delete the set arbitration.
3. **Delete the dead resolver.** Remove `UsageAttributionResolver` and the never-populated `query_last_completed_tool_connector_slug` port method from in-memory + postgres adapters. Drop the test file.
4. **Originating tool carry.** Extend `ToolCallLedger` to track the most recent settled tool per scope (orchestrator vs each subagent). Pop the carry at the next LLM emit in the same scope so `originating_tool_call_id` / `originating_tool_name` land on the row.
5. **Five-value `Purpose` enum.** Implement `Purpose.derive(...)` with the precedence documented in the parent PRD §6.2. Stamp on every row.
6. **Type-level safety.** Pydantic invariants on `UsageAttributionContext` make impossible-attribution states unrepresentable (subagent without slug, tool_interpretation without call_id).

## 3. Non-goals

- **Wire `connector_slug` populating into the ledger from MCP descriptors.** The ledger gains a `connector_slug` field defaulting to `None`; populating it requires a tool-name → connector lookup table that's a separate concern. **Today's behavior preserved: `connector_slug` stays NULL on rows.** (It was effectively NULL already — the resolver read a never-populated table.) The carry mechanism is in place; populating side is the natural next iteration.
- **Wire `summarization.py` through attribution.** That's 01c (`UsageRecorder`).
- **Rollup by purpose / subagent_slug.** That's 01d.
- **Backfill historical rows.** Existing rows keep `purpose='main'` (column default) and `originating_tool_*=NULL`. Honest-null beats wrong-value.
- **Change AgentRuntimeContext to fork per subagent.** The audit confirmed no per-subagent runtime context exists today; the chunk-namespace path doesn't need one.

## 4. Architecture

### 4.1 Signal sources at emit

For each LLM call closing chunk, three signals feed the `UsageAttributionContext`:

```
                    ┌─────────────────────────────────────┐
                    │       chunk (LangGraph envelope)      │
                    └─────────────────────────────────────┘
                                      │
       ┌──────────────────────────────┼──────────────────────────────┐
       ▼                              ▼                              ▼
StreamNamespace.from_value     StreamPartParser.                     AIMessage
  (chunk["ns"])                  supervisor_task_call_id_for         (.tool_calls)
       │                              │                              │
       │                              │                              │
       ▼                              ▼                              ▼
  subagent_task_id              supervisor_task_call_id          output_has_tool_calls
       │ (subgraph UUID)              │ (= our task_id)
       │                              │
       └──────────────┬───────────────┘
                      ▼
       StreamUpdateProcessor.
         subagent_id_for_subgraph(...)         ToolCallLedger.
                      │                          pop_pending_attribution(scope)
                      ▼                                       │
                subagent_slug              originating_tool_call_id +
                                           originating_tool_name +
                                           (connector_slug stays None for now)
                                                       │
                                                       ▼
       ┌─────────────────────────────────────────────────────────┐
       │ Purpose.derive(is_subagent, input_has_tool_message,     │
       │                output_has_tool_calls, is_compression)   │
       └─────────────────────────────────────────────────────────┘
                                       │
                                       ▼
       ┌─────────────────────────────────────────────────────────┐
       │           UsageAttributionContext (frozen)              │
       └─────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                          metrics.per_call.observe(
                              usage, message_id, context=...
                          )
```

### 4.2 The contracts

#### `Purpose` (StrEnum)

Per parent PRD §6.2 — five values, deterministic `Purpose.derive(...)` precedence.

```python
class Purpose(StrEnum):
    MAIN = "main"
    TOOL_PLANNING = "tool_planning"
    TOOL_INTERPRETATION = "tool_interpretation"
    SUBAGENT_WORK = "subagent_work"
    CONTEXT_COMPRESSION = "context_compression"

    @classmethod
    def derive(
        cls,
        *,
        input_has_tool_message: bool,
        output_has_tool_calls: bool,
        is_subagent: bool,
        is_compression: bool,
    ) -> "Purpose": ...
```

Precedence (top wins): compression → subagent → interpretation → planning → main.

#### `UsageAttributionContext` (frozen Pydantic)

```python
class UsageAttributionContext(BaseModel):
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
        if (
            self.purpose == Purpose.TOOL_INTERPRETATION
            and self.originating_tool_call_id is None
        ):
            raise ValueError(
                "originating_tool_call_id required when purpose=tool_interpretation"
            )
        if self.subagent_slug is not None and self.task_id is None:
            raise ValueError("task_id required whenever subagent_slug is set")
        return self
```

### 4.3 ToolCallLedger pending-attribution extension

Today's [`ToolCallEntry`](../../src/runtime_worker/tool_call_ledger.py):

```python
@dataclass
class ToolCallEntry:
    call_id: str
    tool_name: str
    parent_task_id: str | None = None
    subagent_id: str | None = None
    started_at: datetime
    settled: bool = False
    input_tokens: int | None = None
    budget_charged: bool = True
```

Additions:

- `connector_slug: str | None = None` field (defaults None; populating side is a follow-up — see §3 non-goals).
- `consumed_for_attribution: bool = False` flag — set to True once an LLM emit reads the entry as its originating tool.

New ledger methods:

- `mark_pending_attribution(call_id: str)` — called when TOOL_RESULT settles a call (today's `observed_settled` continues to mark the settled flag; this method is invoked alongside it). Idempotent.
- `pop_pending_attribution(scope_key: str | None) -> ToolCallEntry | None` — returns the most-recent-settled-and-unconsumed entry whose `subagent_id == scope_key`; marks it consumed. Pop semantics:
  - Scope key is the subagent_id of the LLM call (or None for orchestrator-scope calls).
  - Returns the entry with the latest `settled_at` timestamp matching the scope.
  - Multiple parallel tools fired in one agent step (e.g. ReAct): the pop returns the latest one. That's deliberate — single representative attribution, not proportional split (parent PRD §9 documents the trade-off; can revisit if a real report ever needs proportional cost-by-tool).

Scope-aware lookups are essential under parallel subagents — a parallel `researcher` subagent's TOOL_RESULT must not stamp an LLM emit running inside a `writer` subagent. Scope key = `subagent_id`.

### 4.4 Streaming executor wiring

[`streaming_executor.py`](../../src/runtime_worker/streaming_executor.py) changes:

1. Remove `active_subagent_tasks: set[str]` and `next(iter(...))` arbitration.
2. Remove `attribution: UsageAttributionResolver | None = None` parameter from `StreamingExecutor.run`.
3. Remove the import + the `if attribution is not None and slot.connector_slug is None: slot.connector_slug = await attribution.resolve(...)` block.
4. Add a helper `_build_attribution_context(...)` that takes the chunk + run + update_processor + ledger and returns a `UsageAttributionContext`. Called inside `_maybe_emit_model_call_completed` right before `mark_completed`.
5. Pass the context to `metrics.per_call.observe(...)` so the slot stamps it.

The `track_subagents` parameter (driven by the same `subagent_started` / `subagent_completed` event loop) stays — it's needed for `result.saw_task_subagent` and `result.subagent_summaries`. But its task-tracking is now read-only (no arbitration).

### 4.5 PerCallSlot stamping

`_PerCallSlot` (per [01a](01a-usage-normalized-token-shape.md)) extends with:

```python
__slots__ = (
    "message_id",
    "task_id",            # now sourced from chunk metadata
    "subagent_id",        # NEW: sourced from update_processor (slug)
    "connector_slug",
    "originating_tool_call_id",  # NEW
    "originating_tool_name",     # NEW
    "purpose",                   # NEW (StrEnum)
    "usage",
    "started_at",
    "completed_at",
)
```

`observe(usage, *, message_id, context: UsageAttributionContext | None = None, ...)` — when context is provided, fields are stamped onto the slot. The accumulator stays single-purpose (per-AIMessage usage); the context attaches alongside.

### 4.6 Handlers stop wiring the resolver

[`handlers/run.py:50, 1297`](../../src/runtime_worker/handlers/run.py) and [`handlers/approval.py:26, 423`](../../src/runtime_worker/handlers/approval.py) currently construct a `UsageAttributionResolver` and pass it to `StreamingExecutor.run`. Both call sites delete the construction. `RuntimeRunHandler.__init__` loses the `attribution` parameter and field. Audit-confirmed: no other callers.

### 4.7 Port surface cleanup

Adapter ports lose `query_last_completed_tool_connector_slug`:

- [`agent_runtime/api/ports.py:492-499`](../../src/agent_runtime/api/ports.py) — Protocol method removed.
- [`runtime_adapters/in_memory/runtime_api_store.py:1281`](../../src/runtime_adapters/in_memory/runtime_api_store.py) — implementation removed.
- [`runtime_adapters/postgres/runtime_api_store.py:2541-2567`](../../src/runtime_adapters/postgres/runtime_api_store.py) — implementation removed.

The in-memory store also keeps a list at `_tool_invocation_completions` that fed only this query — verify and delete if it has no other consumers.

---

## 5. Schema changes

Migration `0028_runtime_usage_attribution_columns.sql`:

```sql
ALTER TABLE runtime_model_call_usage
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'main',
    ADD COLUMN IF NOT EXISTS originating_tool_call_id TEXT,
    ADD COLUMN IF NOT EXISTS originating_tool_name TEXT;
```

- `purpose` defaults to `'main'` so pre-migration rows + any code path that doesn't construct a context (none in 01b — every emit builds one) get the safe bucket.
- `originating_tool_*` are nullable — only TOOL_INTERPRETATION / TOOL_PLANNING calls populate them; MAIN / SUBAGENT_WORK / CONTEXT_COMPRESSION leave them NULL.
- `subagent_id` and `connector_slug` columns already exist; 01b only changes how they're populated. No schema change for those.

Rollback drops the three columns. Old code reads the columns it knows.

Run-level `runtime_run_usage` does NOT get attribution columns. Run-level is one row per run with the aggregate — attribution dimensions live on per-call rows. Rollups (01d) project per-call rows into per-purpose / per-subagent rollup tables.

---

## 6. Files touched (inventory)

### Added

- `agent_runtime/observability/attribution.py` — `Purpose`, `UsageAttributionContext`, `Purpose.derive`.
- `tests/unit/agent_runtime/observability/test_attribution_context.py` — invariants + Purpose.derive precedence.
- `migrations/0028_runtime_usage_attribution_columns.sql` + rollback.

### Modified

- `agent_runtime/persistence/records/telemetry.py` — `RuntimeModelCallUsageRecord` gains `purpose: str = 'main'`, `originating_tool_call_id: str | None`, `originating_tool_name: str | None`.
- `runtime_adapters/postgres/runtime_api_store.py` — `record_model_call_usage` INSERT extends with new columns.
- `agent_runtime/api/ports.py` — drop `query_last_completed_tool_connector_slug` from the Protocol.
- `runtime_worker/tool_call_ledger.py` — add `connector_slug`, `consumed_for_attribution`, `mark_pending_attribution`, `pop_pending_attribution`.
- `runtime_worker/stream_tools.py` — call `ledger.mark_pending_attribution(call_id)` when `observed_settled(call_id)` fires (the TOOL_RESULT emission boundary at line 236-238).
- `runtime_worker/run_metrics.py` — `_PerCallSlot` adds new fields; `observe` accepts `UsageAttributionContext`; `model_call_usage_records` materializes new columns.
- `runtime_worker/streaming_executor.py` — delete `active_subagent_tasks` arbitration; delete resolver wiring; add `_build_attribution_context`; pass into `per_call.observe`.
- `runtime_worker/handlers/run.py` — drop `attribution` parameter + field + construction.
- `runtime_worker/handlers/approval.py` — same.
- `tests/unit/runtime_worker/test_per_call_usage.py` — `observe` calls accept context; new asserts on attribution stamping.

### Deleted

- `agent_runtime/observability/usage_attribution.py` — the resolver class + module.
- `tests/unit/agent_runtime/observability/test_usage_attribution.py` — its tests.
- `runtime_adapters/in_memory/runtime_api_store.py` — `query_last_completed_tool_connector_slug` method + supporting `_tool_invocation_completions` storage if unused elsewhere.
- `runtime_adapters/postgres/runtime_api_store.py` — same method.

---

## 7. Behaviors preserved

| Behavior                                                   | How                                                                                                                      |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Per-call row written once per AIMessage with usage         | Per-call accumulator semantics unchanged; emit boundary is the same.                                                     |
| Cumulative-chunk dedup (last-write-wins via merge)         | 01a's `NormalizedTokenUsage.merge` is the same path.                                                                     |
| `MODEL_CALL_COMPLETED` wire payload shape                  | Unchanged. New attribution fields are on the row, not the wire — 01d adds them to the FE-visible payload.                |
| `run_completed` run-level usage row                        | Unchanged. Attribution lives on per-call rows.                                                                           |
| `subagent_started` / `subagent_completed` lifecycle events | Untouched. Track-subagents flag still drives them.                                                                       |
| Parallel subagent task lifecycle (LangGraph state)         | Untouched. Only the attribution stamping moves off the buggy set arbitration onto deterministic chunk-namespace mapping. |
| Cost stamped at write time, integer micro-USD              | Unchanged. `CostCalculator` operates on the same columns.                                                                |
| Token-kind capture (reasoning, cache_creation, audio)      | Unchanged. 01a's surface untouched.                                                                                      |

---

## 8. Risks

| Risk                                                                                                                                                                                      | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Pydantic invariant on `Purpose.SUBAGENT_WORK ⇒ subagent_slug` raises in production due to a chunk where `subagent_id_for_subgraph` returns None                                           | Medium     | High   | The streaming executor falls back to `Purpose.MAIN` when `subagent_slug is None` (no subagent context resolvable). Pinned test for the "subgraph unlinked" case. Audit confirmed the resolver handles unlinked subgraphs by returning None today; the namespace path already tolerates it. |
| `pop_pending_attribution` returns an entry from a different agent step (e.g. tool fired, LLM emits, tool fires again, LLM emits — the second emit pops the first tool's entry by mistake) | Low        | Medium | `consumed_for_attribution` flag — once popped, the entry is marked and never returned again. The latest-by-settled_at semantics + scope filtering give the natural ReAct pairing. Pinned test for the multi-step ReAct case.                                                               |
| Deleting `query_last_completed_tool_connector_slug` breaks a hidden caller                                                                                                                | Low        | High   | Pre-deletion grep across `services/ai-backend/src` confirms only the resolver consumes it; no other callers exist. The Protocol method is removed in the same PR as the implementations.                                                                                                   |
| Test for parallel subagents requires harness work the existing suite doesn't have                                                                                                         | Medium     | Low    | Existing `test_streaming_executor_isolation.py` already drives multi-subagent chunks; the new test extends it with two distinct subagent task_ids and asserts the per-call row's `task_id` / `subagent_id` partition cleanly.                                                              |
| Adding `purpose` to the row breaks a SELECT \* read elsewhere that expects fixed column order                                                                                             | Low        | Low    | `runtime_adapters/postgres/runtime_api_store.py` reads via column name not position; in-memory store materializes via Pydantic `model_validate`. SELECT \* is fine.                                                                                                                        |
| `_PerCallSlot` `__slots__` doesn't match a test that pickles or dataclass-introspects the slot                                                                                            | Low        | Low    | Slot is a worker-internal helper; no pickling or serialization. New fields are additive — old fields keep their semantics.                                                                                                                                                                 |

---

## 9. Tests

### 9.1 New unit tests

`test_attribution_context.py`:

- `Purpose.derive` precedence: 5 cases covering compression > subagent > interpretation > planning > main; one case where multiple flags are set (compression wins).
- Construction with `purpose=SUBAGENT_WORK` + `subagent_slug=None` raises.
- Construction with `purpose=TOOL_INTERPRETATION` + `originating_tool_call_id=None` raises.
- Construction with `subagent_slug` set but `task_id=None` raises.
- Construction with `purpose=MAIN` and no optional fields succeeds.
- `frozen=True` — assignment raises.
- `extra="forbid"` — unknown field raises.

`test_tool_call_ledger.py` (extend existing):

- `mark_pending_attribution(call_id)` + `pop_pending_attribution(scope_key)` round-trip.
- Pop returns latest-by-settled_at.
- Pop consumes — second pop for same scope returns `None`.
- Two parallel scopes (subagent A, subagent B) don't cross-attribute.
- Pop for orchestrator scope (scope_key=None) ignores subagent entries.
- Pop with empty ledger returns `None` (no raise).

`test_parallel_subagents_attribution.py` (or extend `test_streaming_executor_isolation.py`):

- Drive a stream with two parallel subagents (each with its own `supervisor_task_call_id` and `ns`).
- Assert the per-call usage rows produced partition correctly: subagent A's chunks stamp `task_id=A_call_id`, `subagent_id=A_slug`; subagent B's chunks stamp `task_id=B_call_id`, `subagent_id=B_slug`.
- Sanity: no chunk's row carries the other subagent's identity.

### 9.2 Integration tests

Extend `test_runtime_worker.py` flows:

- A run with one tool call → next LLM call's row carries `purpose=tool_interpretation` + `originating_tool_call_id` + `originating_tool_name`.
- A run with no tools → row carries `purpose=main`.
- A run with a subagent task → subagent's LLM call rows carry `purpose=subagent_work` + `subagent_id=<slug>`.

### 9.3 Regression

All existing tests pass unchanged except the two that referenced the deleted resolver (deleted along with it).

---

## 10. Rollout / rollback

### 10.1 Rollout

One PR, direct cutover. No flags. Sequence:

1. Migration 0028 lands first (additive columns).
2. New `attribution.py` module + `_PerCallSlot` extension.
3. Ledger extension.
4. Streaming executor wiring + resolver deletion in same commit.
5. Handler call-site cleanup.
6. Tests.

### 10.2 Rollback

`git revert` the PR. Migration 0028 columns stay (additive); old code ignores them. No data lost. Resolver code returns to its old behavior (which was a no-op against an empty table). Attribution columns on rows written between the forward migration and the rollback retain their values; rollups treat them as informational only.

---

## 11. Done definition

- Migration 0028 landed.
- `agent_runtime/observability/usage_attribution.py` deleted.
- `tests/unit/agent_runtime/observability/test_usage_attribution.py` deleted.
- `query_last_completed_tool_connector_slug` removed from ports + both adapters.
- `_PerCallSlot` stamps the attribution context.
- Parallel-subagent test green.
- Pydantic invariant tests green.
- Full ai-backend suite green.
- This sub-PRD `Status: Shipped` and parent PRD §4 row ticked.
