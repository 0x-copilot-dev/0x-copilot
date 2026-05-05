# Decomp — `runtime_worker/stream_tools.py`

Source: [services/ai-backend/src/runtime_worker/stream_tools.py](../../../services/ai-backend/src/runtime_worker/stream_tools.py) — **564 LOC, L.** Two classes: `ToolCallStreamState` (dataclass) and `StreamMessageProcessor`. The **tool-call stream state machine** — owns the incremental name/id inference for provider chunks that arrive without complete metadata, plus the in-flight ledger that the run-level error path uses to settle orphans.

## A. Top-level structure

| Symbol                                                         |   Lines | Purpose                                                                                                                                                                                                  |
| -------------------------------------------------------------- | ------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ToolCallStreamState` (dataclass)                              |   25–41 | Per-tool-call accumulator: `namespace`, `key`, `tool_name`, `call_id`, `args_text`, `last_delta`, `args`, `summary`, `subagent_name`, `short_summary`, `started_emitted`, `pending_start`, `started_at`. |
| `StreamMessageProcessor.internal_tool_names`                   |   51–59 | Frozen set: `WRITE_TODOS`, `ASK_A_QUESTION` — surface-suppressed (visibility=internal).                                                                                                                  |
| `StreamMessageProcessor.large_result_artifact_tool_names`      |   60–67 | Frozen set: `READ_FILE`, `RG`, `GREP`, `SEARCH_FILES`.                                                                                                                                                   |
| `_Fields`                                                      |   69–70 | Single `INDEX = "index"` constant for chunk-keyed streams.                                                                                                                                               |
| `__init__(event_producer, update_processor)`                   |   72–86 | Init `_tool_call_states` (keyed by `(run_id, namespace.parts, key)`), `_tool_call_ids` (keyed by `(run_id, call_id)`), `_ledgers` (per-run).                                                             |
| `ledger_for_run(run_id)`                                       |   88–95 | Lazy create + return `ToolCallLedger`.                                                                                                                                                                   |
| `discard_ledger(run_id)`                                       |  97–100 | Free per-run ledger on terminal state.                                                                                                                                                                   |
| `process(*, run, namespace, message, delta)`                   | 102–197 | **Main entry**: emit tool_call chunks; handle tool_result and TOOL_CALL_COMPLETED; resolve subgraph→supervisor task_id.                                                                                  |
| `append_tool_call_chunk_event(...)`                            | 199–250 | Emit TOOL_CALL_STARTED on first ready chunk; subsequent chunks emit TOOL_CALL_DELTA.                                                                                                                     |
| `_append_task_tool_call_event(...)`                            | 252–278 | Special path for `task` tool — emits SUBAGENT_STARTED via update_processor.                                                                                                                              |
| classmethod `tool_call_payload(tool_call)`                     | 280–309 | Build payload from a single chunk (used by tests / non-stateful callers).                                                                                                                                |
| `tool_call_state(run_id, namespace, tool_call)`                | 311–356 | **State accumulation**: lookup by key; create or replace on call_id mismatch; append to `args_text` if delta-style, else replace `args` if dict-style.                                                   |
| `tool_call_state_key(run_id, namespace, payload, call_id)`     | 358–379 | Choose state key: `index:N` > `call:CID` > sole-existing-state > `__current__`.                                                                                                                          |
| classmethod `tool_call_payload_from_state(state)`              | 381–395 | Build payload from accumulated state (used in chunk-driven emit path).                                                                                                                                   |
| classmethod `tool_call_state_ready_to_emit(state)`             | 397–409 | True for non-artifact tools; for artifacts, requires args; for READ_FILE specifically, requires `file_path` or `path` in args.                                                                           |
| classmethod `parse_args_text(value)`                           | 411–419 | Tolerant `json.loads` → mapping or empty dict.                                                                                                                                                           |
| `_TOOL_MESSAGE_STATUS_MAP`                                     | 425–428 | LangChain `error`/`success` → `failed`/`completed`.                                                                                                                                                      |
| classmethod `tool_result_payload(message)`                     | 430–467 | Build TOOL_RESULT payload from LangChain `ToolMessage`; strip type/name/id keys from output; map status.                                                                                                 |
| `tool_result_payload_with_state(run_id, payload)`              | 469–486 | Backfill `tool_name` from state when payload says UNKNOWN_TOOL; mark internal visibility for large-result artifacts.                                                                                     |
| `tool_call_state_for_payload(run_id, payload)`                 | 488–496 | Look up state by `call_id`.                                                                                                                                                                              |
| `_tool_duration_ms(run_id, payload)`                           | 498–507 | `now() - state.started_at` in ms; clamps to 0.                                                                                                                                                           |
| classmethod `apply_tool_visibility(payload)`                   | 509–516 | Mark internal if internal-tool-name OR large-result-artifact.                                                                                                                                            |
| classmethod `mark_internal_visibility(payload)`                | 518–520 | Set `payload.visibility = INTERNAL`.                                                                                                                                                                     |
| classmethod `is_internal_tool_name(tool_name)`                 | 522–524 | Membership check.                                                                                                                                                                                        |
| classmethod `is_large_result_artifact_state(state)`            | 526–533 | Build a synthetic payload, defer to `is_large_result_artifact_payload`.                                                                                                                                  |
| classmethod `is_large_result_artifact_payload(payload)`        | 535–549 | True iff tool name is artifact AND args path starts with `LARGE_TOOL_RESULTS_PREFIX`.                                                                                                                    |
| classmethod `is_large_result_artifact_tool_name(tool_name)`    | 551–558 | Allowlist + substring `"search"`.                                                                                                                                                                        |
| classmethod `is_path_classified_artifact_tool_name(tool_name)` | 561–564 | Allowlist only (no substring fallback).                                                                                                                                                                  |

## B. Feature inventory

| Domain                                                       | Symbols                                                                                                                                                                                                        |  LOC |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---: |
| **Tool-call state machine (incremental name/id inference)**  | `ToolCallStreamState`, `tool_call_state`, `tool_call_state_key`, `tool_call_state_ready_to_emit`, `tool_call_payload_from_state`, `parse_args_text`, `_tool_duration_ms`, `tool_call_state_for_payload`        | ~150 |
| **Stream event emission (start / delta / completed)**        | `process`, `append_tool_call_chunk_event`                                                                                                                                                                      | ~145 |
| **Tool-result payload normalization**                        | `tool_result_payload`, `tool_result_payload_with_state`, `_TOOL_MESSAGE_STATUS_MAP`                                                                                                                            |  ~70 |
| **`task` tool special-casing → SUBAGENT_STARTED**            | `_append_task_tool_call_event`                                                                                                                                                                                 |  ~30 |
| **Visibility / internal-tool / large-result classification** | `apply_tool_visibility`, `mark_internal_visibility`, `is_internal_tool_name`, `is_large_result_artifact_*`, `is_path_classified_artifact_tool_name`, `internal_tool_names`, `large_result_artifact_tool_names` |  ~80 |
| **Ledger lifecycle**                                         | `ledger_for_run`, `discard_ledger`, integration in start + result paths                                                                                                                                        |  ~25 |

## C. Functional spec per domain

### Tool-call state machine

State key resolution (`tool_call_state_key`, 358–379): four-step ladder:

1. `index:{N}` if payload has `index` (provider sends incremental chunks keyed by position).
2. `call:{call_id}` if payload has call_id.
3. Sole-existing-state's key, if there's exactly one state for this `(run_id, namespace.parts)`.
4. `"__current__"` literal — single shared state for the namespace.

State refresh on `call_id` change (330–332): if a new call_id arrives that doesn't match the stored state's call_id, **replace the state entirely** rather than append. Defends against state pollution when the model fires multiple distinct tool calls under the same key.

Args accumulation (341–355):

- If args is a Mapping with `delta` key → append delta to `args_text`, set `last_delta`.
- If args is a non-empty Mapping (full JSON sent at once) → replace `args` whole, clear `last_delta`.
- If args is a string → treat as raw delta, append to `args_text`.

### Emission readiness (`tool_call_state_ready_to_emit`, 397–409)

Non-artifact tools: emit immediately (return True).
Artifact tools (READ_FILE / RG / GREP / SEARCH_FILES): require args.
READ_FILE specifically: also require `file_path` or `path`. Defends against emitting a "Reading file..." card with no path (cosmetic regression).

State has a `pending_start` flag (220, 230–231) so the first chunk that's NOT ready stages a start emission for when readiness arrives.

### Stream event sequence (per tool call)

```
chunk arrived (state accumulates)
  ↓ if state ready to emit
TOOL_CALL_STARTED    ← started_at stamped, ledger.started() called
  ↓ subsequent chunks
TOOL_CALL_DELTA × N  ← status=running, last_delta in payload
  ↓ tool_result arrives
TOOL_RESULT           ← output payload, mapped status, duration_ms
  ↓ ledger.observed_settled()
TOOL_CALL_COMPLETED   ← mirrors tool_result.status, duration_ms; visibility carried over
```

### Tool-result handling (`process`, 134–197)

For every tool-result message in the stream:

1. Build base payload (`tool_result_payload`).
2. Backfill from state (`tool_result_payload_with_state`).
3. **Special-case**: if tool_name == TASK, route to `update_processor.append_task_lifecycle_event(SUBAGENT_COMPLETED)` and return — the task tool's results are subagent completions, not normal tool results.
4. Compute `duration_ms` from state's `started_at`.
5. Emit TOOL_RESULT.
6. Mark ledger entry as settled.
7. Emit TOOL_CALL_COMPLETED with `status` mirrored from tool_result (failed/timed_out/completed). Visibility is propagated from tool_result's INTERNAL flag if present.

### Task tool — subagent lifecycle bridge

The `task` tool is a Deep Agents primitive that delegates to a subagent. This processor **suppresses** its TOOL_CALL_STARTED and TOOL_RESULT events and routes them as SUBAGENT_STARTED/COMPLETED via `update_processor`:

- `_append_task_tool_call_event` (252–278): emits SUBAGENT_STARTED, captures `subagent_name` and `short_summary` from the args. Idempotent on `started_emitted`.
- TOOL_RESULT path (137–153): emits SUBAGENT_COMPLETED via `task_tool_result_payload`, passing through `subagent_name` and `short_summary` from the saved state.

### Visibility classification

Two ways a tool call gets `visibility=internal`:

1. Tool name in `internal_tool_names` (`WRITE_TODOS`, `ASK_A_QUESTION`).
2. Tool name in `large_result_artifact_tool_names` AND args path starts with `LARGE_TOOL_RESULTS_PREFIX`.

Internal-visibility events still persist but the FE renders them under a debug-only filter — they don't show as activity cards.

The `ASK_A_QUESTION` exclusion comment at 54–57 is important: "ask_a_question surfaces its own approval_requested card via the native interrupt path; the tool_call_started/result events are noise and would render a duplicate 'ask_a_question running' tile."

## D. Bugs / edge cases / invariants

- **State replacement on call_id mismatch** (330–332): defends against stale args bleeding between tool calls.
- **`pending_start` flag** (220, 230–231): READ_FILE emits its `started` event only AFTER the path arrives, otherwise the card renders without a meaningful title.
- **`__current__` state key fallback** (379): when no index, no call_id, and ambiguous namespace state, falls back to a literal key. This means concurrent tool calls in the same namespace (rare for main-agent stream) can collide on state.
- **`task` tool result routing** (137–152): SUBAGENT_COMPLETED, not TOOL_RESULT — frontend renders subagent cards, not tool cards.
- **Tool-result status mapping** (425–428): LangChain `error` → `failed`. Without the map, an errored tool would be rendered as `completed` (silent regression).
- **Visibility carry-over on COMPLETED** (182–188): propagates INTERNAL from tool_result. Otherwise a hidden tool would emit a visible COMPLETED card.
- **Artifact-classification has substring fallback** (557): tool names containing `"search"` are also treated as artifact tools (e.g. third-party `confluence_search` would be flagged). `is_path_classified_artifact_tool_name` (561–564) does NOT include the substring fallback — used in `tool_call_state_ready_to_emit` to be more conservative.
- **Duration ms uses state's `started_at`, not message timestamp** (498–507): wall-clock duration as the worker observed it, not the LLM's claimed timing.
- **Ledger `started` requires both call_id AND tool_name** (243–249): missing-id calls don't enter the ledger and won't be settled on failure.
- **`tool_call_state_for_payload` is None-safe** (488–496): callers that pass results without call_ids get None back.
- **`apply_tool_visibility` runs in 3 places**: in `tool_call_payload`, `tool_call_payload_from_state`, `tool_result_payload`, `tool_result_payload_with_state`. Visibility is recomputed every emit — defensive.

## E. Hardcoded vs configurable

### Hardcoded

- Two visibility allowlists (51–59, 60–67).
- LangChain status map (425–428).
- `Values.Tool.UNKNOWN_TOOL` fallback for missing tool names.
- `TraceContext.event_id()` fallback for missing call_ids.
- `LARGE_TOOL_RESULTS_PREFIX` (548) — virtual path constant.
- "search" substring as artifact-tool flag (557).

### Configurable

- `event_producer`, `update_processor` injected.

## F. External dependencies and coupling

### Internal

- `agent_runtime.api.constants.Keys`, `Values`.
- `agent_runtime.api.events.RuntimeEventProducer`.
- `agent_runtime.execution.contracts.JsonObject`, `StreamEventSource`.
- `agent_runtime.observability.tracing.TraceContext` — for fallback event ids.
- `runtime_api.schemas.RunRecord`, `RuntimeApiEventType`, `RuntimeEventVisibility`.
- `runtime_worker.stream_messages.StreamMessageParser`, `StreamTextHelper`.
- `runtime_worker.stream_parts.StreamNamespace`.
- `runtime_worker.stream_subagents.StreamUpdateProcessor` — for task-tool routing.
- `runtime_worker.tool_call_ledger.ToolCallLedger`.

### Stdlib / third-party

- `json`, `dataclasses`, `datetime`, `collections.abc.Mapping`.

## G. Suggested decomposition seams

1. **`tool_call_state.py`** — `ToolCallStreamState`, `tool_call_state`, `tool_call_state_key`, `tool_call_state_ready_to_emit`, `tool_call_payload_from_state`, `parse_args_text`, `_tool_duration_ms`. ~165 LOC.
2. **`tool_result_normalizer.py`** — `tool_result_payload`, `tool_result_payload_with_state`, `_TOOL_MESSAGE_STATUS_MAP`. ~70 LOC.
3. **`tool_visibility.py`** — `apply_tool_visibility`, `mark_internal_visibility`, both `is_*` predicates, the two visibility allowlists. ~80 LOC. Pure classification.
4. **`task_tool_bridge.py`** — `_append_task_tool_call_event` + the SUBAGENT_COMPLETED routing in `process`. ~50 LOC.
5. **`stream_message_processor.py`** — slim orchestrator: `process` + `append_tool_call_chunk_event` + ledger management. ~150 LOC.

The **state-keyed dispatch** (1) is the hardest part to test in isolation because it shares state across calls; pulling it out gives a clean test surface.

The **`task` tool special case** (4) is structurally a different concern from regular tool calls — its events flow into the subagent lifecycle, not the tool lifecycle. Splitting it would make the SubAgent vs Tool boundary explicit.
