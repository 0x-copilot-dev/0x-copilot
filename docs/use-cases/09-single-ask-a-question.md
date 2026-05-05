# 09. Single ask-a-question approval

> Status: documented · Layers: ai-backend (worker, api, persistence), backend-facade, Frontend · Related: 06, 10

## Trigger

Mid-execution, the agent emits a single `ask_a_question` interrupt — a free-form question with optional structured `options`, `multi_select`, `allow_free_text`, `header`, and `hint`. The run pauses at the LangGraph interrupt; the user picks an option and/or types a free-text answer; the agent resumes.

This is **not** a permission gate. The vocabulary is `answered`/`skipped`, not `approved`/`rejected`. That special-case is preserved end-to-end (worker resume payload, API wire status, FE reducer).

## Preconditions

- Run is `RUNNING` and an SSE stream is attached for that `run_id`.
- The agent's graph node yields an `ask_a_question` interrupt with at least `approval_id` and `question`.

## Sequence diagram

```mermaid
sequenceDiagram
    actor User
    participant FE as Browser (ChatScreen)
    participant Facade as backend-facade
    participant AI as ai-backend (api)
    participant Worker as runtime_worker
    participant Store as event store / DB

    Worker->>Worker: graph yields ask_a_question interrupt
    Worker->>Store: create_approval_request (INSERT … ON CONFLICT DO NOTHING, H2)
    Worker->>Store: append_api_event APPROVAL_REQUESTED (seq N)
    Worker->>Store: update_run_status WAITING_FOR_APPROVAL
    Store-->>FE: SSE event APPROVAL_REQUESTED (projected payload)
    FE->>FE: upsertApprovalPart → message status=requires-action
    User->>FE: picks option / types answer
    FE->>Facade: POST /v1/agent/approvals/{id}/decision (decision, answer)
    Facade->>AI: forward with org_id + decided_by_user_id
    AI->>Store: record_approval_decision (decision row)
    AI->>Store: append_api_event APPROVAL_RESOLVED (status=answered/skipped)
    AI->>Worker: enqueue RuntimeApprovalResolvedCommand
    AI-->>FE: 200 ApprovalDecisionResponse
    FE->>FE: resolveApprovalDecision (status=answered, presentation=null)
    Worker->>Worker: rebuild harness, astream_runtime_resume(resume payload)
    Worker->>Store: continues emitting events; eventually run_completed
```

## Function trace

1. **Worker emit.** `RuntimeRunHandler` runs the graph; on a yielded interrupt, `StreamOrchestrator.append_native_interrupt_events` returns `True` and `result` becomes `{action_required: True}` ([run.py:200-218](../../services/ai-backend/src/runtime_worker/handlers/run.py#L200-L218); `_is_action_interrupt` at [run.py:810-819](../../services/ai-backend/src/runtime_worker/handlers/run.py#L810-L819)). The same orchestrator step writes the approval row via `create_approval_request` ([stream_events.py:248-272](../../services/ai-backend/src/runtime_worker/stream_events.py#L248-L272)) and appends the `APPROVAL_REQUESTED` event with monotonic `sequence_no`. `ask_a_question` deliberately does **not** carry `source_tool_call_id` ([stream_events.py:241-246](../../services/ai-backend/src/runtime_worker/stream_events.py#L241-L246)), so it never displaces a sibling tool bubble. Status flips to `WAITING_FOR_APPROVAL`; the LangGraph executor pauses at the checkpoint.
2. **Persistence H2.** `PostgresRuntimeApiStore.create_approval_request` — [runtime_api_store.py:807-880](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L807-L880); design in module docstring [runtime_api_store.py:1-16](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L1-L16) — `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`, then fallback `SELECT a.*, r.conversation_id, r.user_id FROM runtime_approval_requests a JOIN agent_runs r …` if the row already exists. Idempotent on `approval_id`. `ApprovalRequestRecord` ([approvals.py:63-74](../../services/ai-backend/src/runtime_api/schemas/approvals.py#L63-L74)) has `status=PENDING` and the full event payload as `metadata`.
3. **Event projection.** `RuntimeEventPresentationProjector.payload_for_event` routes `APPROVAL_REQUESTED` to `_approval_requested_payload`, which dispatches to `_ask_a_question_requested_payload` for `approval_kind=="ask_a_question"` — [events.py:441-503](../../services/ai-backend/src/runtime_api/schemas/events.py#L441-L503). The question-specific projector preserves `header`/`question`/`hint`/`options`/`multi_select`/`allow_free_text` (the narrower default allow-list strips them); bare-string options are coerced to `{label}` via `_safe_question_options`.
4. **FE render.** `applyRuntimeEvent` routes `approval_requested` to `upsertApprovalPart` — [eventReducer.ts:57-62](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L57-L62). `upsertApprovalPart` ([contentBuilders.ts:114-130](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L114-L130)) builds the part via `approvalPart` ([partFactories.ts:188-200](../../apps/frontend/src/features/chat/chatModel/partFactories.ts#L188-L200)): `toolName="approval_request"`, `toolCallId=approval_id`, `args={…payload, status:"waiting", presentation}`. With no `source_tool_call_id` and no `mcp_auth` fallback match, `replaceToolCallPart`/`replaceFirstMatchingToolPart` ([contentBuilders.ts:150-187](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L150-L187)) appends a fresh part. Message status flips to `{type:"requires-action", reason:"interrupt"}`; `nextMessageStatus` preserves it while `hasPendingAction(content)` is true — [status.ts:45-85](../../apps/frontend/src/features/chat/chatModel/status.ts#L45-L85) — i.e. any `approval_request`/`mcp_auth_required` part with `result === undefined`.
5. **User answers → HTTP.** Runtime's `onResumeToolCall` fires `onApprovalDecision(approval_id, decision, answer)` — [ChatScreen.tsx:813-824](../../apps/frontend/src/features/chat/ChatScreen.tsx#L813-L824). `onApprovalDecision` ([ChatScreen.tsx:577-604](../../apps/frontend/src/features/chat/ChatScreen.tsx#L577-L604)) guards via `pendingApprovalDecisionsRef.current.has(approvalId)`, awaits `decideApproval`, then optimistically calls `resolveApprovalDecision`. `decideApproval` POSTs `/v1/agent/approvals/{id}/decision?org_id=…` with `ApprovalDecisionRequest{decision, decided_by_user_id, answer?}` — [agentApi.ts:193-225](../../apps/frontend/src/api/agentApi.ts#L193-L225); wire shape at [api-types/index.ts:700-745](../../packages/api-types/src/index.ts#L700-L745).
6. **Facade & AI route.** Facade — [backend-facade/app.py:525-539](../../services/backend-facade/src/backend_facade/app.py#L525-L539) — re-derives identity and overrides `decided_by_user_id` with the verified session value. AI route — [routes.py:246-266](../../services/ai-backend/src/runtime_api/http/routes.py#L246-L266) — overrides again from internal-service identity if present, then dispatches to `service.record_approval_decision`. Caller-supplied identity is never trusted.
7. **Record + enqueue.** `RuntimeApiService.record_approval_decision` — [service.py:512-607](../../services/ai-backend/src/agent_runtime/api/service.py#L512-L607) — loads `approval` (404 if missing), enforces `approval.user_id == request.decided_by_user_id` (403 otherwise), persists `ApprovalDecisionRecord` ([approvals.py:48-60](../../services/ai-backend/src/runtime_api/schemas/approvals.py#L48-L60)), appends `APPROVAL_RESOLVED`, enqueues `RuntimeApprovalResolvedCommand` ([commands.py:38-47](../../services/ai-backend/src/runtime_api/schemas/commands.py#L38-L47)), writes audit log `event_type="approval_decision_recorded"`. Wire-level status uses `_wire_status_for` — [service.py:609-627](../../services/ai-backend/src/agent_runtime/api/service.py#L609-L627) — translating persisted `approved`/`rejected` → SSE `answered`/`skipped` for `ask_a_question`. The decision row keeps canonical `approved`/`rejected`; the SSE payload also carries the raw `decision`.
8. **FE resolve.** `resolveApprovalDecision` — [chatModel/approval.ts:12-74](../../apps/frontend/src/features/chat/chatModel/approval.ts#L12-L74) — `ask_a_question` branch sets `args.status` ← `"answered"`/`"skipped"`, `args.presentation` ← `null` (clears the "Waiting for permission" snapshot so the card UI can render its resolved fallback copy), `result` ← `{approval_id, status, decision, answer}`. Setting `result` is what flips `hasPendingAction` to `false`. The later SSE `APPROVAL_RESOLVED` routes to `resolveActionFromPayload` → `resolveQuestionFromPayload` ([eventReducer.ts:63-65](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L63-L65); [chatModel/approval.ts:76-154](../../apps/frontend/src/features/chat/chatModel/approval.ts#L76-L154)) and re-applies the same shape with server-authoritative values. Idempotent.
9. **Worker resume.** `RuntimeApprovalHandler.handle` — [runtime_worker/handlers/approval.py:90-179](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L90-L179) — loads `run`+`approval`, validates `approval.run_id == command.run_id`, audit-emits the decision, builds `_resume_payload` ([approval.py:255-286](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L255-L286)) — for `ask_a_question` that's `{approval_id, decision, answer}`. Sets run `RUNNING`, rebuilds harness via `agent_factory(context, dependencies)`, calls `astream_runtime_resume(harness, resume)`. The user's free-text answer flows back through the resume value only — **not** appended as a separate USER message ([approval.py:131-135](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L131-L135)). If resume hits another interrupt, status returns to `WAITING_FOR_APPROVAL` ([approval.py:155-162](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L155-L162)) — see use case 10. Otherwise `_complete_run_with_result` appends `FINAL_RESPONSE`+`RUN_COMPLETED` and persists the assistant message. Any exception → `RUN_FAILED` and re-raise.

## Runtime events emitted

| Sequence | Event type                                    | Activity kind   | approval_id | Notes                                                                                                                   |
| -------: | --------------------------------------------- | --------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------- |
|        N | `approval_requested`                          | `approval`      | A           | Projected via `_ask_a_question_requested_payload`.                                                                      |
| (paused) |                                               |                 |             | Run is `WAITING_FOR_APPROVAL`; LangGraph checkpoint pinned.                                                             |
|        M | `approval_resolved`                           | `approval`      | A           | `status="answered"` or `"skipped"`; `decision` carries raw `"approved"`/`"rejected"`; `approval_kind="ask_a_question"`. |
|     M+1… | `model_delta`, `tool_call_*`, `subagent_*`, … | various         | —           | Resume stream.                                                                                                          |
| terminal | `final_response` + `run_completed`            | `message`/`run` | —           |                                                                                                                         |

## State changes

- `runtime_approval_requests`: one row, `status=pending`, idempotent on `id` (H2). `metadata` = full event payload.
- `runtime_approval_decisions`: one row, `status=approved|rejected`, `decided_by_user_id`, `answer`, `decided_at`.
- `agent_runs.status`: `RUNNING` → `WAITING_FOR_APPROVAL` → `RUNNING` → terminal.
- `runtime_events`: `approval_requested`, `approval_resolved`, plus the resume stream. `latest_sequence_no` advanced monotonically (H3).
- Outbox / queue: one `RuntimeApprovalResolvedCommand`, claimed and acked exactly once.
- Audit: API-side `approval_decision_recorded`; worker-side `audit_emitter.emit_approval_decision`.
- React state: assistant message gains an `approval_request` tool-call part with `args.status="waiting"` then flips to `"answered"`/`"skipped"` with `result` set. Message status `running` → `requires-action` → `running`.
- Refs: `pendingApprovalDecisionsRef.current.add(approvalId)` on click, `delete` in `finally` ([ChatScreen.tsx:582-603](../../apps/frontend/src/features/chat/ChatScreen.tsx#L582-L603)).

## Edge cases handled

- **Duplicate `APPROVAL_REQUESTED` emission**: H2 idempotent insert; `StreamOrchestrator.create_approval_request` also pre-checks `get_approval_request`.
- **Double-click on the answer button**: `pendingApprovalDecisionsRef` short-circuits before any HTTP call.
- **Caller-supplied `decided_by_user_id`**: facade overrides with verified identity ([app.py:531-538](../../services/backend-facade/src/backend_facade/app.py#L531-L538)); AI route overrides for internal callers ([routes.py:254-259](../../services/ai-backend/src/runtime_api/http/routes.py#L254-L259)); service layer also enforces `approval.user_id == decided_by_user_id` (403 on mismatch).
- **Vocabulary**: `_wire_status_for` translates only the SSE payload status — persisted decision row keeps `approved`/`rejected` as the canonical record. FE `resolveApprovalDecision` and `resolveQuestionFromPayload` both honor `ask_a_question` per part.
- **Optimistic FE update vs. SSE `APPROVAL_RESOLVED`**: `resolveQuestionFromPayload` is idempotent — a late event re-applies the same shape.
- **No `source_tool_call_id`**: `ask_a_question` is a free-standing interrupt and intentionally appends a fresh card instead of displacing the most recent tool bubble.
- **Resume failure**: any exception flips run to `FAILED` and emits `RUN_FAILED` before re-raising ([approval.py:165-179](../../services/ai-backend/src/runtime_worker/handlers/approval.py#L165-L179)).

## Known gaps / TODOs

- `pendingApprovalDecisionsRef` is **never** cleared on unmount. `finally` self-drain handles in-flight ids, but per [CLAUDE.md → Compliance Reviews](../../CLAUDE.md) a `useEffect(() => () => pendingApprovalDecisionsRef.current.clear(), [])` is worth adding.
- `ApprovalRequestRecord.expires_at` exists in schema but no sweep job rejects stale rows.
- `RuntimeApprovalResolvedCommand` consumer has no retry budget — a poisoned command (e.g., `agent_factory` raises permanently) flips the run to `FAILED` on first attempt.
- The `answer` is recoverable from `runtime_approval_decisions.answer`, **not** from `messages` — replay/UI rendering must read the decisions table.

## References

- Worker emit: [run.py:200-218](../../services/ai-backend/src/runtime_worker/handlers/run.py#L200-L218); [stream_events.py:248-272](../../services/ai-backend/src/runtime_worker/stream_events.py#L248-L272)
- Persistence H2: [runtime_api_store.py:1-16, 807-880](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L807-L880)
- API record + enqueue: [service.py:512-627](../../services/ai-backend/src/agent_runtime/api/service.py#L512-L627)
- Resume: [runtime_worker/handlers/approval.py](../../services/ai-backend/src/runtime_worker/handlers/approval.py)
- Projection: [events.py:441-503](../../services/ai-backend/src/runtime_api/schemas/events.py#L441-L503)
- Facade: [backend-facade/app.py:525-539](../../services/backend-facade/src/backend_facade/app.py#L525-L539)
- FE: [eventReducer.ts:57-65](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L57-L65); [contentBuilders.ts:114-187](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L114-L187); [partFactories.ts:188-200](../../apps/frontend/src/features/chat/chatModel/partFactories.ts#L188-L200); [chatModel/approval.ts:12-154](../../apps/frontend/src/features/chat/chatModel/approval.ts#L12-L154); [status.ts:74-85](../../apps/frontend/src/features/chat/chatModel/status.ts#L74-L85); [agentApi.ts:193-225](../../apps/frontend/src/api/agentApi.ts#L193-L225)
- Contracts: [api-types/index.ts:700-745](../../packages/api-types/src/index.ts#L700-L745); [approvals.py:24-83](../../services/ai-backend/src/runtime_api/schemas/approvals.py#L24-L83); [commands.py:38-47](../../services/ai-backend/src/runtime_api/schemas/commands.py#L38-L47)
- Related: [10 — Two ask-a-questions in a row](10-multiple-ask-a-questions-queued.md); 06 (MCP auth approval)
