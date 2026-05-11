# Refactor PRD — Worker streaming pipeline cleanup (P15)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §5.1](../architecture/refactor-audit.md#51-worker-side-toolcallledger-duplicates-persistence-side-toolinvocationstoreport), [§5.2](../architecture/refactor-audit.md#52-approvalrecognisers-in-the-worker), [§5.3](../architecture/refactor-audit.md#53-streaming-pipeline--10-files-inside-the-worker)
**Phase:** 4 — Targeted decoupling
**Roadmap entry:** [`00-roadmap.md` → P15](00-roadmap.md)

---

## 1. Problem

The worker's streaming pipeline is **10 files in [`runtime_worker/`](../../src/runtime_worker/)**:

| File                                                                          | Stated role per [C2](../architecture/03-runtime-worker.puml)                     |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [`streaming_executor.py`](../../src/runtime_worker/streaming_executor.py)     | Drives LangGraph stream; short-circuits on `action_interrupt_events`             |
| [`stream_events.py`](../../src/runtime_worker/stream_events.py)               | `StreamOrchestrator` — fans chunks to per-channel handlers                       |
| [`stream_messages.py`](../../src/runtime_worker/stream_messages.py)           | Channel handler — model deltas, final responses                                  |
| [`stream_parts.py`](../../src/runtime_worker/stream_parts.py)                 | Channel handler — `RuntimeStreamPartAdapter` (LangGraph v2 `StreamPart` parsing) |
| [`stream_subagents.py`](../../src/runtime_worker/stream_subagents.py)         | Channel handler — subagent events                                                |
| [`stream_tools.py`](../../src/runtime_worker/stream_tools.py)                 | Channel handler — tool call / result events                                      |
| [`tool_call_ledger.py`](../../src/runtime_worker/tool_call_ledger.py)         | Worker-side derived state of tool calls                                          |
| [`tool_observations.py`](../../src/runtime_worker/tool_observations.py)       | Worker-side derived state of tool observations                                   |
| [`approval_recognisers.py`](../../src/runtime_worker/approval_recognisers.py) | Pattern recognition on the stream to spot approval requests                      |
| [`run_metrics.py`](../../src/runtime_worker/run_metrics.py)                   | `AssistantRunMetrics` + `TokenUsageExtractor`                                    |

The 10 files contain three problems that ride together. The audit broke them into three findings; this PRD treats them as one PR because they touch the same call sites and tests.

### Problem 1 — `ToolCallLedger` duplicates persistence

The persistence layer already has [`ToolInvocationRecord`](../../src/agent_runtime/persistence/records/tools.py) plus a port that writes it. `ToolCallLedger` keeps a parallel in-memory tracker of "what tool calls happened in this run." When they disagree (worker crash + restart, mid-stream cancel, retry) the ambiguity is undocumented.

The DB-side record survives worker restart. The in-memory ledger does not. Production already trusts the DB on resume. The in-memory ledger exists for fast lookup during a single stream session — that's a cache, not a ledger, and naming it "ledger" implies authority it doesn't have.

### Problem 2 — `ApprovalRecognisers` should not exist

`APPROVAL_REQUESTED` and `MCP_AUTH_REQUIRED` are first-class typed events in [`RuntimeApiEventType`](../../src/runtime_api/schemas/events.py). They are part of `StreamingExecutor.action_interrupt_events`. The streaming executor short-circuits on them.

If they are typed events, they should be **emitted as typed events at the source** — by the tool / MCP middleware that initiates the request. Today, `approval_recognisers.py` exists because LangGraph emits raw chunks and the worker has to **pattern-match** them back into typed approval events. That translation lives one layer too far downstream.

The right place to emit `APPROVAL_REQUESTED` is `CallMcpTool` / `AuthMcpTool` (per [f8](../architecture/f8-mcp-auth.puml)). The right place to emit `MCP_AUTH_REQUIRED` is `AuthMcpTool` itself. The recognizer is rebuilding what the producers already know.

### Problem 3 — channel-handler files may be over-split

`stream_messages.py`, `stream_parts.py`, `stream_subagents.py`, `stream_tools.py` are four files for "translate a `StreamPart` chunk into a `RuntimeEventEnvelope`." The split is reasonable if each handler is non-trivial. It may be five lines of switch-case spread across four files. Verify before merging — the split could be the right shape.

### Symptoms (today)

- Two sources of truth for "did tool X get called in run Y" (in-memory ledger + DB record).
- Approval-event emission is implicit (recognized from patterns) rather than explicit (emitted at source).
- Adding a new approval-style interrupt requires changing the recognizer; adding a new MCP auth flavor requires updating the recognizer's pattern set; both are far from where the new flow is implemented.
- New developers have to read 10 files to understand "what happens when a tool call streams through."

### What this is NOT

- Not a behavior change. Every observable behavior in [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved) survives.
- Not a queue / handler-dispatch change. `RuntimeWorker._dispatch` still routes 3 command types to 3 handlers.
- Not an interrupt-semantics change. `action_interrupt_events = {APPROVAL_REQUESTED, MCP_AUTH_REQUIRED}` continues to short-circuit the loop.
- Not a `RuntimeApprovalHandler` change. Resume-via-`APPROVAL_RESOLVED` continues to drive the executor.

---

## 2. Goal and non-goals

### Goal

Three coherent outcomes in one PR:

1. **Move typed approval-event emission to the source.** `CallMcpTool` / `AuthMcpTool` / any other approval-emitter calls `RuntimeEventProducer.append_api_event(APPROVAL_REQUESTED | MCP_AUTH_REQUIRED, …)` directly. **Delete `approval_recognisers.py`.** The streaming executor still observes `action_interrupt_events` to decide when to short-circuit, but it now sees them as already-typed events from the producer, not as patterns it has to recognize.

2. **Demote `ToolCallLedger` to a per-stream cache, or delete it entirely.** Persistence (`ToolInvocationStorePort`) is the single source of truth for tool calls. The worker either:
   - (a) reads on demand from the port; OR
   - (b) keeps a strictly in-memory cache scoped to the current stream session, rebuilt from the port on resume. In neither case does it carry state across worker restart.

3. **Investigate per-channel handler files; collapse where appropriate.** Goal is 2–3 files (`stream_orchestrator.py` + `stream_handlers.py` + `run_metrics.py`) unless verified that the split carries weight.

### Non-goals

- Do not change the LangGraph integration in `streaming_executor.py` beyond the approval-emission rewiring.
- Do not change `RuntimeApprovalHandler` or any of the 3 worker handlers.
- Do not change `RuntimeWorker._dispatch` or its 3-command-type contract.
- Do not change any event payload shape on the wire.
- Do not change `ToolInvocationStorePort` or `ToolInvocationRecord`.
- Do not change `tool_observations.py` until it's understood (see §10 pre-implementation checklist).

### Success criteria

- `approval_recognisers.py` deleted.
- `tool_call_ledger.py` either deleted, or reduced to a clearly-named in-memory cache (`tool_call_cache.py` / `tool_call_view.py`) with a docstring that says "rebuilt from `ToolInvocationStorePort` on resume."
- Channel-handler file count: 2–3, not 4.
- All `APPROVAL_REQUESTED` / `MCP_AUTH_REQUIRED` events are emitted by named producers (grep proves it: every emission site is in `capabilities/mcp/middleware/` or equivalent capability code, not in `runtime_worker/`).
- `tests/integration/runtime_worker/` covers: cancel mid-stream emits one extra `MODEL_DELTA` then `RUN_CANCELLED`; MCP auth interrupt pauses run and writes approval row; approval-resolved resumes from checkpoint.
- Worker-folder LOC count down by ≥ 30% for the streaming pipeline subset.

---

## 3. Systems touched

This is the expected shape derived from diagrams + index. Read the actual files first; this list is the planning shape, not a contract.

### 3.1 Files deleted

| File                                                               | Reason                                                                                |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `runtime_worker/approval_recognisers.py`                           | Replaced by source-emission of typed approval events                                  |
| `runtime_worker/tool_call_ledger.py` (if cache replacement chosen) | Replaced by `ToolInvocationStorePort` reads, optionally with a per-session view cache |

### 3.2 Files merged

| From                                                           | Into                                                                          |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `stream_messages.py`, `stream_subagents.py`, `stream_tools.py` | `runtime_worker/stream_handlers.py` (one module, per-channel functions)       |
| `stream_parts.py` (LangGraph `StreamPart` parsing)             | Stays as `stream_part_adapter.py` — it's adapter logic, not a channel handler |
| `stream_events.py` (`StreamOrchestrator`)                      | Stays as `stream_orchestrator.py` (rename for clarity)                        |

The split between `stream_part_adapter.py` (parses raw LangGraph chunks) and `stream_handlers.py` (decides what `RuntimeApiEventType` to emit) maps to what's actually two different concerns.

### 3.3 Files unchanged

- [`runtime_worker/streaming_executor.py`](../../src/runtime_worker/streaming_executor.py) — removes the recognizer call path; otherwise unchanged.
- [`runtime_worker/run_metrics.py`](../../src/runtime_worker/run_metrics.py) — unchanged.
- [`runtime_worker/loop.py`](../../src/runtime_worker/loop.py) — unchanged.
- All three handler files under `runtime_worker/handlers/` — unchanged.
- [`runtime_worker/tool_observations.py`](../../src/runtime_worker/tool_observations.py) — verify in §10 before deciding.

### 3.4 Files updated (call-site only)

- `capabilities/mcp/middleware/call_tool.py` — emit `APPROVAL_REQUESTED` directly.
- `capabilities/mcp/middleware/auth_mcp.py` — emit `MCP_AUTH_REQUIRED` directly.
- Any other producer that today implicitly trips a recognizer — grep for the recognized patterns and emit them at source.

---

## 4. Architecture

### 4.1 Approval-event emission contract

**Today** (implicit):

```
tool middleware → graph chunk → StreamingExecutor → ApprovalRecognisers
  → "I see a pattern matching APPROVAL_REQUESTED"
  → emit typed event
```

**After** (explicit):

```
tool middleware → emit APPROVAL_REQUESTED directly via RuntimeEventProducer
  → graph chunk continues to StreamingExecutor
  → StreamingExecutor sees the typed event in its incoming stream
  → action_interrupt_events check matches
  → short-circuit
```

The `action_interrupt_events` set stays. The recognizer goes away because the events are already typed by the time the executor sees them.

### 4.2 Tool call source-of-truth contract

**Single source.** `ToolInvocationStorePort` writes during `TOOL_CALL` / `TOOL_RESULT` event emission. The worker reads on demand via the port. If a per-session view cache is kept, it is:

- Strictly in-memory.
- Bounded to the current stream's run_id.
- Rebuilt from `ToolInvocationStorePort.list_for_run` on session start (resume / recover scenarios).
- Named to reflect cache semantics (not "ledger").

### 4.3 Channel-handler shape

Either:

- **One file `stream_handlers.py`** with a function per channel (`handle_messages_part`, `handle_subagents_part`, `handle_tools_part`) and a small dispatch in the orchestrator. Best if each handler is < 50 LOC.
- **Three files retained** if each handler is non-trivial. The PRD does not insist on the merge; it insists on the investigation. **Whichever shape lands, name the dispatch path so a new reader can find it in one grep.**

---

## 5. Edge cases

Behaviors from [`refactor-audit.md` § Behaviors that must be preserved](../architecture/refactor-audit.md#behaviors-that-must-be-preserved) and the relevant flows:

1. **Cancel mid-stream** ([f4](../architecture/f4-cancel.puml)).
   Run is RUNNING with model chunks in flight. User POSTs cancel. `RuntimeCancelHandler` updates status. Active run handler observes status next loop tick. **One extra `MODEL_DELTA` may arrive** between `RUN_CANCELLING` and the loop's next status check. This race is acceptable and documented. New code must not regress this — the executor must continue to drain the in-flight chunk.

2. **MCP auth interrupt** ([f8](../architecture/f8-mcp-auth.puml)).
   Tool call against unauth'd server → `AuthMcpTool` → emits `MCP_AUTH_REQUIRED` directly (new path) → executor sees event in `action_interrupt_events` → ends stream, writes approval row, run goes `AWAITING_APPROVAL`. Queue claim is **NOT** marked complete. Resume via `APPROVAL_RESOLVED` command picks up the run.

3. **Multi-fire approvals.** Token rotation mid-run fires the approval cycle a second time. Each emission must be from the source middleware, not from a recognizer. Each must produce a fresh approval row.

4. **Run completion ordering.** `FINAL_RESPONSE` → `RUN_COMPLETED` order is preserved. Both events emit before `queue.mark_complete(claim)`.

5. **Subagent handoff stream.** Subagent fleet events (`SUBAGENT_FLEET_STARTED`, `SUBAGENT_STARTED`, etc.) carry `parent_task_id` linkage. Handler consolidation must not lose this linkage.

6. **Tool budget short-circuit.** `ToolBudgetMiddleware` continues to short-circuit past per-task cap with `BUDGET_WARNING`. Any source-emission rewiring stays out of the budget path.

7. **Worker restart resume.** A run that was paused on `MCP_AUTH_REQUIRED` before this refactor lands, and is resumed after it lands, must continue cleanly. The approval row carries the contract; the recognizer's removal does not affect that.

8. **Token-usage extraction.** `TokenUsageExtractor.extract` continues to populate `MODEL_CALL_COMPLETED.payload.usage` with the full breakdown (`input_tokens`, `output_tokens`, `cached_input`, `reasoning_tokens`).

9. **Cooperative cancel during approval-resume.** A run that's been cancelled while paused on approval — the resume path observes `status=CANCELLED` and exits cleanly without re-running the model.

---

## 6. Security considerations

- Approval-event source emission must continue to attach a redacted payload. The Pydantic field validators on `RuntimeEventEnvelope.payload` and `.metadata` run regardless of who emits — moving the emission point does not bypass redaction.
- Caller identity on emitted events: `event_id`, `parent_event_id`, `span_id`, `trace_id` continue to flow from the calling tool's context. Tool middleware must construct these correctly when emitting directly. Today's recognizer constructed them; producers must do the same.
- Permission gating on the tool call that triggers an approval is unchanged. `McpPermissionPolicy` denies first, `AuthMcpTool` emits `MCP_AUTH_REQUIRED` second.

---

## 7. Observability

Same events, same payloads, same emitters from the wire's perspective. The producer changes; consumers (SSE adapter, persistence, frontend) see no difference.

If `ApprovalRecogniser` had any internal logging or metrics today (e.g. "recognized X approval pattern"), those go away. Replace with a single span around `RuntimeEventProducer.append_api_event` at the new emission sites — already standard practice.

---

## 8. Risks

| Risk                                                                                                 | Mitigation                                                                                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A pattern recognized today by `ApprovalRecognisers` has multiple latent producers we don't enumerate | Pre-implementation grep: list every chunk shape the recognizer pattern-matches. For each, identify the producer. If a producer is unknown, halt and investigate before proceeding                                                                         |
| Approval emission from middleware races with the executor's `action_interrupt_events` check          | The executor consumes events through the producer in the same async context. Order is preserved by the event loop. Add a focused integration test: emit `MCP_AUTH_REQUIRED` mid-stream, assert executor short-circuits before the next chunk is processed |
| Channel-handler merge accidentally drops a per-channel quirk                                         | Merge as a no-op refactor first (move code, don't change), run the suite, then collapse. Keep PR atomic but commit-by-commit reviewable                                                                                                                   |
| Worker-side ledger removal breaks a code path that quietly relied on its in-memory state             | Grep all readers of `ToolCallLedger` before deletion. Each reader must be reviewed; many are likely test-only. Production readers must move to `ToolInvocationStorePort`                                                                                  |
| `tool_observations.py` purpose unknown                                                               | §10 pre-implementation checklist requires reading and documenting it before this PR. If it carries derived state we don't understand, it stays in this PR's scope; if it's unrelated, it stays unchanged                                                  |
| Source-emission introduces a circular import (capability → producer → schemas → capability)          | If found, break with a small `RuntimeEventProducerProtocol` that capabilities import; producer satisfies it. Standard fix                                                                                                                                 |

---

## 9. Unit testing requirements

### 9.1 New tests

1. **Source-emission test for `APPROVAL_REQUESTED`.** Construct a `CallMcpTool` invocation that triggers approval. Assert `RuntimeEventProducer.append_api_event` is called with `event_type=APPROVAL_REQUESTED` and the right payload.
2. **Source-emission test for `MCP_AUTH_REQUIRED`.** Same, for `AuthMcpTool`.
3. **Executor short-circuit on typed event.** Feed a synthetic stream containing `APPROVAL_REQUESTED` directly; assert `StreamingExecutor` exits the loop with `action_interrupted=True`.
4. **Approval persistence on interrupt.** End-to-end: tool middleware emits `MCP_AUTH_REQUIRED` → executor short-circuits → approval row written, run status `AWAITING_APPROVAL`, queue claim NOT marked complete.
5. **Resume after approval.** `APPROVAL_RESOLVED` command claimed → run resumes → tool retried → `TOOL_CALL` + `TOOL_RESULT` flow normally.
6. **No recognizer references.** `git grep "ApprovalRecognisers" services/ai-backend/` returns zero hits after the PR.
7. **Tool source-of-truth test.** After a run, `ToolInvocationStorePort.list_for_run` returns every tool call. The worker has no other persisted record.
8. **Cancel mid-stream still allows one extra `MODEL_DELTA`.** Documented behavior; pin a test that asserts one delta arrives between `RUN_CANCELLING` and `RUN_CANCELLED`.

### 9.2 Existing tests touched

- All tests in `tests/unit/runtime_worker/` — update imports to the merged channel-handler module.
- Tests under `tests/unit/runtime_worker/test_approval_recognisers*.py` — delete; replaced by source-emission tests above.
- Integration tests for MCP auth flow (`tests/integration/mcp/`) — should pass without change; if they fail, the source-emission rewiring missed a case.

### 9.3 Tests deleted

- Anything that asserts on the recognizer's pattern set.
- Anything that asserts on the in-memory `ToolCallLedger`'s state independently of `ToolInvocationStorePort`.

---

## 10. Pre-implementation checklist

Run before writing code:

1. **Read and document `tool_observations.py`.** What state does it carry? Who consumes it? If it's another instance of derived state that should live closer to the producer, fold it into this PR's scope. If unrelated, leave it.
2. **Enumerate every recognizer pattern in `approval_recognisers.py`.** List the chunk shapes and map each to the producer that creates them. Verify every producer is in `capabilities/`.
3. **Grep all readers of `ToolCallLedger`.** Decide per reader whether it moves to the port or stays as a per-session cache.
4. **Verify `RuntimeEventProducer` is callable from MCP middleware without a circular import.** If not, plan the small protocol-extraction fix in §8.
5. **Enumerate `RuntimeApiEventType` values currently emitted by the recognizer vs. by direct producers.** Source-emission must cover the full set the recognizer covers today; partial migration is worse than the status quo.
6. **Capture a 50-run sample from staging covering: cancel mid-stream, MCP auth interrupt, approval resolution, multi-tool turn.** Replay through old and new code paths; diff event sequences.
7. **Confirm `RuntimeWorker._dispatch` does not depend on recognizer-side state.** It dispatches on `command_type`; recognizer-removal should not affect it.

---

## 11. Rollback plan

- Single PR; rollback = revert.
- No schema change.
- No queue-format change. In-flight queue items written before the PR remain processable after revert.
- Approval rows written by source-emission have the same shape as those written by the recognizer; rollback does not strand any in-flight approval.

---

## 12. Out of scope (handled by other PRDs)

- The ~1s SSE latency from the in-memory bus — see [P2 (`02-sse-listen-notify.md`)](00-roadmap.md).
- Per-event DB amplification — see [P4 (`04-event-write-consolidation.md`)](00-roadmap.md).
- Per-run sequence allocation replacing `SELECT FOR UPDATE` — see [P16 (`13-per-run-sequence.md`)](13-per-run-sequence.md).
- Citation-side duplication — see [P14 (`11-citations-consolidation.md`)](11-citations-consolidation.md).

---

_Per the team's spec-first workflow ([`docs/CLAUDE.md`](../CLAUDE.md)): do not start implementation until §10 is complete and this PRD is reviewed. If §10 step 1 reveals `tool_observations.py` is not derived state, update §3.3 to leave it out and re-scope this PR._
