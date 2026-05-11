# Refactor PRD — Tool-call envelope + Subagent lifecycle closure

**Status:** Draft
**Author:** investigation 2026-05-11
**Tracks:** verification findings G + H ([refactor-audit](../architecture/refactor-audit.md) addendum)
**Prerequisites:** Logging fix shipped ([logging.py — `RuntimeLogger.exception_metadata`](../../src/agent_runtime/observability/logging.py)); the canonical exception helper exists, so all server-side failures now expose `exception_message` + `traceback` alongside `exception_type` in structured metadata.

---

## 1. Problem

A staff-engineer-grade investigation of the run that the user described as "Search web always failing / 3rd subagent stuck" revealed **two independent defects** sitting on top of each other. Each on its own would be a P1 bug; layered, they produced a symptom (stuck subagent + opaque "failed" cards) that pointed at the wrong subsystem.

### Defect A — `display_metadata` wrapper breaks every `BaseTool` that uses `InjectedToolCallId`

[`agent_runtime/capabilities/middleware/display_metadata.py`](../../src/agent_runtime/capabilities/middleware/display_metadata.py) wraps every model-facing tool to inject `display_title` / `display_summary` into its args schema (so the UI gets a card title before the tool runs). Two wrap branches exist:

| Branch                           | Used for                                                                                                | Mechanism                                                                                    | Health     |
| -------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ---------- |
| `_wrap_base_tool_via_func`       | `StructuredTool` subclasses (Deep Agents builtins, `task`, `write_file`, …)                             | Mutates `func` / `coroutine` in-place on the cloned tool                                     | Works      |
| `_wrap_base_tool_via_delegation` | All other `BaseTool` subclasses (`DuckDuckGoSearchResults`, every MCP tool, citation-capturing wrapper) | Builds a new `StructuredTool` whose coroutine calls `await tool.ainvoke(stripped_args_dict)` | **Broken** |

The delegation branch passes a plain args dict to `ainvoke()`. But the inner tool's schema declares `tool_call_id: Annotated[str, InjectedToolCallId] = ""` (added by [`citation_capturing_tool.py:311`](../../src/agent_runtime/capabilities/citation_capturing_tool.py#L311) and [`mcp/cards.py:223`](../../src/agent_runtime/capabilities/mcp/cards.py#L223) — the citation ledger needs the `tool_call_id` to attach sources to the right call). LangChain's `BaseTool.ainvoke()` then refuses with:

```
ValueError: When tool includes an InjectedToolCallId argument, tool must always
be invoked with a full model ToolCall of the form:
{'args': {...}, 'name': '...', 'type': 'tool_call', 'tool_call_id': '...'}
```

**Surface:**

- Every `web_search` call from the supervisor or a subagent fails before reaching DuckDuckGo.
- Every MCP tool call after auth fails for the same reason (this is why the post-auth Linear flow in the verification log emitted `run_failed` with empty `safe_error`).
- The failure is a true `ValueError` raised inside LangGraph's tool dispatch — it never hits the `RetryingTool` retry budget, so retries don't help.

**LSP violation.** The wrapper claims to be a transparent surface but breaks any tool whose schema declares an injected runtime parameter (`InjectedToolCallId`, and by extension any of LangChain's `Injected*` annotations). A wrapper that does not preserve its subject's invocation contract is a substitutability violation.

### Defect B — Subagent and fleet lifecycles are not closed on run failure

The same run produces this event sequence (verified from `/v1/agent/runs/{id}/events`):

| seq   | event                                           | task_id   | terminal?            |
| ----- | ----------------------------------------------- | --------- | -------------------- |
| 4     | `subagent_started`                              | gcd+prime | queued               |
| 5     | `subagent_started`                              | gcd-only  | queued               |
| 6     | `subagent_started`                              | research  | queued               |
| 7     | `subagent_fleet_started`                        | —         | —                    |
| 8     | `subagent_completed` (status=completed)         | gcd-only  | ✓                    |
| 9     | `subagent_completed` (status=completed)         | gcd+prime | ✓                    |
| 10–21 | 3× `tool_call_started` + `tool_result` (failed) | research  | —                    |
| 22    | `run_failed`                                    | —         | (run-level terminal) |

The research subagent has **no terminal subagent event.** `RuntimeApiEventType` does not even define `subagent_failed`. The fleet has **no `subagent_fleet_finished`** event either. The UI faithfully renders what's in the event log: the research card spins indefinitely under "1 live" because the only state transition it ever received was `subagent_started → queued`. This is an **event problem, not a UI problem**: the contract has no failure-terminal state, and the supervisor's run-level failure does not reconcile dangling subagents on the way down.

**Single-source-of-truth violation.** Subagent and fleet state is reconstructed by reading the event stream (per [persistence/ports.py `SubagentStorePort`](../../src/agent_runtime/persistence/ports.py) — "read-only projection of SUBAGENT\_\* events"). If the stream is missing terminal events, every projection is wrong. Any UI that derives status from events is now forced to invent its own "if run is terminal, treat dangling subagents as failed" rule — which means the inference logic spreads across all consumers (UI, audit exporter, workspace pane, future analytics).

---

## 2. Goals and non-goals

### Goals

- **A1.** `display_metadata` wrapper invokes its inner tool with a full `ToolCall` envelope so any tool declaring `InjectedToolCallId` (or any LangChain `Injected*` annotation) works through the wrapper exactly as it would unwrapped. Verified end-to-end against `web_search`, every MCP tool, and the citation-capturing wrapper.
- **A2.** The wrapper is the **single source of truth** for "how the runtime invokes a wrapped tool" — no duplicated dispatch logic across the two branches.
- **B1.** Every subagent that emits `subagent_started` reaches exactly one of `subagent_completed` (status ∈ {`completed`, `failed`}) or `subagent_paused` before the run terminates.
- **B2.** Every fleet that emits `subagent_fleet_started` reaches exactly one `subagent_fleet_finished` before the run terminates.
- **B3.** On any run-level terminal status (`completed`, `failed`, `cancelled`, `timed_out`, `run_rejected`), the runtime emits reconciling terminal events for any dangling subagents and the fleet — even if the underlying error path forgot to.
- **B4.** UI rendering rule becomes a one-liner: derive subagent status from the latest `subagent_*` event for that `task_id`. No "infer from run status" fallback.

### Non-goals

- Replacing `DuckDuckGoSearchResults` with a different search backend. DDG's intermittent rate-limiting is real but unrelated to either defect; that's a separate roadmap item (audit §3 — library replacements).
- Restructuring `display_metadata` more broadly (e.g. moving the wrap-on-bind to a graph-construction step). That belongs to a wider tool-pipeline cleanup; this PRD is the narrow fix that unblocks tool execution today.
- Adding a `subagent_failed` event type. Reusing `subagent_completed { status: "failed" }` preserves the existing enum surface and matches the f4 cancel pattern (`run_cancelled` with `status: cancelled`) — no new enum value, no client migration.
- Surfacing tool error messages into `tool_result.payload.output` so the model can recover (this is its own concern — see §6 Related work).

### Acceptance criteria

- **A.** Triggering `web_search` from the supervisor with a real query produces a successful `tool_result` whose `payload.output.content` is the actual DDG search result. No `ValueError` in the runtime guard, no `run_failed` from the tool path.
- **A.** Calling a Linear (or any) MCP tool after auth emits `TOOL_CALL → TOOL_RESULT` with `status=completed` (subject to upstream MCP-server availability; the wrapper itself does not refuse).
- **A.** The citation-capturing wrapper sees a non-empty `tool_call_id` for every tool invocation. Citation ledger entries continue to correctly attach to their originating `tool_call_id`.
- **B.** Reproducing the 3-subagent run with a forced web_search failure yields exactly one `subagent_completed { status: failed }` per dispatched subagent, and exactly one `subagent_fleet_finished` per fleet.
- **B.** Even if a `ValueError` escapes the worker between `subagent_started` and any other event, the run-terminal reconciliation path still emits a terminal subagent event for that task before `run_failed` is written.
- **B.** The frontend's "Agents — N live" badge counts only subagents whose latest event is not in the terminal set `{subagent_completed, subagent_paused}`. No "1 live" stuck cards after run terminates.

---

## 3. Part A — Tool-call envelope fix

### A.1 Recommended implementation (minimal, behavior-preserving)

Two changes inside [`display_metadata.py`](../../src/agent_runtime/capabilities/middleware/display_metadata.py):

#### A.1.1 Declare `InjectedToolCallId` on the wrapper's own schema

`wrap_args_schema(original_schema)` already produces a Pydantic model with the model-facing fields (`display_title`, `display_summary`, plus the inner tool's args). It must additionally declare:

```python
tool_call_id: Annotated[str, InjectedToolCallId] = ""
```

so LangChain injects the calling `tool_call_id` into the wrapper's coroutine on every dispatch. This is the same mechanism the inner tool relies on; the wrapper now plays the same game.

#### A.1.2 Pass a full `ToolCall` envelope to the inner

`_wrap_base_tool_via_delegation` becomes:

```python
async def _delegating_coroutine(
    *, tool_call_id: str = "", **kwargs: Any
) -> Any:
    real, _ = strip_display(kwargs)
    return await tool.ainvoke(
        {
            "args": real,
            "name": getattr(tool, "name", "tool"),
            "id": tool_call_id,
            "type": "tool_call",
        }
    )
```

The sibling branch `_wrap_base_tool_via_func` does **not** need to change: when the wrapper has `tool_call_id` in its schema, LangChain will inject it into the `_wrapped_func` / `_wrapped_coroutine` kwargs too; the inner `original_func` / `original_coroutine` receives the unwrapped kwargs (stripped of `display_*`) the same way it always has. The injection plumbing is identical — only the call site below it differs.

### A.2 Eliminating the two-branch divergence

The deeper LSP violation is that `_wrap_base_tool_via_func` and `_wrap_base_tool_via_delegation` should look interchangeable from the outside but dispatch differently inside. After A.1, both branches inject `tool_call_id` from the wrapper schema. We then have a single shared concern: "strip display fields and forward to the inner". Extract:

```python
async def _forward_to_inner_async(
    inner_callable: Callable[..., Awaitable[Any]] | None,
    inner_tool: BaseTool | None,
    *,
    tool_call_id: str,
    kwargs: dict[str, Any],
) -> Any:
    real, _ = strip_display(kwargs)
    if inner_callable is not None:
        return await inner_callable(**real)
    assert inner_tool is not None
    return await inner_tool.ainvoke(
        {"args": real, "name": inner_tool.name, "id": tool_call_id, "type": "tool_call"}
    )
```

Both branches then call this helper. DRY (one strip+forward), single source of truth (one place to fix if a future LangChain version changes the ToolCall shape).

### A.3 Test plan

- **Unit:** new `tests/unit/agent_runtime/capabilities/middleware/test_display_metadata_envelope.py` covering:
  - A wrapped `BaseTool` subclass whose schema declares `InjectedToolCallId` — `ainvoke({...full envelope...})` succeeds with the inner receiving the same `tool_call_id`.
  - A wrapped `StructuredTool` — same envelope behavior, no regression on existing strip/forward.
  - `display_title` / `display_summary` correctly stripped before reaching the inner regardless of branch.
- **Unit:** new test against [`citation_capturing_tool.py`](../../src/agent_runtime/capabilities/citation_capturing_tool.py) confirming `tool_call_id` is observed correctly for both wrapped and unwrapped tools.
- **Integration (in-memory worker):** `tests/integration/runtime_worker/test_web_search_end_to_end.py` — fires a one-shot run with a stubbed `WebSearchToolRegistry` whose tool returns a canned tuple; asserts the run reaches `run_completed` and `tool_result` carries the stubbed content.
- **Manual:** run the exact reproduction we used during the investigation (`Use web_search now for query langchain deepagents`). Expected: `tool_result { status: completed }` with non-empty `output.content`; no `run_failed`.

### A.4 Risk

| Risk                                                                            | Likelihood                                                                                                                          | Mitigation                                                                                                                          |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Existing tools whose schema doesn't accept `tool_call_id` reject the injection  | Low — LangChain's injection only fires for fields declared on the wrapper schema; inner tools receive the stripped kwargs unchanged | Test against a representative set: 2 StructuredTool builtins, 2 BaseTool subclasses, 1 MCP wrapper, 1 citation-capturing wrapper    |
| LangChain version upgrade changes `ToolCall` envelope shape                     | Medium long-term                                                                                                                    | Constants for the envelope keys (`Keys.TOOL_CALL_*`) so future bumps are one-file changes                                           |
| Citation ledger now sees a `tool_call_id` it didn't see before, double-counting | Low                                                                                                                                 | The ledger keys on `(tool_call_id,)`; an idempotent `insert_or_get` is already in place per [f5](../architecture/f5-citations.puml) |

---

## 4. Part B — Subagent and fleet lifecycle closure

### B.1 Recommended implementation

Three concrete changes in the worker:

#### B.1.1 Allow `status: "failed"` on `subagent_completed`

[`runtime_api/schemas/events.py`](../../src/runtime_api/schemas/events.py) — the `subagent_completed` payload schema must accept `status ∈ {"completed", "failed", "cancelled", "timed_out"}`. Today it implicitly only accepts `"completed"` because every existing emit site sends that value. No new event type added; the `RuntimeApiEventType` enum is unchanged.

The choice between adding `subagent_failed` and overloading `subagent_completed` was deliberate: `RUN_*` already uses one event per terminal-status flavour (`run_completed`, `run_failed`, `run_cancelled`, `run_rejected`), but `SUBAGENT_*` already uses `status` as the discriminator on `_started` / `_progress`. Following the local pattern beats global symmetry: clients already inspect `payload.status`, so this is a one-line projector update, not a contract migration.

#### B.1.2 Reconciliation hook on run termination

The single source of truth for "is a subagent still running" is the latest event for its `task_id`. The runtime must guarantee a terminal subagent event before the run-terminal event lands. Add a helper invoked from the run handler's final-state path (success and every failure mode):

```python
# runtime_worker/handlers/run.py (sketch)
async def _close_pending_subagents(
    *,
    run: RunRecord,
    event_producer: RuntimeEventProducer,
    terminal_reason: str,
) -> None:
    """Emit terminal subagent_completed + subagent_fleet_finished events for
    any task_ids that started but never reached a terminal state."""
    pending = await event_producer.list_pending_subagent_task_ids(run.run_id)
    for task_id in pending:
        await event_producer.append_api_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            task_id=task_id,
            status="failed",
            summary=f"Subagent did not complete: {terminal_reason}",
            payload={"task_id": task_id, "status": "failed",
                     "reason": terminal_reason},
        )
    if await event_producer.fleet_is_open(run.run_id):
        await event_producer.append_api_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_FLEET_FINISHED,
            payload={"status": "failed", "reason": terminal_reason},
        )
```

Called from:

- `RuntimeRunHandler.handle` finally-block — before `run_failed` / `run_completed` / `run_rejected` emits.
- `RuntimeCancelHandler.handle` — before `run_cancelled` emits.
- `RuntimeApprovalHandler.handle` (post-resume failure path) — before `run_failed` emits.

The lookup (`list_pending_subagent_task_ids`, `fleet_is_open`) uses the existing event store; no new schema. Both are simple projections over events with `task_id IS NOT NULL` filtered to those whose latest event is non-terminal.

#### B.1.3 Tool-error → subagent-failure propagation (the deeper cause)

The original orphaning happened because the supervisor's run guard caught a `ValueError` and emitted `run_failed` directly, with no chance for the in-flight subagent to learn its work was being abandoned. After Part A this specific path is gone, but the structural issue remains: when a subagent's `SubagentRunner` propagates an exception to the supervisor, the runner must emit `subagent_completed { status: failed }` from its own `finally` block before re-raising. This is the runner-side dual of B.1.2 — local closure when the runner has the most information, global reconciliation as a safety net.

[`agent_runtime/delegation/subagents/runner.py`](../../src/agent_runtime/delegation/subagents/runner.py) — every run-loop exit must emit a terminal event for the current task. Pattern:

```python
try:
    await self._drive_subagent(task, ...)
except Exception as exc:
    await self._emit_subagent_completed(
        task_id=task.task_id,
        status="failed",
        summary=str(exc)[:240],
        payload=RuntimeLogger.exception_metadata(exc),
    )
    raise
```

Note the reuse of [`RuntimeLogger.exception_metadata`](../../src/agent_runtime/observability/logging.py) shipped in the prior fix — single source of truth for exception capture; the same `exception_type` / `exception_message` / `traceback` triple that lands in the server log also lands in the event metadata, so audit + UI debugging share one view.

### B.2 Test plan

- **Unit:** `tests/unit/runtime_worker/handlers/test_run_handler_subagent_reconciliation.py` — RunHandler with a fake event store seeded with `subagent_started` but no terminal event for one task; assert `_close_pending_subagents` emits `subagent_completed { status: failed }` + `subagent_fleet_finished` before `run_failed`.
- **Unit:** `tests/unit/agent_runtime/delegation/subagents/test_runner_error_path.py` — runner that raises mid-stream; assert `subagent_completed { status: failed }` is appended in the `finally` before the exception propagates.
- **Integration:** `tests/integration/runtime_worker/test_three_subagent_with_failure.py` — three subagents dispatched, one configured to raise; expected: 3× `subagent_completed`, 1× `subagent_fleet_finished`, 1× `run_failed` (or `run_completed` if the supervisor can recover); no dangling subagents.
- **Schema regression:** `tests/unit/runtime_api/schemas/test_subagent_completed_status.py` — `subagent_completed { status: failed }` validates; `status` outside `{completed, failed, cancelled, timed_out}` is rejected.

### B.3 UI implications

- The Agents tab's "N live" counter should derive from `latestEvent.event_type ∉ {subagent_completed, subagent_paused}`. Already correct — no UI change required after B.1.1 + B.1.2 ship.
- The subagent card's status badge should map `subagent_completed { status: failed }` to a "Failed" badge. Likely already supported by the existing presentation layer (`activity_kind: subagent`, `status: failed`); confirm in frontend before merging.

### B.4 Risk

| Risk                                                                                                      | Likelihood                                                                                                                  | Mitigation                                                                                                                                                                       |
| --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Existing UI / projections fail open on `subagent_completed { status: failed }` (treating it as "success") | Medium — silent UX bug                                                                                                      | Pre-merge: grep frontend for `event_type === 'subagent_completed'` and confirm status is read. Add a typed `SubagentCompletedStatus` enum on the schema, not a free-form string. |
| Reconciliation double-emits a terminal event (e.g. runner B.1.3 emits, then handler B.1.2 also emits)     | Low                                                                                                                         | `fleet_is_open` / `list_pending_subagent_task_ids` projections check actual latest event before re-emitting. Idempotent by construction.                                         |
| `_close_pending_subagents` adds latency to the terminal path                                              | Low — both queries are indexed by `(run_id, task_id)` and bounded by subagent count per run (~10 max in any realistic flow) | None needed.                                                                                                                                                                     |

---

## 5. Rollback plan

- **Part A:** Revert the `display_metadata.py` change. The wrapper returns to its pre-fix behavior. Tools that don't use `InjectedToolCallId` (StructuredTool builtins) are unaffected.
- **Part B:**
  - B.1.1 (schema widening on `subagent_completed.status`): purely additive; nothing to roll back beyond reverting the schema migration.
  - B.1.2 (reconciliation hook): wrap in a deployment toggle (`SUBAGENT_RECONCILE_ON_TERMINAL = true` by default) so it can be disabled at runtime without a code rollback.
  - B.1.3 (runner finally-block): pure behavior addition; revert if a UI regression appears.

Each part is independently shippable. Recommended order:

1. **Part A** first (unblocks tool execution; without it, Part B's failure path is the dominant codepath).
2. **Part B.1.1** (schema widening) — must precede B.1.2 / B.1.3 emits.
3. **Part B.1.3** (runner finally) — local fix, narrowest blast radius.
4. **Part B.1.2** (reconciliation hook) — global backstop.

---

## 6. Related work (not in scope here)

- **Tool error → model recovery.** Today a failed `tool_result` carries `error_message: "The tool reported an error and didn't return a result."` — generic and uninformative. Surfacing the underlying exception (truncated, scrubbed) into `payload.output.content` lets the model retry intelligently and lets the user see what actually broke. Separate PRD candidate: `tool-error-surface.md`. Reuses the same `RuntimeLogger.exception_metadata` shape.
- **Cancellation cooperation** (verification finding f4). Cancel at t+0.5s currently lets the run finish to natural completion. Different subsystem, separate PRD.
- **Replace DuckDuckGo with a proper search API.** Tracked in [refactor-audit §3 — library replacements](../architecture/refactor-audit.md#3-library-replacements). Independent of this PRD; doing both does not change either's scope.

---

## 7. Unit testing requirements (summary)

| Test                                                                          | Type        | Asserts                                                                      |
| ----------------------------------------------------------------------------- | ----------- | ---------------------------------------------------------------------------- |
| `test_display_metadata_envelope.py::test_basetool_with_injected_tool_call_id` | unit        | Full ToolCall envelope reaches inner tool; inner sees correct `tool_call_id` |
| `test_display_metadata_envelope.py::test_structuredtool_strip_unchanged`      | unit        | Existing StructuredTool wrap path doesn't regress                            |
| `test_display_metadata_envelope.py::test_display_fields_stripped`             | unit        | `display_title` / `display_summary` never reach inner                        |
| `test_web_search_end_to_end.py::test_supervisor_invokes_web_search`           | integration | `tool_result { status: completed }`, no `run_failed`                         |
| `test_run_handler_subagent_reconciliation.py::test_dangling_subagent_closed`  | unit        | Reconciliation emits terminal events before `run_failed`                     |
| `test_runner_error_path.py::test_runner_emits_failed_completion`              | unit        | Runner emits `subagent_completed { status: failed }` on exception            |
| `test_three_subagent_with_failure.py::test_full_fleet_terminal_invariant`     | integration | All subagents reach exactly one terminal event; fleet closes                 |
| `test_subagent_completed_status.py::test_status_enum_validation`              | schema      | `failed` accepted; junk rejected                                             |

---

## 8. Spec updates

- [`docs/architecture/f5-citations.puml`](../architecture/f5-citations.puml) and the data-flow doc must note that the `display_metadata` wrapper passes a `ToolCall` envelope to inner tools (so `tool_call_id` survives the wrap).
- Subagent lifecycle doc (where one exists) must enumerate the terminal-event invariant: every `subagent_started` is followed by exactly one of `subagent_completed` (any status) or `subagent_paused` before the run terminates.
- `RuntimeApiEventType` table in [`docs/architecture/index.md`](../architecture/index.md) (or its successor): annotate `subagent_completed.status` as a discriminated enum.
- Add the verification scenario to [`docs/refactor/00-roadmap.md`](00-roadmap.md) as items P23 (tool-envelope) and P24 (subagent-lifecycle), both in Phase 1 (performance/correctness wins before structural changes).
