# 03. Clicking "New thread" while an interrupt is active

> Status: documented · Layers: Frontend (state-only) · Related: 04, 08

## Trigger

User clicks "New thread" (anything wired to `onSwitchToNewThread`) while the current conversation has either a streaming run (`activeRunId !== null`) or an open `requires-action` part — `approval_request` / `mcp_auth_required` with `result === undefined`, see [`hasPendingAction`](../../apps/frontend/src/features/chat/chatModel/status.ts#L74-L85).

## Preconditions

- `conversationId !== null` and either `activeRunId !== null` with `streamRef.current` open, or one or more assistant `ChatItem`s carry an unresolved approval/MCP-auth tool-call part.
- `pendingApprovalDecisionsRef.current` may hold approvalIds for in-flight `decideApproval` POSTs.

## Sequence diagram

```mermaid
sequenceDiagram
    actor User
    participant FE as ChatScreen
    participant AI as ai-backend (worker)
    Note over AI: Run R is still executing
    User->>FE: click "New thread"
    FE->>FE: streamRef.current.close(); reset 6 state slots
    Note over FE,AI: NO cancelRun POST is sent
    AI-->>AI: continues to emit events for run R (no listener)
```

## Function trace

1. `threadListAdapter.onSwitchToNewThread` — [ChatScreen.tsx:746](../../apps/frontend/src/features/chat/ChatScreen.tsx#L746) — wired directly to `onStartNewChat`.
2. `onStartNewChat` — [ChatScreen.tsx:553-562](../../apps/frontend/src/features/chat/ChatScreen.tsx#L553-L562) — closes SSE locally and resets six React state slots.
3. `EventSource.close()` — synchronous; no payload to the facade. The run continues server-side.
4. `cancelRun` — [agentApi.ts:178-191](../../apps/frontend/src/api/agentApi.ts#L178-L191) — **not called** here (only invoked from `onCancel` at [ChatScreen.tsx:541](../../apps/frontend/src/features/chat/ChatScreen.tsx#L541)).

## Runtime events emitted

_(none from the FE — flow is local state-only.)_

The abandoned run keeps appending events server-side (`model_delta`, `tool_call_*`, `subagent_*`, `final_response`, eventual `run_completed`/`run_failed`). Rows persist with their monotonic `sequence_no` and remain replayable via `GET /v1/agent/runs/{run_id}/events` if the conversation is reopened.

## State changes

State setters fired ([L554-561](../../apps/frontend/src/features/chat/ChatScreen.tsx#L553-L562)):

- `streamRef.current?.close()` then `streamRef.current = null` — SSE closed immediately, no graceful drain.
- `setActiveRunId(null)` — drops the local handle to the in-flight run.
- `setConversationId(null)` — next `submitUserMessage` will `createConversation` ([L432](../../apps/frontend/src/features/chat/ChatScreen.tsx#L432)).
- `setItems([])` — clears in-memory `ChatItem[]` including any `requires-action` message.
- `setLatestRunEvent(null)`, `setShowConnectorSuggestions(false)`, `setStatus("Ready")` — overwrites prior "Waiting for approval..." / "Streaming..." without surfacing what was dropped.

Refs **not** cleared (stale data leaks):

- `pendingApprovalDecisionsRef` ([L118](../../apps/frontend/src/features/chat/ChatScreen.tsx#L118)) — `Set<approvalId>`. In-flight `decideApproval` `finally` blocks still call `delete`, so it self-drains; but approvalIds tied to the abandoned run will never re-render their decision UI.
- `activeRunUserMessageIdsRef` ([L117](../../apps/frontend/src/features/chat/ChatScreen.tsx#L117)) — `Map<runId, userMessageId>`. The entry for the abandoned run is **not** deleted (only the terminal-event branch at [L244](../../apps/frontend/src/features/chat/ChatScreen.tsx#L244) and the OAuth-resume branch at [L352](../../apps/frontend/src/features/chat/ChatScreen.tsx#L352) call `delete`). Bounded but real leak over a long session.
- `latestSequenceRef` — left at the highest sequence; harmless without a `streamRef`.
- `latestReplaySequenceByRunRef` — preserved; overwritten on next `loadConversationById`.
- `reconnectTimeoutRef` — **not** cleared. If SSE was in its 750ms reconnect window when clicked (set at [L268](../../apps/frontend/src/features/chat/ChatScreen.tsx#L268)), the timer fires and reopens an SSE for the orphan `runId`. Self-closes on the next terminal event via `handleEvent` ([L232-247](../../apps/frontend/src/features/chat/ChatScreen.tsx#L232-L247)).

DB / network calls explicitly **not** made:

- No `POST /v1/agent/runs/{run_id}/cancel`. Run is not cancelled.
- No conversation update. No `decideApproval` for any open approvals on the abandoned run — they sit `awaiting_decision` until the run terminates on its own (or the user reopens and acts).

Server-side history is fully preserved. Reopening the conversation later via `loadConversationById` calls `getConversation` + `listMessages` + `replayRunEvents` per run ([L387-396](../../apps/frontend/src/features/chat/ChatScreen.tsx#L387-L396)) and rebuilds via `messagesToChatItems`. The pending-action `useEffect` at [L280-301](../../apps/frontend/src/features/chat/ChatScreen.tsx#L280-L301) then re-detects the open interrupt via `pendingActionRunId` ([L1138-1155](../../apps/frontend/src/features/chat/ChatScreen.tsx#L1138-L1155)) and re-attaches an SSE.

## Edge cases handled

- **SSE already closed.** Optional chaining on `streamRef.current?.close()`; no throw.
- **Approval decision in flight.** `finally` block of `onApprovalDecision` ([L601-603](../../apps/frontend/src/features/chat/ChatScreen.tsx#L601-L603)) cleans up `pendingApprovalDecisionsRef` after the card unmounts; idempotent.

## Known gaps / TODOs

- **No server-side cancel.** `onStartNewChat` is the only run-detaching path that does not call `cancelRun`. The abandoned run keeps consuming worker capacity, LLM tokens, MCP tool quotas, and event-store rows until natural termination or timeout. For long subagent fan-outs that's minutes of wasted compute per click.
  Fix: capture `activeRunId` at top of handler, fire-and-forget `void cancelRun(activeRunId, identity).catch(noop)` before resetting state. Mirror the ordering used by `onCancel` ([L541-550](../../apps/frontend/src/features/chat/ChatScreen.tsx#L541-L550)).
- **`reconnectTimeoutRef` not cleared.** Add the same `clearTimeout` guard `onCancel` uses.
- **`activeRunUserMessageIdsRef` entry leaks.** Delete the abandoned `runId` entry explicitly so a long session doesn't accrete dead mappings.
- **No user feedback.** Status flips straight to `"Ready"`. A user with a half-typed answer to an `ask_a_question` interrupt loses it silently. Consider a confirm dialog when `pendingActionRunId(items) !== null` or `activeRunId !== null`.
- **Open approvals stranded.** Approvals on the abandoned run sit `awaiting_decision` server-side. If the run later times out before the user reopens, the approval is unreachable.
- **Telemetry gap.** No event emitted for "user abandoned run via new-thread"; compliance audit needs this (per [CLAUDE.md → Compliance Reviews](../../CLAUDE.md): "who can do it, what changed, where it is logged").

## References

- Handler: [ChatScreen.tsx:553-562](../../apps/frontend/src/features/chat/ChatScreen.tsx#L553-L562)
- Compare with `onCancel`: [ChatScreen.tsx:540-551](../../apps/frontend/src/features/chat/ChatScreen.tsx#L540-L551)
- Cancel API client: [agentApi.ts:178-191](../../apps/frontend/src/api/agentApi.ts#L178-L191)
- Pending-action detector: [ChatScreen.tsx:1138-1155](../../apps/frontend/src/features/chat/ChatScreen.tsx#L1138-L1155); [chatModel/status.ts:74-85](../../apps/frontend/src/features/chat/chatModel/status.ts#L74-L85)
- Re-attach on reload: [ChatScreen.tsx:280-301](../../apps/frontend/src/features/chat/ChatScreen.tsx#L280-L301)
- Related: [04 — Switch conversation during run](04-switch-conversation-during-run.md), [08 — User cancels mid-stream](08-user-cancels-mid-stream.md)
