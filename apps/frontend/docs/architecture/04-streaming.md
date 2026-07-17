# Streaming

How runtime SSE events reach the chat surface.

See also:

- [01-network-layer.md](01-network-layer.md) — the SSE reader uses the same
  `correlationHeaders()` as every other request
- [features/chat-surface-invariants.md](../features/chat-surface-invariants.md) —
  what the planning pulse derives from the event stream
- [`src/features/chat/chatModel/README.md`](../../src/features/chat/chatModel/README.md) —
  the reducer pipeline downstream of the parser

Source: [`src/api/agentApi.ts`](../../src/api/agentApi.ts) — `streamRunEvents`,
`streamInboxEvents`, `_streamSseEvents`, `_dispatchFrame`

---

## Why `fetch`, not `EventSource`

The browser's `EventSource` cannot send custom headers, so the bearer never
reaches the facade and the stream 401s. Workarounds (cookie sessions,
`?token=…` URL params) either invent a second auth scheme or write
bearer-equivalents into access logs / proxy logs — bad fit for the
bearer-only model the codebase committed to in W0.1.

The fetch-based reader in `_streamSseEvents`:

1. `fetch(url, { headers: { ...correlationHeaders(), accept: "text/event-stream" }, signal: controller.signal })`
2. On `response.ok`, calls `onOpen()` and reads `response.body` with a
   `getReader()` loop.
3. Buffers chunks, splits on `\n\n` (the SSE frame delimiter), and
   dispatches each completed frame to `_dispatchFrame`.
4. `_dispatchFrame` parses `event:` and `data:` lines; if `event:` matches
   `runtime_event` (or the per-stream constant), the buffered `data:` text
   is passed to `onMessage`.
5. Abort is cooperative: the returned `AgentEventStream` has a `close()`
   that calls `controller.abort()`. The reader loop checks
   `controller.signal.aborted` before surfacing errors so cancellation
   doesn't look like a stream error.

Reconnect semantics intentionally live with the caller — the chat screen
knows the right `?after_sequence=N` to resume with based on the highest
event it has actually rendered.

---

## Event envelope validation

`onMessage` calls `JSON.parse`, then `isRuntimeEventEnvelope(parsed)` (from
`@0x-copilot/api-types`). Two error paths:

| Failure                                      | `onProtocolError` reason |
| -------------------------------------------- | ------------------------ |
| `JSON.parse` throws                          | `"malformed_json"`       |
| Parsed value is not a `RuntimeEventEnvelope` | `"invalid_envelope"`     |

`RuntimeStreamProtocolError` carries the reason and the **length** of the
offending payload — never the raw payload. The original data may contain
user-typed prompts or model output, and this error object can flow through
React error boundaries / OTEL spans where any string field could be
serialised. Length preserves the one useful debugging signal (was the
payload truncated?) without the leak.

Network failures (`onError`) follow the EventSource reconnect path with the
latest received `sequence_no`.

---

## Resume contract

Events arrive with a monotonic `sequence_no` per run. The chat screen
records the **highest sequence_no it actually rendered**, then reconnects
with `?after_sequence=N` on errror or page reload:

```
GET /v1/agent/runs/{run_id}/stream?after_sequence=N   # live, tail
GET /v1/agent/runs/{run_id}/events?after_sequence=N   # replay only
```

The replay endpoint (`replayRunEvents`) returns a typed
`RuntimeEventReplayResponse` for the cold-load case (resuming a run after
the SSE stream has closed); the stream endpoint is for live tailing.
**Don't** call both for the same `after_sequence`; the events would
duplicate. The chat screen calls replay once on conversation open and
opens a stream from the last replayed sequence onward.

---

## What the frontend reads from each event

The backend projects each runtime event into the following display-shape
fields **at write time**. The frontend reads these fields directly and does
not derive activity types from event-name prefixes:

| Field           | Used for                                                                 |
| --------------- | ------------------------------------------------------------------------ |
| `event_type`    | Phase derivation in `chatRunState.deriveRunUiState` (see invariants doc) |
| `activity_kind` | "tool" / "subagent" / "heartbeat" — drives card category                 |
| `display_title` | Card title in thread + Workspace pane                                    |
| `summary`       | One-line description in collapsed cards                                  |
| `status`        | `running` / `queued` / `completed` / `failed` / `cancelled`              |
| `visibility`    | `"internal"` events suppressed (heartbeats, scheduler ticks)             |
| `sequence_no`   | Resume cursor; idempotency key for reducers                              |

The reducer pipeline downstream (citations, sources, subagents, drafts) is
documented in [`chatModel/README.md`](../../src/features/chat/chatModel/README.md).

---

## Inbox SSE (`streamInboxEvents`)

A second stream pattern: `/v1/agent/me/inbox/stream?after_sequence=N` —
per-user channel for approval assignments. Same `_streamSseEvents` reader,
different envelope (`InboxEventEnvelope` with `sequence_no`, `event_type`,
`approval_id`). Reconnect with the highest received `sequence_no`.
