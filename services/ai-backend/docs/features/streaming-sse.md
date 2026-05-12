# Streaming and SSE

How events flow from the worker to the browser over Server-Sent Events, and how
clients survive disconnects, follow in-flight runs, and trigger cancellation.

See also:

- [architecture/01-request-lifecycle.md](../architecture/01-request-lifecycle.md) — full request path
- [diagrams/flows/f1-single-turn.puml](../architecture/diagrams/flows/f1-single-turn.puml)
- [diagrams/flows/f3-sse-resume.puml](../architecture/diagrams/flows/f3-sse-resume.puml)
- [diagrams/flows/f4-cancel.puml](../architecture/diagrams/flows/f4-cancel.puml)

---

## What it does

Events produced by the worker are persisted with a monotonic `sequence_no` per run.
Clients open a long-lived HTTP connection and receive those events as SSE frames.
If the connection drops, the client reconnects with the highest `sequence_no` it
received and gets a gap-fill replay from the event store — no events are lost.

---

## Key modules

| File                                    | Role                                                              |
| --------------------------------------- | ----------------------------------------------------------------- |
| `runtime_api/sse/adapter.py`            | `RuntimeSseAdapter` — replay + bus notification loop              |
| `runtime_api/sse/event_bus.py`          | `RuntimeEventBus` — in-memory asyncio event per run_id            |
| `runtime_api/sse/postgres_event_bus.py` | `PostgresEventBus` — Postgres LISTEN/NOTIFY (multi-pod)           |
| `runtime_api/sse/inbox_adapter.py`      | `RuntimeInboxSseAdapter` — per-user inbox (workspace feed)        |
| `runtime_api/http/routes.py`            | `GET /v1/agent/runs/{run_id}/stream` endpoint                     |
| `agent_runtime/api/events.py`           | `RuntimeEventProducer.append_api_event()` — writes + notifies bus |

---

## SSE frame format

```
event: runtime_event
id: <sequence_no>
data: <RuntimeEventEnvelope JSON>

```

The `id` field matches `sequence_no`. The browser's `EventSource` API surfaces it
as `event.lastEventId`. On reconnect, the client sends `?after_sequence=<lastEventId>`.

---

## `RuntimeSseAdapter.stream()` logic

`runtime_api/sse/adapter.py`

```python
latest_sequence = after_sequence
while True:
    replay = await service.replay_events(after_sequence=latest_sequence)
    for event in replay.events:
        latest_sequence = max(latest_sequence, event.sequence_no)
        yield format_event(event)
    if replay.run_status in TERMINAL_RUN_STATUSES:
        event_bus.unsubscribe(run_id)
        return                          # stream closes naturally
    if not follow:
        if not replay.events:
            yield heartbeat_event(...)  # synthetic envelope, metadata.transient=True
        event_bus.unsubscribe(run_id)
        return
    await event_bus.wait(run_id, timeout=2.0)  # wake on push or 2s poll fallback
```

Key behaviours:

- **Push notification** — `RuntimeEventProducer` calls `event_bus.notify_sync(run_id)` after
  every `append_event`. In-memory bus wakes the asyncio `Event`; Postgres bus sends
  `NOTIFY runtime_events_<run_id>`.
- **2s poll fallback** — if no push arrives within `FALLBACK_POLL_SECONDS=2.0`, the adapter
  re-reads anyway. This tolerates transient bus failures without losing events.
- **Gap fill on reconnect** — on reconnect with `after_sequence=42`, the first `replay_events`
  call returns envelopes `#43, #44, #45, …` up to the latest persisted. No events are missed.
- **Terminal detection** — `replay.run_status in TERMINAL_RUN_STATUSES` (`COMPLETED`,
  `CANCELLED`, `FAILED`). The adapter unsubscribes and returns; the HTTP response completes.

---

## `follow` vs non-`follow`

| `follow`         | Behaviour                                                              |
| ---------------- | ---------------------------------------------------------------------- |
| `true` (default) | Keep connection alive; tail new events as they arrive                  |
| `false`          | Emit all existing events, then one heartbeat if none exist, then close |

`follow=false` is used for polling snapshots and by the replay-only endpoint
`GET /v1/agent/runs/{run_id}/events`.

---

## Heartbeat event

When `follow=false` and there are no new events, the adapter emits one synthetic
`HEARTBEAT` envelope (`event_type=HEARTBEAT`, `metadata.transient=true`,
`sequence_no=max(1, latest+1)`). This signals to the client that the stream is
up-to-date, not stalled. The sequence number is not persisted — it is advisory.

---

## Cancellation

`POST /v1/agent/runs/{run_id}/cancel`

1. `RuntimeApiService.cancel_run()` enqueues a `RuntimeCancelCommand`.
2. `RuntimeEventProducer.append_api_event(RUN_CANCELLING)` — advisory event (may arrive
   before the worker acts).
3. The worker claims the cancel command; `RuntimeCancelHandler` marks the run `CANCELLED`,
   emits `RUN_CANCELLED`.
4. `RuntimeSseAdapter` sees `CANCELLED` in `TERMINAL_RUN_STATUSES` → stream closes.

The live run's `RuntimeRunHandler` checks run status on the next graph loop tick and
exits. A MODEL_DELTA in-flight when the cancel lands may still reach the client before
the terminal status — this is acceptable.

See flow diagram [f4-cancel](../architecture/diagrams/flows/f4-cancel.puml).

---

## Terminal run statuses

`ConversationQueryService.TERMINAL_RUN_STATUSES` (`agent_runtime/api/`):
`COMPLETED`, `CANCELLED`, `FAILED`.

Once a run reaches a terminal status, no new events are appended to it.

---

## Workspace / inbox SSE

A separate stream `GET /v1/agent/workspace/stream` delivers per-user inbox events
(new conversation notifications, workspace feed updates) via `RuntimeInboxSseAdapter`
and `RuntimeInboxEventBus`. These are distinct from per-run streams.

---

## Postgres LISTEN/NOTIFY (multi-pod production)

`runtime_api/sse/postgres_event_bus.py`

In production (`RUNTIME_STORE_BACKEND=postgres`), the event bus is backed by Postgres
`LISTEN`/`NOTIFY`. One dedicated `psycopg` connection per API replica listens on a channel
named `runtime_events_<run_id>`. When the worker appends an event and calls
`NOTIFY runtime_events_<run_id>, '<run_id>:<sequence_no>'`, all API replicas holding an SSE
connection for that run are woken simultaneously.

**Latency:** Postgres LISTEN/NOTIFY reduces p50 SSE latency from ~1 s (polling) to ~50 ms.

**Reconnect:** The listener connection uses exponential backoff (cap 30 s) on disconnect.

**Fan-out:** All `asyncio.Event` waiters for a given `run_id` on the same API process are
woken together when a notification arrives.

**Poll fallback:** Even with LISTEN/NOTIFY, the `FALLBACK_POLL_SECONDS=2.0` timeout still
applies. A missed notification (NOTIFY fired before LISTEN completed) is caught by the
next poll. Events are never lost — the fallback is the safety net, not the primary path.

**NOTIFY payload bounds:** `<run_id>:<sequence_no>` — UUIDs plus an integer, well under
Postgres's 8 KB NOTIFY payload limit.

---

## Extension points

- To add a new terminal status: update `AgentRunStatus`, add to `TERMINAL_RUN_STATUSES`,
  update the worker handler that emits it.
- To change push notification backend: implement `RuntimeEventBus` protocol and wire in
  `runtime_adapters/factory.py`.
