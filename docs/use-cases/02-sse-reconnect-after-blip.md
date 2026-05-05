# 02. SSE reconnect after a network blip

> Status: documented · Layers: fe / facade / ai-backend / db · Related: 01

## Trigger

While a run is streaming, the browser's `EventSource` fires `onerror` (transient TCP/proxy hiccup, sleep/wake, ingress reconnect). The run keeps going on the server; the client must re-attach without replaying events it already rendered.

## Preconditions

- A run is in flight: `activeRunId !== null`, `streamRef.current` holds the current `EventSource`, the worker is producing events into `runtime_events`.
- `latestSequenceRef.current` reflects the highest `sequence_no` the reducer has applied (advanced inside `handleEvent`).
- Server invariants: `runtime_events.sequence_no` is monotonic per `run_id` (UNIQUE on `(run_id, sequence_no)`, `FOR UPDATE` on `agent_runs` during append), and `agent_runs.latest_sequence_no` only moves forward.

## Sequence diagram

```mermaid
sequenceDiagram
    actor User
    participant FE as Browser (ChatScreen)
    participant Facade as backend-facade
    participant AI as ai-backend (runtime_api)
    participant Worker as runtime_worker
    participant Store as event store / DB
    Worker->>Store: append events seq=N+1, N+2 (network drops in between)
    AI--xFE: TCP/proxy drop, EventSource fires onerror
    FE->>FE: close streamRef, setStatus("Stream paused. Reconnecting..."), 750ms timer
    FE->>Facade: GET /v1/agent/runs/{run_id}/stream?after_sequence=N
    Facade->>AI: forward, follow=true
    AI->>Store: list_events_after(run_id, after=N) → seq>N rows
    AI-->>FE: SSE replays N+1..latest in sequence_no order
    Worker->>Store: appends seq=N+3; notify_sync wakes adapter
    AI-->>FE: live stream resumes from N+3 onward
```

## Function trace

1. `EventSource` fires `error` — the listener registered at [agentApi.ts:277](apps/frontend/src/api/agentApi.ts#L277) calls the caller's `onError` handler.
2. `streamRunEvents` `onError` → `startEventStream`'s inline closure — [ChatScreen.tsx:264-274](apps/frontend/src/features/chat/ChatScreen.tsx#L264-L274) — closes `streamRef`, sets `streamRef.current = null`, sets status `"Stream paused. Reconnecting..."`, schedules `window.setTimeout(..., 750)` into `reconnectTimeoutRef.current`.
3. Timer fires, recursion — [ChatScreen.tsx:268-270](apps/frontend/src/features/chat/ChatScreen.tsx#L268-L270) — calls `startEventStream(runId, latestSequenceRef.current)` with the highest sequence the reducer has actually consumed (not `0`, so the server can skip already-delivered events).
4. `startEventStream` body — [ChatScreen.tsx:252-278](apps/frontend/src/features/chat/ChatScreen.tsx#L252-L278) — clears any stale reconnect timer, closes any leftover `streamRef`, then opens a new stream via `streamRunEvents`.
5. `streamRunEvents` — [agentApi.ts:239-279](apps/frontend/src/api/agentApi.ts#L239-L279) — builds `params = identityParams(identity)`, sets `after_sequence=<latestSequenceRef.current>`, opens `new EventSource("/v1/agent/runs/{run_id}/stream?...")`, re-binds the `runtime_event` and `error` listeners. The named-event payload uses `SSE_EVENT_NAME = "runtime_event"` — [agentApi.ts:26](apps/frontend/src/api/agentApi.ts#L26).
6. Facade `stream_run` — [app.py:465-507](services/backend-facade/src/backend_facade/app.py#L465-L507) — re-authenticates, forwards `after_sequence` upstream via `identity.scoped_params({"after_sequence": after_sequence})`, opens a fresh `httpx.AsyncClient`, and only on a successful upstream status returns a `StreamingResponse` that proxies bytes verbatim. Aborts and closes both clients if the browser disconnects again ([app.py:497-505](services/backend-facade/src/backend_facade/app.py#L497-L505)).
7. `RuntimeApiRoutes.stream_run` — [routes.py:201-226](services/ai-backend/src/runtime_api/http/routes.py#L201-L226) — pulls the singleton `RuntimeEventBus` off `app.state.runtime_event_bus` and hands it to the SSE adapter so wakeups come from the worker, not polling.
8. `RuntimeSseAdapter.stream` — [adapter.py:26-70](services/ai-backend/src/runtime_api/sse/adapter.py#L26-L70) — sets `latest_sequence = after_sequence`, then loops:
   - call `service.replay_events(after_sequence=latest_sequence)`,
   - yield each envelope through `format_event` ([adapter.py:106-114](services/ai-backend/src/runtime_api/sse/adapter.py#L106-L114)) — `event:` is the `SSE_EVENT_NAME` constant, `id:` is the event's `sequence_no`, `data:` is `envelope.model_dump_json()`,
   - if `replay.run_status` is terminal, unsubscribe and return,
   - otherwise `await event_bus.wait(run_id, timeout=2.0)` and loop.
9. `RuntimeApiService.replay_events` — [service.py:412-440](services/ai-backend/src/agent_runtime/api/service.py#L412-L440) — fetches the run (tenant-scoped), calls `event_store.list_events_after(org_id, run_id, after_sequence)` and computes `latest_sequence_no` either from the returned rows or `event_store.get_latest_sequence`.
10. Postgres `list_events_after` — [runtime_api_store.py:2014-2033](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L2014-L2033) — `SELECT * FROM runtime_events WHERE org_id=%s AND run_id=%s AND sequence_no > %s ORDER BY sequence_no ASC`. Because the producer-side `append_event` ([runtime_api_store.py:1912-2012](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L1912-L2012)) takes `SELECT ... FOR UPDATE` on `agent_runs` and reads `MAX(sequence_no)+1` inside that lock, no two events ever share a `sequence_no`, and any event with `sequence_no <= N` was already delivered before the disconnect.
11. Continued worker activity — [event_bus.py:38-55](services/ai-backend/src/runtime_api/sse/event_bus.py#L38-L55) — every new `event_producer.append_api_event` (see [events.py:140-146](services/ai-backend/src/agent_runtime/api/events.py#L140-L146)) calls `RuntimeEventBus.notify_sync(run_id)`, which schedules `condition.notify_all` on the loop. The adapter's `await event_bus.wait(...)` returns immediately and the loop picks up the new event on the next `replay_events` call.
12. Frontend `handleEvent` (idempotent) — [ChatScreen.tsx:216-250](apps/frontend/src/features/chat/ChatScreen.tsx#L216-L250) — `latestSequenceRef.current = Math.max(latestSequenceRef.current, event.sequence_no)`, then `applyRuntimeEvent(items, event)`. The reducer's `MODEL_DELTA` path appends to the trailing text part ([eventReducer.ts:100-114](apps/frontend/src/features/chat/chatModel/eventReducer.ts#L100-L114)); because the server's `after_sequence=N` filter never replays already-delivered deltas, the assistant text is not duplicated.
13. Terminal-event path on resume — [ChatScreen.tsx:232-247](apps/frontend/src/features/chat/ChatScreen.tsx#L232-L247) — if the run finished while disconnected, the resumed stream replays the post-N events including `RUN_COMPLETED`. `handleEvent` clears `reconnectTimeoutRef`, closes the stream, clears `activeRunId`, and triggers `refreshConversations()`. The SSE adapter's loop also exits via `replay.run_status in TERMINAL_RUN_STATUSES` ([adapter.py:51-54](services/ai-backend/src/runtime_api/sse/adapter.py#L51-L54)).

## Runtime events emitted

No new events are produced by the reconnect itself — it is read-only on the server. The client receives whichever subset of the run's existing events satisfies `sequence_no > after_sequence`, in order. Examples (numbers illustrative, not normative):

| condition                                                      | replayed on resume                                 | UI projection                                                 |
| -------------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------- |
| Disconnect mid-stream after seq=12, resume `after_sequence=12` | seq=13..latest, then live                          | deltas continue appending into the same assistant `ChatItem`. |
| Disconnect after `FINAL_RESPONSE` (seq=K), resume              | seq=K+1 = `RUN_COMPLETED` (or already terminal)    | terminal handler closes stream, clears `activeRunId`.         |
| Run finished entirely while offline                            | all events from N+1..terminal in one batch         | adapter exits after replay because `run_status` is terminal.  |
| Empty replay, run still running                                | zero events, then `event_bus.wait` blocks up to 2s | client stays open; reducer no-ops.                            |

If the disconnect window is long enough that the worker has emitted only internal-visibility events, the reducer drops them at [eventReducer.ts:42-47](apps/frontend/src/features/chat/chatModel/eventReducer.ts#L42-L47); they still increment `latestSequenceRef`.

## State changes

DB rows written on reconnect: **none from the SSE side**. `replay_events` is a `SELECT`-only read. The only writes during a reconnect are whatever the worker happens to do concurrently (the same writes documented in 01).

Frontend state setters/refs:

- `setStatus("Stream paused. Reconnecting...")` from the `onError` closure.
- `streamRef.current` — set to `null`, then re-assigned to the new `EventSource`.
- `reconnectTimeoutRef.current` — set by `window.setTimeout`, cleared on the next reconnect attempt or on terminal-event handling.
- `latestSequenceRef.current` — read only at reconnect time; further advanced by `handleEvent` as new envelopes arrive.

No setters are called for `setItems` until the first replayed envelope is reduced; the existing assistant `ChatItem` (created during the original stream) is mutated in place via `appendTextDelta`.

## Edge cases handled

- Repeated blips — each `onError` cancels the in-flight `reconnectTimeoutRef` before scheduling the next one ([ChatScreen.tsx:254-257](apps/frontend/src/features/chat/ChatScreen.tsx#L254-L257), [ChatScreen.tsx:264-270](apps/frontend/src/features/chat/ChatScreen.tsx#L264-L270)). The 750 ms backoff is fixed (no exponential growth yet).
- Reconnect during cancel — `onCancel` ([ChatScreen.tsx:537-548](apps/frontend/src/features/chat/ChatScreen.tsx#L537-L548)) clears `reconnectTimeoutRef` and closes `streamRef` before sending the cancel POST, so a stalled reconnect can't fire after the run is cancelled.
- Component unmount while paused — the cleanup at [ChatScreen.tsx:200-207](apps/frontend/src/features/chat/ChatScreen.tsx#L200-L207) clears the timeout and closes the stream.
- Out-of-order writes inside the AI backend — `set_run_latest_sequence` is monotonic (`AND latest_sequence_no < %s`), so a late event never rewinds the cursor ([runtime_api_store.py:746-768](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L746-L768)). Replay still uses raw `runtime_events.sequence_no`, so the client gets every row even if the cursor lags.
- Concurrent appenders during reconnect — the `agent_runs FOR UPDATE` row lock plus the `UNIQUE (run_id, sequence_no)` constraint guarantee gap-free monotonic sequences regardless of how many appenders the worker spawns ([runtime_api_store.py:1912-1922](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py#L1912-L1922)).
- Idempotent reducer — `applyRuntimeEvent` ignores `activity_kind === "heartbeat"` and `visibility === "internal"` and matches existing parts by `toolCallId` / `assistantMessageId(run_id)` so re-arrival of an already-applied event would not duplicate state. The `after_sequence` filter makes that re-arrival impossible on the happy path; the property is the second line of defense.
- Malformed frame after reconnect — `RuntimeStreamProtocolError` ([agentApi.ts:32-51](apps/frontend/src/api/agentApi.ts#L32-L51)) is dispatched to `onProtocolError` ([ChatScreen.tsx:272-274](apps/frontend/src/features/chat/ChatScreen.tsx#L272-L274)) which only updates `status`; the stream stays open.

## Known gaps / TODOs

- Backoff is constant 750 ms — no jitter, no cap on retry count. A pathological proxy that closes every connection within 750 ms will spin.
- The reconnect path does not surface to the user that buffered server events arrived in a burst; the assistant message can suddenly grow by many tokens at once.
- `replay_events` returns `has_more=False` unconditionally ([service.py:439](services/ai-backend/src/agent_runtime/api/service.py#L439)) — there is no pagination for very large reconnect windows; the entire post-N tail is loaded into one response.
- If `app.state.runtime_event_bus` is unset (e.g. early in startup), the SSE adapter falls back to a 2 s polling sleep ([adapter.py:67-70](services/ai-backend/src/runtime_api/sse/adapter.py#L67-L70)) — visible as a worst-case 2 s gap before live events resume.
- Cross-process workers cannot wake the SSE adapter via the in-process `RuntimeEventBus`; the singleton is per-process ([event_bus.py:19-26](services/ai-backend/src/runtime_api/sse/event_bus.py#L19-L26)). Multi-replica deploys rely on the 2 s fallback poll until a shared notify channel is added.

## References

- [apps/frontend/src/features/chat/ChatScreen.tsx](apps/frontend/src/features/chat/ChatScreen.tsx)
- [apps/frontend/src/api/agentApi.ts](apps/frontend/src/api/agentApi.ts)
- [apps/frontend/src/features/chat/chatModel/eventReducer.ts](apps/frontend/src/features/chat/chatModel/eventReducer.ts)
- [services/backend-facade/src/backend_facade/app.py](services/backend-facade/src/backend_facade/app.py)
- [services/ai-backend/src/runtime_api/http/routes.py](services/ai-backend/src/runtime_api/http/routes.py)
- [services/ai-backend/src/runtime_api/sse/adapter.py](services/ai-backend/src/runtime_api/sse/adapter.py)
- [services/ai-backend/src/runtime_api/sse/event_bus.py](services/ai-backend/src/runtime_api/sse/event_bus.py)
- [services/ai-backend/src/agent_runtime/api/service.py](services/ai-backend/src/agent_runtime/api/service.py)
- [services/ai-backend/src/agent_runtime/api/events.py](services/ai-backend/src/agent_runtime/api/events.py)
- [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py)
