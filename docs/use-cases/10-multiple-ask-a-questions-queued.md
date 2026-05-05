# 10. Two ask-a-questions in a row

> Status: documented · Layers: ai-backend (worker, api), backend-facade, Frontend · Related: 09, 06

## Trigger

The agent emits an `ask_a_question` interrupt; before the run terminates, **a second** `ask_a_question` is emitted on the same run. Two approval cards are present in the assistant message at once.

In practice "two interrupts in a row" almost always means: the user answered the first question, the worker resumed via `RuntimeApprovalHandler.handle`, and a downstream graph node yielded another `ask_a_question` interrupt during that resume. The handler then flips the run back to `WAITING_FOR_APPROVAL` ([approval.py:155-162](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L155-L162)) and the second `APPROVAL_REQUESTED` arrives on the still-open SSE stream — so the user sees the _first_ card resolving (`status="answered"`) and the _second_ card appearing.

It is also possible — but rare — for two `APPROVAL_REQUESTED` events to land in the **same SSE batch** before the user has answered either. That happens when a single graph step produces two pending interrupts in one tick. The reducer applies them in `sequence_no` order; the second one falls through `replaceToolCallPart`'s no-match branch and is appended as a fresh part.

Use case 09 is the foundation; this doc only calls out behavior **specific** to having two unresolved `ask_a_question` parts coexist.

## Preconditions

- Run is `RUNNING` (or briefly transitions back through `RUNNING` between the two interrupts).
- SSE stream is attached for `run_id`, or will be re-attached on reconnect with `?after_sequence=N`.
- Both interrupt payloads carry distinct `approval_id` values (same id is impossible — H2 idempotent insert).

## Sequence diagram

```mermaid
sequenceDiagram
    actor User
    participant FE as Browser (ChatScreen)
    participant AI as ai-backend (api)
    participant Worker as runtime_worker
    participant Store as event store / DB

    Worker->>Store: APPROVAL_REQUESTED A1 (seq N)
    Worker->>Store: status=WAITING_FOR_APPROVAL
    Store-->>FE: SSE A1 → upsertApprovalPart (card 1)

    User->>FE: answer A1
    FE->>AI: POST /approvals/A1/decision (via facade)
    AI->>Store: decision A1 + APPROVAL_RESOLVED A1
    AI->>Worker: enqueue RuntimeApprovalResolvedCommand A1
    AI-->>FE: 200; resolveApprovalDecision card 1 → "answered"

    Worker->>Worker: handle A1, rebuild harness, astream_runtime_resume
    Worker->>Store: model_delta / tool events …
    Worker->>Store: APPROVAL_REQUESTED A2 (seq M, M > N)
    Worker->>Store: status=WAITING_FOR_APPROVAL again
    Store-->>FE: SSE A2 → upsertApprovalPart (card 2 appended)

    User->>FE: answer A2
    Note over FE,AI: same path as A1; second resume continues run to terminal
```

## Function trace

### Two cards coexist (the reducer story)

1. SSE delivers `APPROVAL_REQUESTED` for `A2`. `applyRuntimeEvent` routes to `upsertApprovalPart` — [eventReducer.ts:57-62](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L57-L62).
2. `upsertApprovalPart` builds `nextPart = approvalPart(payload)` with `toolCallId = A2.approval_id` — [contentBuilders.ts:114-130](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L114-L130). Calls `replaceToolCallPart`. Match logic ([contentBuilders.ts:150-187](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L150-L187)):
   - `ask_a_question` payloads do not set `source_tool_call_id` ([stream_events.py:241-246](../../services/ai-backend/src/runtime_worker/stream_events.py#L241-L246)) → first branch `if (!toolCallId)` → `replaceFirstMatchingToolPart`.
   - `replaceFirstMatchingToolPart` calls `mcpApprovalMatchesWrapper` as `fallbackMatch`. That matcher matches `mcp_tool` approval wrappers, **not** `ask_a_question`.
   - With no match, it falls through to `upsertPart` — which appends a new tool-call part ([contentBuilders.ts:323-339](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L323-L339)).
3. Result: card 1 (`toolCallId=A1`) and card 2 (`toolCallId=A2`) are siblings in the same assistant message's `content`. Order is `sequence_no` order — card 1 first, card 2 below.

If card 1 has already been resolved by the time card 2 arrives (the typical resume case), card 1 has `args.status="answered"` and `result` set; card 2 has `args.status="waiting"` and `result === undefined`.

### Pending-action gating

`hasPendingAction(content)` returns `true` if **any** tool-call part with `toolName ∈ {"approval_request","mcp_auth_required"}` has `result === undefined` — [status.ts:74-85](../../apps/frontend/src/features/chat/chatModel/status.ts#L74-L85). With one resolved and one pending, the message stays `requires-action`. Only when **both** cards have `result` set does `nextMessageStatus` ([status.ts:45-54](../../apps/frontend/src/features/chat/chatModel/status.ts#L45-L54)) let the message drop back to `running`, then `complete` on `run_completed`.

`hasPendingActionForRun` in `applyRuntimeEvent` ([eventReducer.ts:82-84, 162-172](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L82-L84)) early-returns to suppress generic progress/tool/subagent updates while any approval is unresolved. `approval_requested` and `approval_resolved` are checked **before** that gate ([eventReducer.ts:57-65](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L57-L65)), so a second `APPROVAL_REQUESTED` is never suppressed by an outstanding first card.

### Decision dispatch with two cards

The runtime's `onResumeToolCall` ([ChatScreen.tsx:813-824](../../apps/frontend/src/features/chat/ChatScreen.tsx#L813-L824)) carries the `approval_id` from the specific card the user clicked, so the right card resolves regardless of order.

`onApprovalDecision` ([ChatScreen.tsx:577-604](../../apps/frontend/src/features/chat/ChatScreen.tsx#L577-L604)):

- `pendingApprovalDecisionsRef.current.has(approvalId)` is **per-id**, not global. Two different `approval_id` values can each have one in-flight decision concurrently. The ref deduplicates **same-id** double-clicks; it does **not** serialize across ids.
- `await decideApproval(...)` for A1 and A2 can be in flight simultaneously from the FE's perspective.

### Server-side ordering

- Both `record_approval_decision` calls run in the API process — [service.py:512-607](../../services/ai-backend/src/agent_runtime/api/service.py#L512-L607). Each writes its own `runtime_approval_decisions` row, appends its own `APPROVAL_RESOLVED` event, and enqueues its own `RuntimeApprovalResolvedCommand`.
- The durable queue serializes consumers per worker. If A1's resume is still streaming when A2's command is claimed, A2 waits until the worker is free. In practice this is moot for the typical case (A2 was _produced by_ A1's resume — A1 is already done by the time A2 even exists).
- For the rare same-batch case, the second decision's resume payload is computed against the persisted approval-row metadata; the harness is rebuilt on each resume, so there is no stale-checkpoint risk.

### Vocabulary stays per-card

Each card carries its own `args.approval_kind`. `resolveApprovalDecision` ([chatModel/approval.ts:12-74](../../apps/frontend/src/features/chat/chatModel/approval.ts#L12-L74)) branches per part, so mixing an `ask_a_question` card with an `mcp_auth_required` card in the same message is well-defined: `answered`/`skipped` for the first, `approved`/`rejected` for the second.

## Runtime events emitted

| Sequence | Event type                         | Activity kind   | approval_id | Notes                                                        |
| -------: | ---------------------------------- | --------------- | ----------- | ------------------------------------------------------------ |
|        N | `approval_requested`               | `approval`      | A1          | First card.                                                  |
|      N+1 | `approval_resolved`                | `approval`      | A1          | `status="answered"` (or `"skipped"`).                        |
|  N+2…M-1 | resume stream                      | various         | —           | `model_delta`, `tool_call_*`, `subagent_*` from A1's resume. |
|        M | `approval_requested`               | `approval`      | A2          | Second card; appended (no `source_tool_call_id` match).      |
|      M+1 | `approval_resolved`                | `approval`      | A2          | After user answers second card.                              |
| terminal | `final_response` + `run_completed` | `message`/`run` | —           | Or yet another interrupt.                                    |

Same-batch variant (rare): `A1` and `A2` arrive at sequences `N` and `N+1` with no intervening resume traffic; reducer applies them in order, appending each as a fresh part.

## State changes

- Two `runtime_approval_requests` rows (`A1`, `A2`), each idempotent on `id` (H2).
- Two `runtime_approval_decisions` rows after both are answered.
- `agent_runs.status`: `RUNNING` → `WAITING_FOR_APPROVAL` → `RUNNING` → `WAITING_FOR_APPROVAL` → `RUNNING` → terminal.
- `runtime_events`: 4 approval events (`requested` × 2, `resolved` × 2) plus the two resume streams.
- Queue: two distinct `RuntimeApprovalResolvedCommand` rows, processed in enqueue order (the second blocks until the first resume returns from `astream_runtime_resume`).
- Audit: two `approval_decision_recorded` API rows ([service.py:590-601](../../services/ai-backend/src/agent_runtime/api/service.py#L590-L601)) and two worker-side `audit_emitter.emit_approval_decision` emissions.
- React state: assistant message holds **two** `approval_request` tool-call parts. Message stays `requires-action` until both `result`s are set.
- Refs: `pendingApprovalDecisionsRef` can hold **multiple** `approval_id`s simultaneously (one per in-flight decision); each is added on click and removed in `finally`.

## Edge cases handled

- **Same `sequence_no` collision is impossible**: per-run `UNIQUE(run_id, sequence_no)` and the `agent_runs` row lock for serialized appends (H1, [runtime_api_store.py:1-11](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L1-L11)).
- **Out-of-order answers**: each `decideApproval` round-trip is independent; `pendingApprovalDecisionsRef` is per-id; server scopes by `approval_id`. Out-of-order is fine.
- **Double-click on one card while another is in flight**: per-id guard short-circuits the double; the other card's in-flight decision is unaffected.
- **Optimistic resolve before SSE `APPROVAL_RESOLVED`**: `resolveQuestionFromPayload` ([chatModel/approval.ts:76-117](../../apps/frontend/src/features/chat/chatModel/approval.ts#L76-L117)) re-applies the same shape with server-authoritative values. Idempotent.
- **Same SSE batch (rare)**: events are persisted with monotonic `sequence_no`; FE applies them in order. The second always falls into the `replaceToolCallPart` no-match → append branch (no `mcp_auth` wrapper to match).
- **Worker resume produces two interrupts in one step**: `StreamOrchestrator.append_native_interrupt_events` writes both approval rows + two `APPROVAL_REQUESTED` events before `RuntimeRunHandler` flips status to `WAITING_FOR_APPROVAL` ([run.py:207-218](../../services/ai-backend/src/runtime_worker/handlers/run.py#L207-L218)). Two cards land at the FE in `sequence_no` order.
- **Vocabulary stays correct per-card**: `args.approval_kind` is checked independently in `resolveApprovalDecision`; an `ask_a_question` answer and a permission-gate approve in the same message do not cross-contaminate.

## Known gaps / TODOs

- **`pendingApprovalDecisionsRef` is never cleared on unmount.** Verified: there is no `useEffect` cleanup and no other reset path — only the per-id `delete` in `finally` ([ChatScreen.tsx:601-603](../../apps/frontend/src/features/chat/ChatScreen.tsx#L601-L603), [681](../../apps/frontend/src/features/chat/ChatScreen.tsx#L681)). With two concurrent in-flight decisions this is more visible: navigating away while both are pending leaves the ref holding two ids until each resolves.
- **Ordering hazard if a future tool ever reuses an `approval_id` as its `toolCallId`**: `replaceFirstMatchingToolPart`'s fallback would match. Today not exploitable — `approval_id` is a fresh `uuid4().hex` ([approvals.py:66](../../services/ai-backend/src/runtime_api/schemas/approvals.py#L66)).
- **Worker resume serialization**: if a run has two queued `RuntimeApprovalResolvedCommand`s and the first resume itself emits a third `APPROVAL_REQUESTED`, the second command resumes from a state that already has another pending interrupt. The harness reloads from the persisted `runtime_context` each time, so this works, but the user-visible card count can grow non-monotonically while the queue drains. There is no FE indicator for "queued resume in progress".
- **No FE telemetry for "user answered N approvals on this run"**. Per [CLAUDE.md → Compliance Reviews](../../CLAUDE.md), answer count and answer text are recoverable only from `runtime_approval_decisions`.
- **Same-batch rendering polish**: when two cards arrive in one SSE flush, both transitions to `requires-action` happen in one render — non-issue functionally; potential for a brief flash if presentation copy differs.

## References

- Reducer & part construction: [eventReducer.ts:57-65](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L57-L65); [contentBuilders.ts:114-187](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L114-L187); [partFactories.ts:188-200](../../apps/frontend/src/features/chat/chatModel/partFactories.ts#L188-L200)
- Pending-action gate: [status.ts:45-85](../../apps/frontend/src/features/chat/chatModel/status.ts#L45-L85); [eventReducer.ts:82-84, 162-172](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L82-L84)
- Per-card resolve: [chatModel/approval.ts:12-154](../../apps/frontend/src/features/chat/chatModel/approval.ts#L12-L154)
- Decision dispatch: [ChatScreen.tsx:577-604, 813-824](../../apps/frontend/src/features/chat/ChatScreen.tsx#L577-L604)
- Worker resume → second interrupt: [runtime_worker/handlers/approval.py:90-179](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L90-L179); [run.py:200-218](../../services/ai-backend/src/runtime_worker/handlers/run.py#L200-L218)
- API: [service.py:512-627](../../services/ai-backend/src/agent_runtime/api/service.py#L512-L627); [routes.py:246-266](../../services/ai-backend/src/runtime_api/http/routes.py#L246-L266)
- Facade: [backend-facade/app.py:525-539](../../services/backend-facade/src/backend_facade/app.py#L525-L539)
- Persistence: [runtime_api_store.py:807-880](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L807-L880)
- Foundation: [09 — Single ask-a-question approval](09-single-ask-a-question.md). Compare with 06 (MCP auth approval) for the permission-gate vocabulary.
