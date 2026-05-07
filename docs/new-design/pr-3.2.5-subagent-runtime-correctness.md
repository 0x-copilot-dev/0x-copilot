# PR 3.2.5 — Subagent runtime correctness: deterministic call_id linkage + per‑subagent interrupt isolation

> **Status:** Phase 1 shipped (verified end‑to‑end) · Phase 2 in progress · Phases 3 + 4 planned · v1
> **Plan reference:** Wave 3 follow‑up to [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md). Closes the ground‑truth bugs that surfaced when we tried to render the in‑thread fleet card with real backend data: inner subagent tool calls leaking to the supervisor thread, and the entire run halting on any single subagent's interrupt.
> **Owner:** ai‑backend (worker + execution surface) · packages/api‑types (2 new event variants in Phase 3) · apps/frontend (Phase 4 — paused state visuals + clickable rows).
> **Size:** **L.** Touches the runtime control plane (executor, event projection, deepagents integration). Bounded by the existing `streaming_executor.run` contract: `StreamingResult.action_interrupted` is the single channel that flows back to the run handler.
> **Depends on:** ✅ PR 1.5, 3.2.1, 3.2.2, 3.2.3, 3.2.4. **No new schema migration.** **No new auth scope.** **No new dep.**
> **Reads alongside:**
>
> - [`pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md) — `parent_task_id` linkage contract for tool/reasoning events nested under a `run_subagent` part.
> - [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md) — fleet rendering depends on the linkage being right; this PR is what makes that linkage actually work in production.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — runtime + streaming + projection rules.
>
> **Sibling docs:** none. This PR is the close‑out of the subagent‑surface line opened by 3.2.x.

---

## 0 · TL;DR

Two correctness bugs were found in the subagent runtime when we tried to render the in‑thread fleet card with real production data:

| Bug                                                                                                                                                                                                                                  | Symptom (as the user sees it)                                                                                                                                                                                  | Root cause                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Inner subagent tool calls leak to the supervisor thread.** Web searches and other inner tool calls from inside a subagent render as standalone tool cards in the main thread instead of nesting inside the subagent's timeline. | `parent_task_id` on the inner `tool_call_*` events is `None` whenever ≥2 subagents are dispatched in the same supervisor turn (parallel research fleet). FE reducer can't match them to a `run_subagent` part. | `StreamUpdateProcessor.subagent_call_id_for_subgraph` returns `None` when ≥2 supervisor `call_id`s are unlinked — by design, to avoid mis‑attribution from a racy FIFO pop. The behavior is documented in‑file.                                             |
| **B. Any single subagent's interrupt halts the entire run.**                                                                                                                                                                         | One subagent in a fleet hits an approval / MCP auth / ask‑a‑question, and **all sibling subagents' work in flight is cancelled**. The user has to re‑run them once they resolve the interrupt.                 | `streaming_executor.run` early‑returns the moment it observes `APPROVAL_REQUESTED` or `MCP_AUTH_REQUIRED` in the event stream. That abandons the supervisor's `astream`, which in turn cancels parallel branches via LangGraph's iterator‑driven execution. |

Both bugs are real, both have user‑visible blast radius, both block the polished fleet UX from PR 3.2.4. This PR fixes them at the runtime control plane and makes the FE light up correctly without further FE work for bug A.

The four‑phase scope, with status:

| Phase | What                                                                                                                                                                                                                                                                                                     | Status                                                                  | Verified                                  |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------- |
| **1** | Deterministic `(subgraph_task_id → supervisor_call_id)` linkage via injected `RunnableConfig.metadata`. Replaces the FIFO heuristic. Ships our `atlas_task_tool` (monkey‑patches `deepagents._build_task_tool`).                                                                                         | **Shipped**                                                             | Production run trace + 2 regression tests |
| **2** | Per‑subagent interrupt isolation. `streaming_executor.run` no longer early‑returns on the first interrupt — it drains the stream so sibling subagents finish their work. Paused subagent stays paused via LangGraph's checkpoint.                                                                        | **In progress** (single‑edit landed; broad ai‑backend test run pending) | TBD                                       |
| **3** | New event variants: `subagent_paused` (emitted when a subagent's branch hits an interrupt) and `subagent_resumed` (emitted when the user resolves it). Distinct from existing `subagent_progress`/`subagent_completed` so the FE can mark a row paused without inferring from the absence of completion. | Planned                                                                 | —                                         |
| **4** | Frontend: render paused subagent state in fleet rows + workspace pane card. Make rows clickable to expand the per‑subagent timeline inline (closes the visible gap from your earlier "I want them clickable" ask). Independent rows (no accordion).                                                      | Planned                                                                 | —                                         |

LoC estimate (post Phase 1): ai‑backend ≈ 90 (Phase 2: executor + tests) + 120 (Phase 3: event variants + audit hooks) · api‑types ≈ 30 (Phase 3) · frontend ≈ 200 (Phase 4: paused visual + click‑to‑expand) plus tests. **Net new ≈ 440 LoC** beyond Phase 1's already‑shipped ≈ 350 LoC.

---

## 1 · PRD

### 1.1 Problem

Two bug classes, both observed live, both with user‑visible damage:

#### Bug A — inner subagent tool calls leak to the supervisor thread

When the supervisor dispatches a parallel research fleet (≥2 subagents in one turn), each subagent's inner `web_search` / `read_file` / etc. tool calls render as **standalone cards in the supervisor thread** instead of nesting under the `run_subagent` block (and equivalently failing to populate `args.activities` for the workspace pane's expandable timeline).

The root cause is documented in [`stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py):

> _For the FIRST event in a new subgraph, we link to a queued supervisor call_id ONLY when exactly one subagent is currently unlinked. With two or more unlinked subagents a naive FIFO pop is racy: when the supervisor dispatches a fast subagent (e.g. one that calls no internal tools) and a slow research subagent in parallel, the slow subagent's first tool message can arrive at the processor before the fast subagent's `SUBAGENT_COMPLETED` removes it from the queue, and the slow subagent's tools end up wrongly attributed to the fast subagent. Returning None here for ambiguous cases makes early tool events orphan rather than mis‑attributed._

Translation: the resolver chose **orphaning over mis‑attribution** because the linkage was implicit (subgraph order ≈ dispatch order, but only when nothing concurrent). A real fleet — three `general-purpose` subagents started in one tick — has ≥2 unlinked queue entries when the first inner tool fires, so every inner tool call returns `parent_task_id = None`.

The frontend reducer (`upsertSubagentActivity`) can't match orphaned events to any `run_subagent` part, so they fall through to the standalone tool‑card path. Visible result in the user's screenshot: "Reading 6 sources" group card in the supervisor thread, plus three full `<SubagentCard>` blocks above the empty fleet card.

#### Bug B — any single subagent's interrupt halts the entire run

When a subagent calls a tool that triggers an approval / MCP auth / ask‑a‑question, the supervisor's `astream` is abandoned mid‑iteration ([`streaming_executor.py:175-178`](../../services/ai-backend/src/runtime_worker/streaming_executor.py#L175)):

```python
for event in new_events:
    if event.event_type in cls.action_interrupt_events:
        result.action_interrupted = True
        return result   # ← abandons the iterator
```

LangGraph's `astream` is a consumer‑driven async iterator. Abandoning it cancels the underlying graph execution. **Other parallel subagent branches that were healthy and mid‑work are cancelled** along with the interrupted one. When the user later resolves the approval, the resumed run starts fresh — siblings re‑run from scratch. Wasted tokens, wasted time, no preservation of progress.

### 1.2 Goals

1. **A is shipped.** Inner subagent tool calls correctly nest under their parent `run_subagent` part for any number of parallel subagents. Verified in production.
2. **B is fixed at the runtime, not papered over in the FE.** Sibling subagents keep running when one of them needs an interrupt. The paused subagent stays paused via LangGraph's checkpoint; the others advance until done.
3. **The interrupt visual contract honors the runtime semantics.** A new `subagent_paused` / `subagent_resumed` event pair lets the FE mark a specific subagent paused without inferring from "started but never completed". This is the primary signal for the fleet row's amber pause state.
4. **Per‑row click‑to‑expand inline timeline** in the fleet card (the explicit ask from your prior UX feedback). Every fleet row gets a `<details>` disclosure showing the same `SubagentActivityList` the workspace pane shows. Independent — opening one row doesn't close another.
5. **Zero schema migration. Zero new dep. Zero new auth scope.** Stays inside the existing event/persistence/streaming surface.
6. **Existing test suite stays green.** Including legacy / synthetic chunk fixtures that don't go through our metadata‑injecting task tool — they fall through to the original behavior via a deliberate raw‑UUID fallback.

### 1.3 Non‑goals

- ❌ **Approval forwarding from a paused subagent.** The two‑stage approval chain (PR 1.4) doesn't change. A subagent's interrupt routes to the same approval queue; if the user wants to forward it, that's PR 1.4's existing path.
- ❌ **Cancel a single subagent without cancelling the run.** Out of scope. The existing run‑cancel cancels everything; a per‑subagent cancel button is a follow‑up.
- ❌ **Streaming `subagent_paused`'s payload to the FE Activity tab.** Phase 3 ships the event variant; surfacing it as an inline‑thread card vs only the fleet row + pane is a Phase 4 visual decision.
- ❌ **Multi‑interrupt resolution flow.** If the user has 2 paused subagents and approves one, the other stays paused. The runtime supports this via LangGraph's per‑branch resume, but the visual flow (which approval card resolves which subagent? what happens to the sibling) is a follow‑up.
- ❌ **Replay correctness for runs interrupted by Bug A/B before this PR shipped.** Old runs in the event store keep their orphan tool events. New runs are correct. We don't backfill.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                                                               | Verified by                                                                                                                                                        |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| AC‑1  | When the supervisor dispatches ≥2 subagents in one turn, every inner `tool_call_*` and `reasoning_summary_*` event from inside any of those subagents carries `parent_task_id` equal to that subagent's supervisor `call_id`.                                                                                                                                                           | **Phase 1 ✓** — verified by production run trace; regression test in `test_stream_events.py::test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids`. |
| AC‑2  | When a subagent in a fleet hits an interrupt, sibling subagents that were running continue to completion. They emit `SUBAGENT_COMPLETED` with their full result. The supervisor's `astream` exits naturally only when all subagents are either done or paused.                                                                                                                          | Phase 2 — backend unit test driving an interrupt event mid‑stream; assert siblings' `SUBAGENT_COMPLETED` events are observed.                                      |
| AC‑3  | A subagent that hits an interrupt emits exactly one `subagent_paused` event with its `task_id`. When the user resolves the interrupt, exactly one `subagent_resumed` event fires before any further `SUBAGENT_PROGRESS` from that subagent. Both events are subagent‑scoped (have `task_id`, `parent_task_id`).                                                                         | Phase 3 — backend unit tests + api‑types schema parity tests.                                                                                                      |
| AC‑4  | The frontend fleet row visually marks a paused subagent (amber indicator + pause text in the meta line). Sibling rows that are still running keep their progress animation.                                                                                                                                                                                                             | Phase 4 — RTL test on `<FleetSubagentRow>`.                                                                                                                        |
| AC‑5  | Every fleet row is a `<details>` disclosure. Closed: the existing compact row layout. Open: `SubagentActivityList` populated from the chat tree's `args.activities` (same selector PR 3.2.1 wired). Independent — opening one row doesn't toggle another.                                                                                                                               | Phase 4 — RTL test on `<FleetSubagentRow>`.                                                                                                                        |
| AC‑6  | Existing tests stay green. Specifically: `test_runtime_worker_persists_normalized_activity_stream_events` (the synthetic stream fixture) keeps passing; `tests/unit/runtime_worker/test_stream_events.py::test_tool_event_inside_subagent_carries_subagent_id` keeps passing.                                                                                                           | Phase 1 ✓ + Phase 2 + Phase 3 — full ai‑backend pytest suite.                                                                                                      |
| AC‑7  | No new dep added in `services/ai-backend/requirements.txt` or `apps/frontend/package.json`.                                                                                                                                                                                                                                                                                             | Diff audit.                                                                                                                                                        |
| AC‑8  | No new event type wire‑breaks. `subagent_paused` / `subagent_resumed` are added as new variants of `RuntimeApiEventType`; no existing variant is renamed or removed.                                                                                                                                                                                                                    | api‑types diff + projection coverage.                                                                                                                              |
| AC‑9  | RLS / tenant isolation invariants hold. `subagent_paused` / `subagent_resumed` are persisted through the existing event store (RLS by `org_id`); no new column added.                                                                                                                                                                                                                   | Persistence test (Phase 3).                                                                                                                                        |
| AC‑10 | When the user is running a fleet and one subagent hits an MCP auth requirement, the FE shows: (1) the MCP auth card in the main thread (existing behavior), (2) the affected fleet row in amber/paused state (new), (3) sibling fleet rows continuing to animate progress until they complete. After the user authenticates, the paused row resumes its progress, eventually completes. | Phase 4 — manual `make dev` walk‑through.                                                                                                                          |

### 1.5 Risks

| Risk                                                                                                                                                                                                                                           | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 2: removing the executor's early‑return on interrupt causes a regression on supervisor‑level interrupts (where there are no siblings to keep running). Worst case: the run hangs because we wait for an `astream` that's already paused. | LangGraph's `astream` exits naturally when all branches are paused or done. With no siblings, "all branches" is just the supervisor; iterator returns immediately after the interrupt event is yielded. Net effect: same as before for the no‑siblings case. New backend unit test asserts: a single‑subagent run with an interrupt still terminates the loop in the same number of chunks as before.                                             |
| Phase 2: a sibling subagent emits a SECOND interrupt mid‑drain. The handler still transitions to WAITING_FOR_APPROVAL — but with two pending interrupts.                                                                                       | Existing approval queue already supports multiple. The user resolves them in any order. Each resolution resumes its branch via LangGraph's per‑interrupt resume. Cross‑interrupt ordering is the user's problem, not ours.                                                                                                                                                                                                                        |
| Phase 1: monkey‑patching `deepagents._build_task_tool` is brittle if deepagents refactors that internal symbol.                                                                                                                                | Idempotent install (`_atlas_task_tool_installed` flag); covered by tests; if upstream renames, tests fail loudly. The wrapping is small (~150 LoC) — easy to update for a new deepagents API.                                                                                                                                                                                                                                                     |
| Phase 1's `from __future__ import annotations` would have broken langchain's `ToolRuntime` injection (PEP 563 makes annotations strings).                                                                                                      | **Already hit and fixed.** The file deliberately omits `from __future__ import annotations` — see comment in `atlas_task_tool.py`. A regression here would surface as `TypeError: atask() missing 1 required positional argument: 'runtime'` on the first run.                                                                                                                                                                                    |
| Phase 3: `subagent_paused` events emitted from a subgraph that hasn't yet been linked (no `subagent_call_id_for_subgraph` cache hit) get `parent_task_id = subgraph_uuid` instead of the supervisor `call_id`.                                 | The cache is populated by Phase 1's metadata‑injection on the FIRST chunk from a subgraph (which is always a `messages` chunk emitted by the LLM before any tool call or interrupt). By the time an interrupt fires, the cache is warm. If the worker ever observes an interrupt before any other event from a subgraph, the resolver falls back to the raw subgraph UUID — same as today; the FE handles this gracefully via PR 3.2.4's reshape. |
| Phase 4: the click‑to‑expand inline timeline duplicates content already visible in the workspace pane.                                                                                                                                         | Yes — that's the explicit user ask. The pane is the verification surface for "everything across this conversation"; the fleet rows are the verification surface for "what's happening right now in this turn". Two surfaces, same data, different scopes.                                                                                                                                                                                         |

### 1.6 Unit testing requirements

**Phase 1 (already shipped):**

- `tests/unit/runtime_worker/test_stream_events.py::test_chunk_metadata_links_parallel_subagents_to_supervisor_call_ids` — happy‑path: 2 subagents in flight, each emits a chunk with `supervisor_task_call_id` metadata, both inner tool calls correctly attributed. ✓
- `tests/unit/runtime_worker/test_stream_events.py::test_chunk_without_supervisor_metadata_falls_back_to_raw_subgraph_id` — legacy / synthetic chunks (no metadata) fall back to the raw subgraph UUID. ✓
- Full ai‑backend pytest suite: 809/809 passing. ✓

**Phase 2 (in progress):**

- `tests/unit/runtime_worker/test_streaming_executor_isolation.py::test_interrupt_does_not_cancel_sibling_subagents` (new) — drive a synthetic stream where sub A hits an interrupt and sub B continues to emit `SUBAGENT_COMPLETED`; assert both events are observed in the executor's recorded events. Assert `result.action_interrupted == True` AND `result.subagent_summaries` includes sub B's summary.
- `tests/unit/runtime_worker/test_streaming_executor_isolation.py::test_supervisor_only_interrupt_terminates_promptly` — ensure the supervisor‑level interrupt (no sibling subagents) doesn't hang. Same call shape as today.

**Phase 3 (planned):**

- `tests/unit/runtime_api/schemas/test_subagent_paused_event.py` — pydantic round‑trip + projection metadata + `parent_task_id` set correctly.
- `tests/unit/runtime_worker/test_stream_subagents.py::test_subagent_paused_emitted_on_interrupt` — drive a subagent into an interrupt; assert exactly one `subagent_paused` event with the right `task_id` is emitted.

**Phase 4 (planned):**

- `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.test.tsx` — paused state visual; click‑to‑expand discloses timeline; independent disclosures.

---

## 2 · Spec

### 2.1 Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Phase 1 (shipped) — deterministic call_id linkage                         │
│                                                                            │
│   Supervisor LLM → emits `task` tool call                                  │
│         │                                                                  │
│         ▼                                                                  │
│   atlas_task_tool.task / atask                                             │
│   (replaces deepagents._build_task_tool via module-load monkey-patch)      │
│         │                                                                  │
│         ├─ captures runtime.tool_call_id (= supervisor call_id)            │
│         ├─ builds subagent's RunnableConfig:                               │
│         │     metadata = {..., "supervisor_task_call_id": tool_call_id}   │
│         └─ subagent.ainvoke(state, config)                                 │
│                                                                            │
│   LangGraph subgraphs=True streaming → chunks with                         │
│       data = (message, metadata)  ← metadata carries our key               │
│         │                                                                  │
│         ▼                                                                  │
│   StreamPartParser.supervisor_task_call_id_for(part)                       │
│   StreamUpdateProcessor.register_supervisor_call_id_for_subgraph(...)      │
│         │                                                                  │
│         ▼                                                                  │
│   Cache: (run_id, subgraph_task_id) → supervisor_call_id                   │
│   subsequent events resolve via cache, deterministic                       │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Phase 2 (in progress) — interrupt isolation                               │
│                                                                            │
│   StreamingExecutor.run loop:                                              │
│      for chunk in supervisor.astream:                                      │
│          ... emit events ...                                               │
│          for event in new_events:                                          │
│              if event.event_type in action_interrupt_events:               │
│                  result.action_interrupted = True                          │
│                  # OLD: return result  ← cancels iterator + siblings       │
│                  # NEW: continue draining — siblings keep emitting         │
│              ... (subagent tracking)                                       │
│                                                                            │
│   When astream exhausts (all branches done or paused):                     │
│      result.action_interrupted carries WAITING_FOR_APPROVAL transition     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Phase 3 (planned) — subagent_paused / subagent_resumed                    │
│                                                                            │
│   When stream_events observes an interrupt event with parent_task_id ≠     │
│   None (i.e. inside a subagent), also emit:                                │
│      RuntimeApiEventType.SUBAGENT_PAUSED                                   │
│        payload: {task_id, reason, source_event_id}                         │
│        parent_task_id: same supervisor call_id                             │
│                                                                            │
│   When approval handler resumes a subagent's branch, also emit:            │
│      RuntimeApiEventType.SUBAGENT_RESUMED                                  │
│        payload: {task_id, source_event_id}                                 │
│        parent_task_id: same supervisor call_id                             │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  Phase 4 (planned) — FE paused state + clickable rows                      │
│                                                                            │
│   subagentReducer.applySubagentEvent: handle paused/resumed → update       │
│     SubagentEntry.status                                                   │
│                                                                            │
│   <FleetSubagentRow>:                                                      │
│      - "paused" / "running" / "completed" / "failed" → indicator + meta    │
│      - <details> disclosure: <SubagentActivityList activities=…/>          │
│      - Independent disclosures, no accordion                               │
└────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                                                | Module                                                                                                                                                   | Phase | Status                                                                      |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | --------------------------------------------------------------------------- |
| `services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py`                 | NEW — replaces `deepagents._build_task_tool`. Injects `supervisor_task_call_id` into subagent config metadata.                                           | 1     | ✓                                                                           |
| `services/ai-backend/src/agent_runtime/execution/factory.py`                         | EXTEND — `install_atlas_task_tool()` at module‑load time.                                                                                                | 1     | ✓                                                                           |
| `services/ai-backend/src/runtime_worker/stream_parts.py`                             | EXTEND — `SUPERVISOR_TASK_CALL_ID_KEY` constant + `StreamPartParser.supervisor_task_call_id_for(part)` reader.                                           | 1     | ✓                                                                           |
| `services/ai-backend/src/runtime_worker/stream_subagents.py`                         | EXTEND — `register_supervisor_call_id_for_subgraph(...)` + `cached_subagent_call_id_for_subgraph(...)`. FIFO‑pop kept inside legacy resolver only.       | 1     | ✓                                                                           |
| `services/ai-backend/src/runtime_worker/stream_events.py`                            | EXTEND — `append_activity_events` reads chunk metadata, registers linkage, resolves cache‑first with raw‑UUID fallback.                                  | 1     | ✓                                                                           |
| `services/ai-backend/tests/unit/runtime_worker/test_stream_events.py`                | EXTEND — 2 regression tests for parallel‑fleet linkage + legacy fallback.                                                                                | 1     | ✓                                                                           |
| `services/ai-backend/src/runtime_worker/streaming_executor.py`                       | EDIT — drop early‑return on `action_interrupt_events`; flag `result.action_interrupted = True` and continue draining.                                    | 2     | Single edit landed; broad ai‑backend test run + new isolation test pending. |
| `services/ai-backend/tests/unit/runtime_worker/test_streaming_executor_isolation.py` | NEW — sibling‑continuation + supervisor‑only termination tests.                                                                                          | 2     | Pending.                                                                    |
| `packages/api-types/src/index.ts`                                                    | EXTEND — `RuntimeApiEventType.SUBAGENT_PAUSED`, `RuntimeApiEventType.SUBAGENT_RESUMED`. New payload types `SubagentPausedEvent`, `SubagentResumedEvent`. | 3     | Pending.                                                                    |
| `services/ai-backend/src/runtime_api/schemas/events.py`                              | EXTEND — projection metadata for the two new event types.                                                                                                | 3     | Pending.                                                                    |
| `services/ai-backend/src/runtime_worker/stream_events.py` (round 2)                  | EXTEND — emit `subagent_paused` alongside the existing approval/auth event when `parent_task_id` is non‑None.                                            | 3     | Pending.                                                                    |
| `services/ai-backend/src/runtime_worker/handlers/approval.py`                        | EXTEND — emit `subagent_resumed` when a subagent‑scoped approval is resolved.                                                                            | 3     | Pending.                                                                    |
| `apps/frontend/src/features/chat/chatModel/subagentReducer.ts`                       | EXTEND — handle `subagent_paused` / `subagent_resumed` to flip `SubagentEntry.status`.                                                                   | 4     | Pending.                                                                    |
| `apps/frontend/src/features/chat/components/subagents/FleetSubagentRow.tsx`          | EXTEND — paused state visual + click‑to‑expand `<details>` with `<SubagentActivityList>` body.                                                           | 4     | Pending.                                                                    |

### 2.3 What we do NOT add

- ❌ A new database migration. All event variants ride the existing `runtime_events` table.
- ❌ A `subagent_paused` audit row distinct from existing approval audit. The existing `approval_request` / `mcp_auth_required` audit rows already log who/when/why; we don't double‑audit.
- ❌ A new auth scope. `RUNTIME_USE` covers everything.
- ❌ A new dep in any service or app.

### 2.4 Phase 1 — implementation summary (already shipped, documented for the record)

**Deepagents replacement.** `atlas_task_tool.build_atlas_task_tool` mirrors deepagents' `_build_task_tool` shape: same `StructuredTool.from_function(name="task", func=task, coroutine=atask, args_schema=TaskToolSchema, infer_schema=False)`. The only behavioural delta is in `_build_subagent_config`:

```python
def _build_subagent_config(runtime: ToolRuntime) -> RunnableConfig:
    parent_configurable = dict(runtime.config.get("configurable", {}) or {})
    parent_metadata = dict(runtime.config.get("metadata", {}) or {})
    return {
        "configurable": {
            **parent_configurable,
            "ls_agent_type": "subagent",
            SUPERVISOR_TASK_CALL_ID_KEY: runtime.tool_call_id,
        },
        "metadata": {
            **parent_metadata,
            SUPERVISOR_TASK_CALL_ID_KEY: runtime.tool_call_id,
        },
    }
```

`runtime.tool_call_id` is the supervisor's `task` call_id, available at the langchain tool boundary. We thread it into both `configurable` (defensive) and `metadata` (primary — what the worker reads). LangGraph propagates `metadata` through `subgraphs=True` chunks for `messages` mode in the `data` tuple's second position.

**Module‑load monkey‑patch.** `factory.py` calls `install_atlas_task_tool()` at module import time. The install is idempotent (sets `_ds._atlas_task_tool_installed = True`).

**Worker reader.** `StreamPartParser.supervisor_task_call_id_for(part)` probes both the `data[1]` metadata (messages mode) and a top‑level `metadata` field (other modes). Returns the call_id or `None`.

**Cache + fallback.** `StreamUpdateProcessor.register_supervisor_call_id_for_subgraph(run_id, subgraph_task_id, supervisor_call_id)` pins the mapping and removes the call_id from the FIFO queue so legacy paths can't double‑dispatch it. `cached_subagent_call_id_for_subgraph(...)` is a cache‑only lookup with no FIFO side effect — used by the chunk‑level emit path. `subagent_call_id_for_subgraph(...)` keeps the FIFO fallback for `messages` mode (`stream_tools.process`) where it was the original source of truth.

**Wire‑up.** `stream_events.append_activity_events` extracts `chunk_supervisor_call_id`, registers the linkage if present, then resolves `parent_task_id` cache‑first with raw‑UUID fallback. Existing `messages`‑mode resolution stays inside `stream_tools.process` so that path's tests don't regress.

**Critical pitfall (recorded so we don't repeat it):** `from __future__ import annotations` at the top of `atlas_task_tool.py` was removed deliberately. PEP 563 stringifies annotations, which broke langchain's `ToolRuntime` injection (it uses `inspect.signature(fn).parameters[..].annotation` and checks `issubclass(annotation, _DirectlyInjectedToolArg)`). With stringified annotations, the check fails silently and `runtime` isn't injected — manifests as `TypeError: atask() missing 1 required positional argument: 'runtime'` on the first dispatch.

**Verification.** Production run trace (run id `4ced479741494b939986760a3b606a66`) shows two parallel subagents (`call_knDW…` and `call_XjTE…`); inner tool calls at seqs 7, 79, 81, 83 attributed to sub A; seq 85 attributed to sub B. Interleaved emission, correct attribution. No FIFO race. 809/809 unit tests pass.

### 2.5 Phase 2 — interrupt isolation (in progress)

**The change.** `streaming_executor.run` no longer returns when it observes an interrupt event:

```python
# Before
for event in new_events:
    if event.event_type in cls.action_interrupt_events:
        result.action_interrupted = True
        return result   # abandons the iterator → cancels parallel branches

# After
for event in new_events:
    if event.event_type in cls.action_interrupt_events:
        result.action_interrupted = True
        # do NOT return — drain the rest so siblings finish
    ...
```

**Why this works.** LangGraph's `astream(subgraphs=True)` is a consumer‑driven async iterator over the entire run's event stream. When a node hits `interrupt(...)`, that node's coroutine raises a `GraphInterrupt`. LangGraph's runner catches it, persists the checkpoint for that branch, and marks the branch as "interrupted". Other parallel branches are unaffected — they keep running. The iterator keeps yielding events from healthy branches until they all reach a steady state (done or interrupted). Then `astream` exits naturally.

**What the user sees.** A fleet of three subagents. One needs Salesforce auth. Approval card appears in the supervisor thread. The other two finish their searches, emit their results. Once those two are done, the run is in `WAITING_FOR_APPROVAL`. The user authenticates. The paused subagent resumes from its checkpoint, runs its tool, emits its result. The supervisor synthesizes the final message using all three subagents' results.

**What stays the same.** `result.action_interrupted = True` still flows back to the run handler, which still transitions to `WAITING_FOR_APPROVAL`. The approval queue, audit, and resume infrastructure are unchanged.

### 2.6 Phase 3 — `subagent_paused` / `subagent_resumed` event variants (planned)

**Why two new events instead of inferring from existing ones.** The FE currently has no way to distinguish "subagent is running but slow" from "subagent is paused waiting for the user". The first should keep the spinner; the second should switch to amber. Inferring from "started + has open approval card with matching parent_task_id" is fragile (depends on cross‑surface event correlation in the FE reducer).

**Emit sites:**

- `stream_events.append_activity_events` — when an approval / mcp_auth / ask‑a‑question event fires AND `parent_task_id` resolves to a subagent's supervisor call_id, ALSO emit `subagent_paused` with the same `parent_task_id` and `task_id = parent_task_id`. Reason field carries the kind of interrupt.
- `handlers/approval.py` — when an approval is resolved AND the underlying interrupt was subagent‑scoped, emit `subagent_resumed` before invoking the resume of the subagent's branch.

**Payload shape:**

```ts
interface SubagentPausedEvent extends RuntimeEventEnvelope {
  event_type: "subagent_paused";
  task_id: string; // the supervisor's task call_id
  parent_task_id: string; // same — the FE matches via this
  payload: {
    reason: "approval" | "mcp_auth" | "ask_a_question";
    source_event_id: string; // the corresponding interrupt event_id
  };
}
interface SubagentResumedEvent extends RuntimeEventEnvelope {
  event_type: "subagent_resumed";
  task_id: string;
  parent_task_id: string;
  payload: {
    source_event_id: string; // the resolution event_id
  };
}
```

### 2.7 Phase 4 — FE paused visual + clickable rows (planned)

**`SubagentEntry.status` extended** to include `paused`. Reducer updates on `subagent_paused` → `paused`, on `subagent_resumed` → back to `running`.

**`<FleetSubagentRow>` paused state:**

- Indicator: amber dot (no animation)
- Meta line: `"Paused — waiting on you"` with a status tone
- Progress bar: frozen at its current fill (no animation)
- Sibling rows: unchanged — keep animating progress

**Click‑to‑expand inline timeline:**

- `<details>` disclosure on each row
- `<summary>` = the existing compact row content + a chevron
- `<details>` body = `<SubagentActivityList>` populated from the pane's `useSubagentActivities` selector — same data, different rendering surface
- Independent: opening one disclosure does not close another (no accordion)

---

## 3 · Library evaluation

Per PR 3.2.1 §3, PR 3.2.2 §3, PR 3.2.4 §3 — same posture, no new deps. The only library question this PR introduces is whether to fork or monkey‑patch `deepagents._build_task_tool`:

| Approach                                              | Verdict                                                                                                                                        |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fork deepagents**                                   | ❌ Adds a maintenance fork of a moving dep. The function we're replacing is small (~150 LoC); a single internal symbol replacement is cheaper. |
| **Monkey‑patch**                                      | ✓ Idempotent install, easy to update if upstream renames, easy to remove if upstream adopts a metadata‑injection hook officially.              |
| **Wrap subagents at the langchain Runnable boundary** | ❌ Doesn't have access to `runtime.tool_call_id`. By the time we'd see `state` in a `Runnable`, the supervisor's call_id is gone.              |
| **PR upstream a metadata injection hook**             | Worth doing as a follow‑up; in the meantime, monkey‑patch ships now.                                                                           |

---

## 4 · File change summary

```
services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py    NEW (~210 LoC) — Phase 1 ✓
services/ai-backend/src/agent_runtime/execution/factory.py            +10 LoC — Phase 1 ✓
services/ai-backend/src/runtime_worker/stream_parts.py                +50 LoC — Phase 1 ✓
services/ai-backend/src/runtime_worker/stream_subagents.py            +50 LoC — Phase 1 ✓
services/ai-backend/src/runtime_worker/stream_events.py               +35 LoC — Phase 1 ✓
services/ai-backend/tests/unit/runtime_worker/test_stream_events.py   +110 LoC — Phase 1 ✓

services/ai-backend/src/runtime_worker/streaming_executor.py          ~+15 LoC — Phase 2 (in progress)
services/ai-backend/tests/unit/runtime_worker/
    test_streaming_executor_isolation.py                              NEW (~120 LoC) — Phase 2

packages/api-types/src/index.ts                                       +30 LoC — Phase 3
services/ai-backend/src/runtime_api/schemas/events.py                 +40 LoC — Phase 3
services/ai-backend/src/runtime_worker/stream_events.py (round 2)     +30 LoC — Phase 3
services/ai-backend/src/runtime_worker/handlers/approval.py           +20 LoC — Phase 3
services/ai-backend/tests/unit/runtime_api/schemas/
    test_subagent_paused_event.py                                     NEW (~80 LoC) — Phase 3

apps/frontend/src/features/chat/chatModel/subagentReducer.ts          +20 LoC — Phase 4
apps/frontend/src/features/chat/components/subagents/
    FleetSubagentRow.tsx                                              +60 LoC — Phase 4
apps/frontend/src/features/chat/components/subagents/
    FleetSubagentRow.test.tsx                                         +120 LoC — Phase 4
apps/frontend/src/styles.css                                          +30 LoC — Phase 4

# nothing else changes
migrations/                                                           0
package.json / requirements.txt                                       0
auth scopes                                                           unchanged
```

---

## 5 · Verification checklist

- [x] **Phase 1: deterministic linkage** — production run trace confirms `parent_task_id = supervisor call_id` for every inner tool event across parallel subagents.
- [x] **Phase 1: regression tests** — new tests in `test_stream_events.py` cover happy path + legacy fallback.
- [x] **Phase 1: full pytest** — 809/809 ai‑backend tests pass.
- [ ] **Phase 2: streaming_executor edit** — landed; needs ai‑backend test run + new isolation test.
- [ ] **Phase 2: live sibling‑continuation walk‑through** in `make dev` — research fleet with one MCP auth interrupt; siblings finish; approval resolves; paused subagent resumes; final answer synthesized.
- [ ] **Phase 3: api‑types diff** — clean `npm run typecheck --workspace @enterprise-search/api-types`.
- [ ] **Phase 3: backend tests** — new event types projected, persisted, scoped by run_id.
- [ ] **Phase 4: FE typecheck + tests + build** — clean.
- [ ] **Phase 4: live walk‑through** — fleet card row shows amber pause + clickable timeline.

---

## 6 · Out of scope (follow‑ups)

- Per‑subagent cancel button (separate from run‑cancel).
- Approval forwarding from a paused subagent (rides PR 1.4's two‑stage chain when ready).
- Visual treatment for runs with multiple concurrent paused subagents (UI sketch, then ship).
- Audit row distinct from `approval_request` / `mcp_auth_required` (they already audit; doubling adds noise).
- Backfill of pre‑PR runs (events are immutable; archive reads keep their old state).

---

## References

- [`docs/new-design/pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md), [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md), [`pr-3.2.3-subagent-backend-completion.md`](./pr-3.2.3-subagent-backend-completion.md), [`pr-3.2.4-fleet-nests-compact-rows.md`](./pr-3.2.4-fleet-nests-compact-rows.md).
- [`services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py`](../../services/ai-backend/src/agent_runtime/execution/atlas_task_tool.py) — Phase 1 implementation.
- [`services/ai-backend/src/runtime_worker/stream_parts.py`](../../services/ai-backend/src/runtime_worker/stream_parts.py), [`stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py), [`stream_events.py`](../../services/ai-backend/src/runtime_worker/stream_events.py), [`streaming_executor.py`](../../services/ai-backend/src/runtime_worker/streaming_executor.py) — runtime worker surface.
- [`services/ai-backend/.venv/lib/python3.13/site-packages/deepagents/middleware/subagents.py`](../../services/ai-backend/.venv/lib/python3.13/site-packages/deepagents/middleware/subagents.py) — upstream `_build_task_tool` we mirror.
