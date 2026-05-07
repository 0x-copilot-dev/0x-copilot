# PR 3.2.6 — Subagent paused / resumed event variants (wire + reducer)

> **Status:** Partially landed · v1
> **Plan reference:** Phase 3 of the subagent runtime correctness train. Phase 1 ([`pr-3.2.5-subagent-call-id-propagation.md`](./pr-3.2.5-subagent-call-id-propagation.md)) made `parent_task_id` deterministic; Phase 2 (executor restructure inside [`streaming_executor.py:175-219`](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L175-L219)) stopped early-returning on the first interrupt so siblings keep streaming. **This PR finishes the wire contract** so the FE can mark a single fleet row "paused" without inferring it from the absence of `SUBAGENT_COMPLETED`.
> **Owner:** ai-backend (worker `subagent_resumed` emit + tests) · packages/api-types (already landed) · apps/frontend (reducer state + projection — minimal; no visual rendering — that's Phase 4 / [`pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md`](./pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md)).
> **Size:** **S.** ≈ 80 LoC backend + 40 LoC FE reducer + tests. No migration. No new deps.
> **Depends on:** ✅ Phase 1 (deterministic `parent_task_id`) — without it, the worker can't tell whether an interrupt fired _inside_ a subagent vs. on the supervisor itself. ✅ Phase 2 (interrupt drain) — without it, the supervisor's stream halts before the paused subagent's siblings can keep emitting events.
> **Reads alongside:**
>
> - [`pr-3.2.5-subagent-call-id-propagation.md`](./pr-3.2.5-subagent-call-id-propagation.md) — the linkage that makes `parent_task_id` reliable.
> - [`pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md`](./pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md) — the FE Phase 4 that consumes the new state.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — streaming-event invariants.
> - [`packages/api-types/CLAUDE.md`](../../packages/api-types/CLAUDE.md) — contract change discipline.

---

## 0 · TL;DR

After Phase 1+2 landed, the FE _can_ correlate every inner tool call to its parent subagent, and the worker drains sibling streams while one subagent waits on approval/auth. What it still **can't** do reliably is render that paused state in the UI: the only signal the FE has is "saw `SUBAGENT_STARTED`, didn't see `SUBAGENT_COMPLETED`, but the run is `WAITING_FOR_APPROVAL`" — which is also true for siblings that are healthily mid-work. Inferring "paused" from absence is brittle and forces every FE component that cares about subagent state to redo the same negative-space derivation.

**Fix:** explicit `subagent_paused` and `subagent_resumed` events on the wire, scoped per-subagent via `task_id == parent_task_id == supervisor_call_id`. The FE reducer flips one entry's `status` to `paused` and the rest of the UI reads that state directly.

Current state of the work:

- ✅ `RuntimeApiEventType.SUBAGENT_PAUSED` / `SUBAGENT_RESUMED` exist in [`runtime_api/schemas/common.py:119-120`](../../services/ai-backend/src/runtime_api/schemas/common.py#L119-L120).
- ✅ TypeScript payloads `SubagentPausedPayload` / `SubagentResumedPayload` exist in [`packages/api-types/src/index.ts:1514-1539`](../../packages/api-types/src/index.ts#L1514-L1539).
- ✅ Worker emits `subagent_paused` from [`stream_events.py:378-406`](../../services/ai-backend/src/runtime_worker/stream_events.py#L378-L406) for `APPROVAL_REQUESTED` / `MCP_AUTH_REQUIRED` interrupts that resolved a `parent_task_id`.
- ❌ Worker does NOT emit `subagent_resumed` from the approval handler ([`runtime_worker/handlers/approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py)).
- ❌ Worker does NOT emit `subagent_paused` for native `ASK_A_QUESTION` interrupts (only the two `_SUBAGENT_INTERRUPT_REASONS` keys are handled).
- ❌ FE `subagentReducer.applySubagentEvent` ([`apps/frontend/src/features/chat/chatModel/subagentReducer.ts:36-82`](../../apps/frontend/src/features/chat/chatModel/subagentReducer.ts#L36-L82)) routes only `SUBAGENT_STARTED` / `_PROGRESS` / `_COMPLETED` — paused / resumed fall through.
- ❌ `SubagentLifecycleStatus` ([`packages/api-types/src/index.ts:1735-1741`](../../packages/api-types/src/index.ts#L1735-L1741)) has no `paused` value, so even if the reducer routed the event there's no state to set.

**Scope of this PR:** finish the five ❌ items above. No visual rendering — that's [`pr-3.2.7-...`](./pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md). When this PR ships, every reader of `SubagentEntry.status` automatically distinguishes paused subagents from running ones.

---

## 1 · PRD

### 1.1 Problem

Today's UI cannot reliably tell which fleet member is paused:

1. **`WAITING_FOR_APPROVAL` is a run-level flag, not a subagent-level one.** The supervisor halts the whole run when _any_ subagent (or itself) interrupts. From the FE's vantage point, "the run is waiting for approval and three subagents are running" doesn't say _which_ of the three is the one waiting.
2. **The FE has been inferring paused state from `SUBAGENT_STARTED + no SUBAGENT_COMPLETED + run.status === WAITING_FOR_APPROVAL`.** False positives: a sibling that's still healthily streaming `SUBAGENT_PROGRESS` events lights up "paused" the moment the run flips to `WAITING_FOR_APPROVAL`. Confusing UX.
3. **Resume is invisible.** When the user approves the gated tool, the worker resumes the paused subagent via the LangGraph checkpoint. There's no event on the wire that says "this specific subagent is running again" — just whatever events fall out of the resumed stream. Until the next `SUBAGENT_PROGRESS`, the FE doesn't know to flip back from paused → running.

`parent_task_id` is now reliable (Phase 1) and siblings keep streaming during interrupts (Phase 2), so the worker has the information needed to emit per-subagent pause/resume signals. We just have to actually emit them and have the FE consume them.

### 1.2 Goals

1. **Explicit pause and resume on the wire.** A subagent that hits an interrupt emits exactly one `SUBAGENT_PAUSED` event tagged with its own `task_id`. The matching resume — tagged with the same `task_id` — fires before the next downstream activity event from that subagent.
2. **Cover all three runtime interrupt kinds.** `APPROVAL_REQUESTED` and `MCP_AUTH_REQUIRED` (already wired) plus `ASK_A_QUESTION` (native interrupt path), so the FE never sees a paused subagent that didn't emit a paused event.
3. **Symmetric resume.** Whichever resolution path fires (approval decision, MCP-auth completion, ask_a_question answer), the worker emits exactly one `SUBAGENT_RESUMED` for the matching `task_id` before any further activity from that subagent. Idempotent: replays don't double-emit.
4. **Reducer state, not visual state.** Add `paused` to `SubagentLifecycleStatus` and project the new events in `subagentReducer`. The fleet row / pane card visual treatment is Phase 4 — this PR only ensures the data is correct.
5. **No assumption that a subagent stays paused if its run is cancelled.** If the run is cancelled while a subagent is paused, the cancel cascade transitions the entry to `cancelled` (existing behavior). No `SUBAGENT_RESUMED` is emitted — the subagent never ran again. Reducer handles cancelled-from-paused without special casing.
6. **No changes to the run-level state machine.** `WAITING_FOR_APPROVAL` still represents "the run is waiting on a human"; the new events are sub-run granularity. The handlers in [`runtime_api/handlers/run.py`](../../services/ai-backend/src/runtime_api/handlers/run.py) and the approval handler don't change their transitions.
7. **Auditability.** Pause and resume events persist with the same `runtime_events` shape as everything else — `parent_task_id`, `sequence_no`, projection columns. Compliance review needs to be able to answer "who paused this subagent and when".

### 1.3 Non-goals

- ❌ **FE visual treatment of paused state** — that's Phase 4 / [`pr-3.2.7-...`](./pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md). This PR's FE diff is reducer + type only, no component changes.
- ❌ **Backfill of historical runs.** Pre-PR runs replay without paused/resumed events. The reducer handles their absence (status stays `running` until the eventual `SUBAGENT_COMPLETED`); the projection for those archived runs is the same as today.
- ❌ **New approval/auth surface or kinds.** `reason` is constrained to the union already supported by the worker (`approval | mcp_auth | ask_a_question`). New interrupt kinds extend the union as part of the PR that introduces them.
- ❌ **Pausing supervisor-owned interrupts.** When the supervisor itself (not a child subagent) hits an interrupt, `parent_task_id` is None. `_maybe_emit_subagent_paused` already early-returns for that case. Run-level pause is what the existing `WAITING_FOR_APPROVAL` flag is for.
- ❌ **Cancellation during pause.** Existing `runtime_worker/handlers/cancel.py` cascade still owns the terminal-state transition. We don't emit a synthetic resume to "release" the paused state before cancelling — that would race the cancel handler. The reducer's projection of `SUBAGENT_COMPLETED` with `status=cancelled` overrides any prior `paused`.
- ❌ **A separate Approvals-tab badge for paused subagents.** Tracked in Phase 4. Until then, the existing run-level approval card surfaces the gate.
- ❌ **Per-subagent retention/legal hold differentiation.** Same retention rules as other `runtime_events` rows — owned by the persistence spec, not this PR.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                    | Verified by                                                                                                                                |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| AC-1  | When `APPROVAL_REQUESTED` fires inside a subagent (resolved `parent_task_id` is non-None), the worker emits exactly one `SUBAGENT_PAUSED` with `task_id == parent_task_id`, `reason == "approval"`, and `source_event_id == <approval_event.event_id>`.                                      | Existing path; covered by new test `test_subagent_paused_emitted_on_approval_inside_subagent`.                                             |
| AC-2  | Same for `MCP_AUTH_REQUIRED` (`reason == "mcp_auth"`).                                                                                                                                                                                                                                       | New test `test_subagent_paused_emitted_on_mcp_auth_inside_subagent`.                                                                       |
| AC-3  | Same for `ASK_A_QUESTION` native interrupt (`reason == "ask_a_question"`).                                                                                                                                                                                                                   | New test `test_subagent_paused_emitted_on_ask_a_question_inside_subagent`.                                                                 |
| AC-4  | When the gated interrupt resolves (approval decided, MCP token populated, ask_a_question answered) and the worker resumes via `astream_runtime_resume`, exactly one `SUBAGENT_RESUMED` is emitted with the matching `task_id` **before** the first activity event from the resumed subagent. | New test `test_subagent_resumed_emitted_before_first_activity_event_from_resumed_subagent`.                                                |
| AC-5  | Idempotency: re-replay of the same approval resolution does not re-emit `SUBAGENT_RESUMED`. The worker tracks "last-emitted-resume-for-task" per run and skips duplicates.                                                                                                                   | New test `test_subagent_resumed_idempotent_on_replay`.                                                                                     |
| AC-6  | A run-level (supervisor-owned) interrupt (where `parent_task_id is None`) does NOT emit `SUBAGENT_PAUSED` or `SUBAGENT_RESUMED`. Existing run-level approval flow is unchanged.                                                                                                              | New test `test_supervisor_level_interrupt_does_not_emit_subagent_paused`; existing approval-handler tests pass.                            |
| AC-7  | A subagent that is paused and then the run is cancelled: cancel cascade emits `SUBAGENT_COMPLETED(status=cancelled)`. The reducer's terminal projection wins; final `SubagentEntry.status === "cancelled"`. No synthetic resume emitted.                                                     | New reducer test `test_paused_then_cancelled_lands_in_cancelled`.                                                                          |
| AC-8  | `SubagentLifecycleStatus` gains the `"paused"` literal (additive, non-breaking per [`packages/api-types/CLAUDE.md`](../../packages/api-types/CLAUDE.md) — _"new enum values **on a payload the server already tolerates**"_; here, the server is the source emitting the literal).           | `npm run typecheck --workspace @enterprise-search/api-types`.                                                                              |
| AC-9  | `subagentReducer.applySubagentEvent` projects `SUBAGENT_PAUSED` → `status = "paused"` (preserving display_title / objective_summary / started_at). Returns same map reference if already paused.                                                                                             | New reducer test `test_subagent_paused_event_flips_status_to_paused`.                                                                      |
| AC-10 | `subagentReducer.applySubagentEvent` projects `SUBAGENT_RESUMED` → `status = "running"`. Returns same map reference if already running.                                                                                                                                                      | New reducer test `test_subagent_resumed_event_flips_status_back_to_running`.                                                               |
| AC-11 | `isRunningStatus("paused") === false`. Existing fleet-row "is anything running" checks correctly classify paused as not running.                                                                                                                                                             | New reducer test `test_paused_is_not_a_running_state`.                                                                                     |
| AC-12 | `SUBAGENT_PAUSED` / `_RESUMED` events persist into `runtime_events` with the same retention / `parent_task_id` indexing as other subagent events. Replay of a run via `GET /v1/agent/runs/{run_id}/events` includes them.                                                                    | Manual canary trace in §5.                                                                                                                 |
| AC-13 | The full ai-backend test suite passes; the frontend test suite passes.                                                                                                                                                                                                                       | `pytest tests/unit/runtime_worker/ tests/unit/agent_runtime/ tests/unit/runtime_api/`; `npm test --workspace @enterprise-search/frontend`. |

### 1.5 Risks

| Risk                                                                                                                                                                                                                                              | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`SUBAGENT_RESUMED` emitted before LangGraph resume actually streams events from the paused subagent** — race where the FE flips to running but no further events arrive (e.g. resume immediately interrupts again on a chained tool call).      | Acceptable: a chained pause emits another `SUBAGENT_PAUSED`, flipping the row back. The interim "running" state is honest — the subagent IS running; it just hits another gate fast. Tests cover the chained case (`test_resume_then_immediate_pause_emits_both_events`).                                                                                                                                                                                                                   |
| **Idempotency on replay.** Approval handler can be invoked twice for the same approval (e.g. retry after transient error). Without dedup, two `SUBAGENT_RESUMED` events emit.                                                                     | Track `(run_id, task_id) → last_resume_emitted_at` on the approval handler's per-call state and skip duplicates within the same handler invocation. Cross-invocation dedup is via the existing approval-status idempotency in [`runtime_api/handlers/approval`](../../services/ai-backend/src/runtime_api/handlers/approval.py): the second invocation no-ops before reaching the resume code path.                                                                                         |
| **Adding `"paused"` to `SubagentLifecycleStatus` could surface in `GET .../subagents` archive responses without the schema column to back it.**                                                                                                   | The archive endpoint reads the latest projected status from `runtime_async_tasks`; we don't write `paused` there (terminal-only). Live SSE payloads carry the new status; archive replay does not because the underlying task row never persisted "paused" as a checkpoint state. Document explicitly in the contract: `SubagentEntry.status` is `paused` only on live streams; archived entries always show the last-terminal-or-running status.                                           |
| **A subagent's interrupt pauses the supervisor's stream long enough that LangGraph emits a `SUBAGENT_COMPLETED` for the same task before the resume path fires.**                                                                                 | Phase 2's restructure made the interrupt drain non-blocking: the supervisor stream keeps pulling chunks until the LangGraph subgraph itself yields (which it doesn't until resumed). The paused subagent's `SUBAGENT_COMPLETED` literally cannot be emitted before resume. Defensive: if the worker observes `SUBAGENT_COMPLETED` for a `task_id` currently tracked as paused, emit an implicit `SUBAGENT_RESUMED` first so the reducer doesn't skip the running→paused→completed sequence. |
| **FE migrations: an old client connected during a server upgrade receives `SUBAGENT_PAUSED` it doesn't understand.**                                                                                                                              | Reducer's switch falls through `default: return undefined` — unknown event types are no-ops. Old clients render the same "running" state until the eventual `SUBAGENT_COMPLETED`. No crash, just slightly stale state.                                                                                                                                                                                                                                                                      |
| **Cancellation during pause raises whether to also emit a synthetic resume.**                                                                                                                                                                     | Decision documented in §1.3 / AC-7: no synthetic resume. Cancel cascade owns terminal transition. Reducer's `paused` → `cancelled` projection is direct (no intermediate running state).                                                                                                                                                                                                                                                                                                    |
| **`ASK_A_QUESTION` interrupts go through the native LangGraph interrupt path, not the explicit `APPROVAL_REQUESTED` path.** Adding it to `_SUBAGENT_INTERRUPT_REASONS` requires routing through the same `_maybe_emit_subagent_paused` call site. | Extend `append_activity_events` to call `_maybe_emit_subagent_paused` after native-interrupt persistence. Resolution path (`parent_task_id`) is already wired from Phase 1. Test: `test_subagent_paused_emitted_on_ask_a_question_inside_subagent`.                                                                                                                                                                                                                                         |

### 1.6 Unit testing

Per [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md):

**New tests** in `tests/unit/runtime_worker/test_stream_events.py`:

- `test_subagent_paused_emitted_on_approval_inside_subagent` — explicit-API approval flow with non-None `parent_task_id`. Asserts one `SUBAGENT_PAUSED` event with `reason="approval"`, `task_id == parent_task_id`, `source_event_id == <approval.event_id>`. (Already implemented for explicit approvals; this test pins it.)
- `test_subagent_paused_emitted_on_mcp_auth_inside_subagent` — same shape, `reason="mcp_auth"`.
- `test_subagent_paused_emitted_on_ask_a_question_inside_subagent` — native-interrupt path. New routing in `append_activity_events`.
- `test_supervisor_level_interrupt_does_not_emit_subagent_paused` — `parent_task_id=None` short-circuits. No paused event.

**New tests** in `tests/unit/runtime_worker/test_handlers_approval.py` (or wherever the approval handler is tested):

- `test_subagent_resumed_emitted_before_first_activity_event_from_resumed_subagent` — driver feeds an approval-decided event then a resume stream. Asserts `SUBAGENT_RESUMED` precedes any tool / progress / completion event from that `task_id`.
- `test_subagent_resumed_idempotent_on_replay` — invoke the approval handler twice. Second invocation does not re-emit.
- `test_resume_then_immediate_pause_emits_both_events` — resumed subagent immediately hits a second approval. Sequence: `SUBAGENT_RESUMED(task=A)` → `APPROVAL_REQUESTED(parent=A)` → `SUBAGENT_PAUSED(task=A, reason=approval)`.
- `test_subagent_completed_in_paused_state_emits_implicit_resume` — defensive: if for any reason `SUBAGENT_COMPLETED` arrives while task is still tracked paused, emit `SUBAGENT_RESUMED` first so reducer ordering is correct.

**New FE tests** in `apps/frontend/src/features/chat/chatModel/subagentReducer.test.ts`:

- `test_subagent_paused_event_flips_status_to_paused` — seeded `running` entry + `SUBAGENT_PAUSED` event → `status === "paused"`. Other fields (display_title, objective_summary, started_at) preserved.
- `test_subagent_resumed_event_flips_status_back_to_running` — seeded `paused` entry + `SUBAGENT_RESUMED` event → `status === "running"`.
- `test_paused_is_not_a_running_state` — `isRunningStatus("paused") === false`.
- `test_paused_then_cancelled_lands_in_cancelled` — `paused` + `SUBAGENT_COMPLETED(status=cancelled)` → `status === "cancelled"`. Terminal wins.
- `test_unknown_status_in_paused_payload_is_no_op` — payload with garbage `reason` doesn't crash; entry unchanged.
- `test_paused_event_for_unknown_task_id_seeds_a_minimal_entry` — replay arrives mid-conversation; entry didn't exist; reducer seeds + flips status.

**No design-system or visual tests added.** Phase 4 owns the visual surface.

---

## 2 · Spec

### 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BEFORE — paused state inferred from absence                                  │
│                                                                              │
│  worker emits APPROVAL_REQUESTED(parent_task_id=call_A)                      │
│  worker emits SUBAGENT_PAUSED(task_id=call_A) ← already wired                │
│  user approves                                                               │
│  worker resumes via astream_runtime_resume                                   │
│  resumed subagent emits SUBAGENT_PROGRESS / TOOL_CALL_STARTED / ...          │
│  ↑ FE has no signal that THIS subagent is running again until first event   │
│  ↑ FE infers "still paused" from missing SUBAGENT_COMPLETED + run.WAITING…  │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ AFTER — explicit paused / resumed bookends                                  │
│                                                                              │
│  worker emits APPROVAL_REQUESTED(parent_task_id=call_A)                      │
│  worker emits SUBAGENT_PAUSED(task_id=call_A, reason=approval, source=…)     │
│  user approves                                                               │
│  approval handler: just before astream_runtime_resume(...) yields chunks,    │
│    emit SUBAGENT_RESUMED(task_id=call_A, source_event_id=<decision.event_id>)│
│  resumed subagent emits SUBAGENT_PROGRESS / TOOL_CALL_STARTED / ...          │
│  FE reducer: paused → running on RESUMED, then normal projection.            │
│                                                                              │
│  Native ASK_A_QUESTION path:                                                 │
│  append_activity_events sees native_interrupt with parent_task_id=call_B     │
│  → emits SUBAGENT_PAUSED(task_id=call_B, reason=ask_a_question, source=…)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                                                     | Module                                                                                                                                                                | Owns                                                                             |
| ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `services/ai-backend/src/runtime_worker/stream_events.py`                                 | **EXTEND.** Extend `_SUBAGENT_INTERRUPT_REASONS` to cover `ASK_A_QUESTION`. Route native-interrupt persistence through `_maybe_emit_subagent_paused`.                 | Pause emission at every interrupt entry point. One source of truth for `reason`. |
| `services/ai-backend/src/runtime_worker/handlers/approval.py`                             | **EXTEND.** Before the resume stream is iterated, emit `SUBAGENT_RESUMED` for every paused `task_id` whose interrupt was just resolved. Track per-handler-call dedup. | Resume emission. Knows the resolution `event_id`.                                |
| `services/ai-backend/src/runtime_api/schemas/common.py`                                   | **NO CHANGE.** Enum already has `SUBAGENT_PAUSED` / `SUBAGENT_RESUMED`.                                                                                               | n/a                                                                              |
| `packages/api-types/src/index.ts`                                                         | **EXTEND** (small): add `"paused"` to `SubagentLifecycleStatus`. Payload types `SubagentPausedPayload` / `SubagentResumedPayload` already present.                    | Type contract.                                                                   |
| `apps/frontend/src/features/chat/chatModel/subagentReducer.ts`                            | **EXTEND.** Project `SUBAGENT_PAUSED` → `status = "paused"`; `SUBAGENT_RESUMED` → `status = "running"`. Update `RUNNING_STATES` (paused not included).                | State projection. `isRunningStatus` consumers automatically reflect the change.  |
| `apps/frontend/src/features/chat/chatModel/subagentReducer.test.ts`                       | **EXTEND.** New cases per §1.6.                                                                                                                                       | Reducer regressions.                                                             |
| `services/ai-backend/tests/unit/runtime_worker/test_stream_events.py`                     | **EXTEND.** New cases per §1.6.                                                                                                                                       | Worker emit regressions.                                                         |
| `services/ai-backend/tests/unit/runtime_worker/test_handlers_approval.py` (or equivalent) | **EXTEND.** Resume-side cases per §1.6.                                                                                                                               | Approval handler regressions.                                                    |

**Not changed:** the `runtime_events` schema (no new column), any migration, the run-level `WAITING_FOR_APPROVAL` transition, the cancel cascade, the SSE envelope shape (`RuntimeEventEnvelope` is the same; new variants reuse the existing `payload` discriminated union), the workspace-pane archive endpoint, the FE component layer.

### 2.3 The pause emission contract

```python
# services/ai-backend/src/runtime_worker/stream_events.py (excerpt)

_SUBAGENT_INTERRUPT_REASONS: dict[RuntimeApiEventType, Literal["approval", "mcp_auth", "ask_a_question"]] = {
    RuntimeApiEventType.APPROVAL_REQUESTED: "approval",
    RuntimeApiEventType.MCP_AUTH_REQUIRED: "mcp_auth",
    RuntimeApiEventType.ASK_A_QUESTION: "ask_a_question",  # NEW
}

# Existing _maybe_emit_subagent_paused stays as-is. The diff is:
# 1. The reasons table grows by one key.
# 2. The native-interrupt persistence path (which currently emits APPROVAL_REQUESTED /
#    MCP_AUTH_REQUIRED / ASK_A_QUESTION through `native_interrupt_payloads`) calls
#    `_maybe_emit_subagent_paused(...)` after each native interrupt persists, mirroring
#    how it already does for explicit-API interrupts.
```

### 2.4 The resume emission contract

```python
# services/ai-backend/src/runtime_worker/handlers/approval.py (excerpt)

# Tracks tasks resumed in this handler invocation to suppress duplicates.
_resumed_task_ids: set[str] = set()

async def _emit_subagent_resumed(
    self,
    *,
    run: RunRecord,
    task_id: str,
    decision_event_id: str | None,
) -> None:
    if task_id in _resumed_task_ids:
        return
    _resumed_task_ids.add(task_id)
    payload: dict[str, object] = {"task_id": task_id}
    if isinstance(decision_event_id, str):
        payload["source_event_id"] = decision_event_id
    await self.event_producer.append_api_event(
        run=run,
        source=StreamEventSource.SUBAGENT,
        event_type=RuntimeApiEventType.SUBAGENT_RESUMED,
        payload=payload,
        parent_task_id=task_id,  # task_id == parent_task_id for symmetry with paused
    )

# Call site: just before `await self._stream_resume(...)`. Resolve the set of
# (paused_task_ids_for_this_resolution) by reading the matching APPROVAL_REQUESTED /
# MCP_AUTH_REQUIRED / ASK_A_QUESTION events whose `parent_task_id` is non-None and
# whose approval/auth/question is the one being resolved.
```

The handler already has the resolution `event_id` in scope; reading `parent_task_id` off the matched interrupt event is the same query already used for the approval-detail endpoint.

### 2.5 The reducer projection

```typescript
// apps/frontend/src/features/chat/chatModel/subagentReducer.ts (excerpt)

const SUBAGENT_PAUSED = "subagent_paused";
const SUBAGENT_RESUMED = "subagent_resumed";

// RUNNING_STATES stays { queued, running } — paused is NOT a running state.

function projectEvent(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry | undefined {
  switch (event.event_type) {
    case SUBAGENT_STARTED:
      return onStarted(current, event);
    case SUBAGENT_PROGRESS:
      return onProgress(current, event);
    case SUBAGENT_COMPLETED:
      return onCompleted(current, event);
    case SUBAGENT_PAUSED:
      return onPaused(current, event);
    case SUBAGENT_RESUMED:
      return onResumed(current, event);
    default:
      return undefined;
  }
}

function onPaused(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
  if (base.status === "paused") return base; // identity-stable on replay
  return { ...base, status: "paused" };
}

function onResumed(
  current: SubagentEntry | undefined,
  event: RuntimeEventEnvelope,
): SubagentEntry {
  const base = current ?? seedFromEvent(event);
  if (base.status === "running") return base;
  // Defensive: only flip to running if currently paused or queued. Don't
  // resurrect terminal states (completed/cancelled/failed/timed_out).
  if (base.status === "paused" || base.status === "queued") {
    return { ...base, status: "running" };
  }
  return base;
}
```

### 2.6 Failure modes

| Failure                                                                                     | Behavior                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Approval handler crashes after emitting `SUBAGENT_RESUMED` but before resume stream starts. | Run transitions to `failed`. Reducer projects `SUBAGENT_COMPLETED(status=failed)` from cancel/cleanup cascade — terminal wins over the prior `running`. UI correctly shows the subagent as failed. |
| Two approval decisions race for the same approval.                                          | Existing approval-handler idempotency rejects the second one before reaching `_emit_subagent_resumed`. No double-emission.                                                                         |
| Native `ASK_A_QUESTION` interrupt with `parent_task_id=None` (asked by supervisor).         | `_maybe_emit_subagent_paused` short-circuits. Run-level approval card surfaces it; no per-subagent state change.                                                                                   |
| Replay of a pre-PR run.                                                                     | No paused/resumed events in the archive. Reducer seeds `running`; projects `_COMPLETED` to terminal. Same projection as today. No surprise renders.                                                |
| FE on an old build receives a `SUBAGENT_PAUSED` event.                                      | `projectEvent` switch falls through `default`. No-op. Status stays `running`. Acceptable transient until rebuild.                                                                                  |
| Run cancelled while paused.                                                                 | Cancel cascade emits `SUBAGENT_COMPLETED(status=cancelled)`. Reducer's `onCompleted` projects terminal `cancelled`. No synthetic resume. Final state is correct.                                   |

---

## 3 · Library evaluation

The headline question: **how do we tell the FE that a specific subagent is paused?**

| Approach                                                                             | Pro                                                                                              | Con                                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Explicit `SUBAGENT_PAUSED` / `SUBAGENT_RESUMED` events on the wire (this PR).** | Symmetric, auditable, projects directly into reducer. No inference. Survives reconnect / replay. | Two new event variants. Two new wire shapes. Mitigation: api-types + enum already landed.                                                                                                                                                             |
| B. FE infers paused state from `parent_task_id` on `APPROVAL_REQUESTED` events.      | No new events.                                                                                   | Inference logic duplicated in every reducer/component that cares. No deterministic resume signal — FE has to wait for the next `SUBAGENT_PROGRESS` to flip back. Brittle on reconnect (interrupt event already past the resume cursor). **Rejected.** |
| C. Add `status: "paused"` to `SUBAGENT_PROGRESS` payload.                            | One event variant.                                                                               | Worker doesn't naturally emit `SUBAGENT_PROGRESS` at the moment of interrupt — would need to synthesize one. Resume-side same. Conflates two concepts (progress + lifecycle). **Rejected.**                                                           |
| D. Lift to a generic `SUBAGENT_STATUS_CHANGED` envelope with a status enum.          | Future-proofs (new statuses don't add events).                                                   | Adds an indirection without a clear use case beyond paused/resumed. The two-event shape is honest; conflating them invites future ambiguity. **Rejected.**                                                                                            |

**Decision: A.** The infrastructure is already half-built (api-types + enum + worker pause emission). The remaining work is finishing the symmetric resume side and projecting the events in the reducer. Smallest delta, cleanest contract.

---

## 4 · File change summary

```
services/ai-backend/src/runtime_worker/
  stream_events.py                              EXTEND   ~+15 LoC   add ASK_A_QUESTION reason; route native interrupts through _maybe_emit_subagent_paused
  handlers/approval.py                          EXTEND   ~+50 LoC   _emit_subagent_resumed + dedup + call site before _stream_resume

services/ai-backend/tests/unit/runtime_worker/
  test_stream_events.py                         EXTEND   ~+90 LoC   4 new pause-emit cases
  test_handlers_approval.py                     EXTEND   ~+100 LoC  4 new resume-emit + idempotency cases

packages/api-types/src/
  index.ts                                      EXTEND   +1 line    "paused" added to SubagentLifecycleStatus union

apps/frontend/src/features/chat/chatModel/
  subagentReducer.ts                            EXTEND   ~+30 LoC   onPaused / onResumed projections + switch cases
  subagentReducer.test.ts                       EXTEND   ~+120 LoC  6 new reducer cases

# nothing else changes
migrations/                                     0
runtime_api/schemas/common.py                   0  (already has the enum)
packages/api-types/src/index.ts payload types   0  (already has SubagentPausedPayload / SubagentResumedPayload)
apps/frontend/src/features/chat/components/     0  (visual rendering is Phase 4)
```

Net new ≈ 100 LoC backend + 30 LoC FE + 310 LoC tests.

---

## 5 · Verification checklist

- [ ] `cd services/ai-backend && pytest tests/unit/runtime_worker/ tests/unit/agent_runtime/ tests/unit/runtime_api/` → all green.
- [ ] `npm run typecheck --workspace @enterprise-search/api-types` → clean.
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` → clean.
- [ ] `npm test --workspace @enterprise-search/frontend` → all green; new reducer cases pass.
- [ ] Manual canary on `make dev`:
  - Trigger a parallel-fleet run where one subagent invokes a tool that requires approval.
  - Confirm: `SUBAGENT_PAUSED(task_id=<paused_call>)` appears between `APPROVAL_REQUESTED` and the approval decision.
  - Sibling subagent's events keep streaming (Phase 2 invariant).
  - User approves → `SUBAGENT_RESUMED(task_id=<paused_call>)` precedes the next event from that subagent.
  - Pull `GET /v1/agent/runs/{run_id}/events` → both events present with monotonic `sequence_no` and the correct `parent_task_id`.
  - Reduce client-side: console-log `SubagentEntry.status` transitions; observe `running → paused → running → completed`.
- [ ] `git diff packages/api-types/` shows only the `"paused"` literal addition.
- [ ] `git diff migrations/` is empty.

---

## 6 · Out of scope (follow-ups)

- **Phase 4 — FE visual treatment of paused state + clickable rows with inline timeline.** Owns the amber pulse, paused chip, and click-to-expand affordance on `<FleetSubagentRow>` and the pane card. Tracked in [`pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md`](./pr-3.2.7-subagent-paused-fleet-row-and-clickable-timeline.md).
- **Approvals tab badge for paused subagents.** Useful once Phase 4 lands; a per-subagent badge in the workspace pane Approvals tab links the paused row to the gating approval card.
- **Backfill paused/resumed on archived runs.** We don't backfill. If the archive UI surfaces visibly broken renders (rare — runs that were paused mid-flight at PR cut), a one-time migration could synthesize bookend events from `runtime_async_tasks.status` transitions. Tracked as a paper cut.
- **`SubagentLifecycleStatus.paused` in the archive read.** Today the archive reads `runtime_async_tasks.status` which is terminal-or-running. If product wants paused-in-archive semantics we'd add a `last_known_status` projection. Out of scope here.
- **Telemetry.** Counters for `SUBAGENT_PAUSED` / `_RESUMED` per run / per reason. If real traffic shows runaway pause emissions (e.g., a chained-approval loop), we add metrics in a follow-up.

---

## References

- [`docs/new-design/pr-3.2.5-subagent-call-id-propagation.md`](./pr-3.2.5-subagent-call-id-propagation.md) — Phase 1, makes `parent_task_id` deterministic.
- [`services/ai-backend/src/runtime_worker/streaming_executor.py:175-219`](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L175-L219) — Phase 2 interrupt drain.
- [`services/ai-backend/src/runtime_worker/stream_events.py:367-406`](../../services/ai-backend/src/runtime_worker/stream_events.py#L367-L406) — existing `_maybe_emit_subagent_paused`.
- [`services/ai-backend/src/runtime_worker/handlers/approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) — approval/resolution handler (resume-emit site).
- [`apps/frontend/src/features/chat/chatModel/subagentReducer.ts`](../../apps/frontend/src/features/chat/chatModel/subagentReducer.ts) — FE state projection.
- [`packages/api-types/src/index.ts:1514-1539`](../../packages/api-types/src/index.ts#L1514-L1539) — `SubagentPausedPayload` / `SubagentResumedPayload` (already landed).
- [`packages/api-types/src/index.ts:1735-1741`](../../packages/api-types/src/index.ts#L1735-L1741) — `SubagentLifecycleStatus` (extends in this PR).
