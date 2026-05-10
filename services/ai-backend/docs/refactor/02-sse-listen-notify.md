# Refactor PRD — SSE bus over Postgres `LISTEN/NOTIFY` (P2)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §4.1](../architecture/refactor-audit.md#41-sse-delivery-is-1s-in-production)
**Roadmap entry:** [`00-roadmap.md` P2](00-roadmap.md#phase-1--performance-wins-no-structural-change)

---

## 1. Problem

`RuntimeSseAdapter` (the per-run SSE surface) has two delivery mechanisms:

1. **Push** — `RuntimeEventBus.notify_sync(run_id)` is called from the writer (`RuntimeEventProducer.append_*_event` via `on_event_appended`). The adapter is `await event_bus.wait(run_id, timeout=2.0)` and gets woken immediately — sub-millisecond delivery.
2. **Poll fallback** — when `wait` times out (after 2 s), the adapter calls `service.replay_events(after_sequence=N)` and resumes.

The push path uses `asyncio.Condition` ([`runtime_api/sse/event_bus.py`](../../src/runtime_api/sse/event_bus.py)). The Condition lives in process memory. In production:

- The **API** runs in its own process (often N replicas behind an LB).
- The **worker** runs in its own process (often M replicas).

`notify_sync` from the worker writes to a Condition that lives in the worker's address space. The API process never sees the wakeup. The 2-second poll fallback is the **actual delivery mechanism in production**. Average end-to-end latency for a freshly appended event is therefore ≈ 1 s (uniform 0–2 s).

In dev (`RUNTIME_START_IN_PROCESS_WORKER=true`) the worker shares the API process, so the in-memory Condition does work — which is why no one has noticed the production gap.

The same problem exists twice: [`event_bus.py`](../../src/runtime_api/sse/event_bus.py) for run streams, [`inbox_bus.py`](../../src/runtime_api/sse/inbox_bus.py) for the inbox push channel.

### Why now

P4 Stage 1 + Stage 2 reduced per-event DB ops by ~50%. The next bottleneck on the perceived "live streaming" experience is the bus latency. Without this fix, every other latency optimization is masked behind a 1-second average wait.

### What this is NOT

- **Not a wire format change.** SSE clients see identical frames — only the producer-side notification mechanism changes.
- **Not a change to `replay_events`** or the resume contract (`?after_sequence=N`).
- **Not a change to event ordering or `sequence_no` semantics.**
- **Not a switch of database** — Postgres `LISTEN/NOTIFY` is built into the existing infra; no Redis / NATS / Kafka added.
- **Not a removal of the poll fallback.** The poll continues to run as a backstop with a longer interval (10 s instead of 2 s) so a missed notification still surfaces eventually.

---

## 2. Goal and non-goals

### Goal

Replace the in-process `asyncio.Condition` push with a **Postgres `LISTEN/NOTIFY`-based bus** that delivers wakeups across processes. Drop p50 SSE end-to-end delivery latency from ~1 s to ~50 ms. Raise the poll-fallback interval from 2 s to 10 s (notification is the primary mechanism, poll is the backstop).

### Non-goals

- Replace polling entirely. The poll stays as the missed-notification backstop.
- Add a new external infra dependency (Redis / NATS / etc.). Postgres only.
- Carry event payload in the notification. Notification is a **wakeup signal** with the new `sequence_no`; the SSE adapter still reads the event from the store. (Postgres `NOTIFY` payload is limited to 8000 bytes — sized for a sequence number, not the envelope.)
- Refactor SSE replay logic. Only the bus changes.
- Move `inbox_bus.py` to `LISTEN/NOTIFY` in this PR — that is a clear follow-up but a separate change.

### Success criteria

- A new `EventBusBackend` Protocol with two implementations: `InMemoryEventBus` (current behavior, renamed) and `PostgresEventBus` (`LISTEN/NOTIFY`-based).
- `RUNTIME_EVENT_BUS_BACKEND` env var selects between them. Default `in_memory` so the behavior change ships dark.
- The API process's lifespan (`RuntimeApiAppFactory`) starts and stops a `PostgresEventBus.listen_loop()` task when the Postgres backend is selected.
- The worker (or the postgres adapter, on every `append_event` / `append_events_batch`) calls `NOTIFY runtime_events_v1, '<run_id>:<sequence_no>'`.
- `RuntimeSseAdapter.FALLBACK_POLL_SECONDS` changes from `2.0` to `10.0` only when `PostgresEventBus` is the configured backend.
- All existing SSE tests pass without modification (in-memory backend remains the default).
- New tests pin: NOTIFY payload format, dispatcher routes by run_id, idempotent unsubscribe, listener restarts on connection drop.

---

## 3. Systems touched

### 3.1 New / refactored files

| File                                                                                                             | Change                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`src/runtime_api/sse/event_bus.py`](../../src/runtime_api/sse/event_bus.py)                                     | Define `EventBusBackend` Protocol. Rename current class to `InMemoryEventBus`. Keep `RuntimeEventBus.get_default()` as a backwards-compat alias returning `InMemoryEventBus.get_default()` so existing call sites need no edit. |
| `src/runtime_api/sse/postgres_event_bus.py` (new)                                                                | `PostgresEventBus` — owns one dedicated psycopg connection for `LISTEN`. Background task drains notifications and dispatches by `run_id` to local `asyncio.Event`s. NOTIFY is fired by the postgres adapter.                    |
| [`src/runtime_adapters/postgres/runtime_api_store.py`](../../src/runtime_adapters/postgres/runtime_api_store.py) | After `append_event` / `append_events_batch`, run `NOTIFY runtime_events_v1, '<run_id>:<sequence_no>'`. Gated on `notify_after_append: bool = False` constructor arg; settings turn it on with the Postgres bus.                |
| [`src/agent_runtime/settings.py`](../../src/agent_runtime/settings.py)                                           | New env var `RUNTIME_EVENT_BUS_BACKEND` (`in_memory` \| `postgres`, default `in_memory`). New `RuntimeExecutionSettings.event_bus_backend` field.                                                                               |
| [`src/runtime_api/app.py`](../../src/runtime_api/app.py)                                                         | Lifespan starts `PostgresEventBus.listen_loop()` as a background task when the Postgres bus is selected; cancels + awaits cleanly on shutdown.                                                                                  |
| [`src/runtime_adapters/factory.py`](../../src/runtime_adapters/factory.py)                                       | When the postgres backend is selected, pass `notify_after_append=settings.execution.event_bus_backend == "postgres"` to `PostgresRuntimeApiStore`.                                                                              |
| [`src/runtime_api/sse/adapter.py`](../../src/runtime_api/sse/adapter.py)                                         | `FALLBACK_POLL_SECONDS` becomes a property derived from the bus type (`2.0` for in-memory, `10.0` for Postgres).                                                                                                                |

### 3.2 Channel format

```
NOTIFY runtime_events_v1, '<run_id>:<sequence_no>'
```

- Channel name `runtime_events_v1` is versioned so a future schema change can use a parallel `runtime_events_v2` channel without disrupting in-flight clients.
- Payload `'<run_id>:<sequence_no>'` is short (well under the 8000-byte Postgres limit), trivially parseable, and self-validating (UUID + integer).
- `LISTEN` is on the bare channel name; the dispatcher parses each notification and routes by run_id.

### 3.3 Connection ownership

Postgres `LISTEN` is connection-bound: a connection that has issued `LISTEN` receives notifications until it disconnects or `UNLISTEN`s. Implications:

- The `PostgresEventBus` holds **one dedicated psycopg `AsyncConnection`** for the lifetime of the API process. It is not returned to the pool.
- All NOTIFY-side operations come from connections in the regular pool (transient — a NOTIFY emitted inside any transaction is delivered when the transaction commits).
- On listener-side connection drop, the loop reconnects with exponential backoff (capped at 30 s) and re-issues `LISTEN`. Any notifications missed during the gap are recovered by the SSE adapter's poll fallback.

### 3.4 Notification dispatch

```python
class PostgresEventBus:
    async def listen_loop(self) -> None:
        # 1. acquire dedicated conn from pool's listener variant
        # 2. await conn.execute("LISTEN runtime_events_v1")
        # 3. async for notify in conn.notifies():
        #        run_id, _, seq = notify.payload.partition(":")
        #        event = self._listeners.get(run_id)
        #        if event is not None:
        #            event.set()
        # 4. on connection drop: log + reconnect backoff
```

The `_listeners` map is `dict[str, asyncio.Event]`. SSE adapters call `await bus.wait_for(run_id, timeout)` which:

1. Allocates / reuses an `asyncio.Event` for the run_id.
2. `await asyncio.wait_for(event.wait(), timeout=...)`
3. Clears the event so the next call sees a fresh wait.

`unsubscribe(run_id)` removes the entry from `_listeners` once the SSE stream closes (terminal status / disconnect / heartbeat).

### 3.5 Footprint summary

- **Added**: ~200 LOC across new module + adapter NOTIFY + settings + lifespan wiring.
- **Modified**: ~30 LOC in 4 existing files.
- **Tests**: ~150 LOC pinning the bus contract.
- No DB schema migration. No event format change. No SSE wire change.

---

## 4. Behaviors that must be preserved

| Behavior                                  | How preserved                                                                                                                                                               |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSE wire format (`event:`/`id:`/`data:`)  | Unchanged in `format_event`                                                                                                                                                 |
| Resume contract (`?after_sequence=N`)     | Unchanged                                                                                                                                                                   |
| `follow=false` synthetic heartbeat        | Unchanged                                                                                                                                                                   |
| Terminal-status auto-close + unsubscribe  | Unchanged                                                                                                                                                                   |
| `sequence_no` monotonic per run           | Unchanged                                                                                                                                                                   |
| `notify_sync` from worker / API codepaths | Implementation moves; surface stays — `RuntimeEventProducer.on_event_appended` still receives a callable. The default callback continues to be the configured bus's notify. |
| In-memory bus default for tests + dev     | Default backend is `in_memory`; existing test suites continue to pass without modification                                                                                  |

---

## 5. Risks

| Risk                                                                          | Mitigation                                                                                                                                                                            |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Listener connection drop loses notifications                                  | Poll fallback (10 s) catches missed events. Reconnect loop with exponential backoff (cap 30 s). Tests pin reconnect behavior.                                                         |
| Postgres connection limit pressure (one extra dedicated conn per API replica) | Negligible at typical replica counts (≤ 50 replicas × 1 conn = 50 connections, well under any reasonable Postgres pool ceiling). Documented in deployment notes.                      |
| Notification delivered but no listener registered yet                         | `LISTEN` is in place before any NOTIFY can be issued (lifespan ordering). For an SSE client that subscribes after a NOTIFY: their initial `replay_events` call catches up regardless. |
| `NOTIFY` payload exceeds 8000 bytes                                           | Payload is `<run_id>:<sequence_no>` — bounded by UUID (36) + colon + integer ≪ 8000.                                                                                                  |
| Adapter constructor adds a new optional flag                                  | `notify_after_append: bool = False` default; settings opts in only when Postgres bus is selected. No regression for tests / dev.                                                      |
| `FALLBACK_POLL_SECONDS` change affects in-memory dev path                     | The in-memory bus keeps `2.0` s; the change to `10.0` only applies when the Postgres bus is configured (where notifications are real).                                                |
| Concurrent SSE clients on the same run                                        | Each gets its own `wait_for` call; `asyncio.Event.set()` wakes all current waiters. Acceptable — fan-out is intentional.                                                              |

---

## 6. Unit testing requirements

In `tests/unit/runtime_api/sse/test_event_bus.py` (new):

- `test_in_memory_bus_default_unchanged` — default constructor path returns `InMemoryEventBus` and behaves like the current `RuntimeEventBus`.
- `test_postgres_bus_dispatches_to_correct_run_id` — fake notification stream; `wait_for(run_id="A")` returns when `A:5` arrives; `wait_for(run_id="B")` does not.
- `test_postgres_bus_payload_parser_handles_malformed` — notifications with missing colon / non-numeric sequence are dropped with a warning; do not crash the listener loop.
- `test_postgres_bus_unsubscribe_is_idempotent` — calling `unsubscribe` twice with the same run_id is a no-op.
- `test_postgres_bus_reconnect_on_drop` — simulate connection loss; the loop reconnects within the backoff window and re-issues `LISTEN`.
- `test_postgres_bus_notify_after_append_emits` — when `PostgresRuntimeApiStore.append_event` runs with `notify_after_append=True`, a NOTIFY is fired with the expected payload.
- `test_postgres_bus_notify_after_append_disabled` — when the flag is False, no NOTIFY fires.
- `test_settings_flag_selects_postgres_backend` — `RUNTIME_EVENT_BUS_BACKEND=postgres` produces a settings field that the factory uses to flip the adapter's `notify_after_append`.
- `test_sse_adapter_poll_fallback_uses_10s_for_postgres_bus` — `RuntimeSseAdapter.FALLBACK_POLL_SECONDS` derives from the configured bus.
- `test_sse_adapter_poll_fallback_uses_2s_for_in_memory_bus` — default behavior unchanged.

In-memory tests can run in pure asyncio. Postgres LISTEN/NOTIFY tests require either:

- A real Postgres instance (CI integration runner), OR
- Mocking psycopg's `notifies()` async iterator + `execute("LISTEN …")`.

For the unit suite we use mocks; integration tests against a live Postgres are out of scope for this PR.

---

## 7. Rollout plan

1. PR ships with `RUNTIME_EVENT_BUS_BACKEND` defaulting to `in_memory` — existing dev / test paths see zero change.
2. Staging: set `RUNTIME_EVENT_BUS_BACKEND=postgres`. Verify SSE p50 latency drops to < 100 ms via existing instrumentation. Soak ≥ 24 h.
3. Production: flip the env var. Monitor for: notification miss rate (poll catches → would surface as 10-second-late events; alert if > 0.1% of events).
4. After production soak: optional follow-up to retire the in-memory backend's call sites in `app.py`'s lifespan.

Rollback: `RUNTIME_EVENT_BUS_BACKEND=in_memory` reverts to the pre-P2 behavior on next pod restart.

---

## 8. Open questions

- **psycopg `notifies()` semantics across `pgbouncer` (transaction pooling)**. `LISTEN` on a transaction-pooled connection silently breaks (the connection is recycled between statements). The dedicated listener connection MUST bypass pgbouncer or use a session-pooled connection. Document in deployment notes; verify before production rollout.
- **Inbox bus migration.** The same fix applies to `inbox_bus.py` (per-user inbox notifications). Out of scope for this PR; tracked as immediate follow-up.
- **NOTIFY in subagent paths.** Subagent events go through the same `RuntimeEventProducer.append_*_event` pipeline, so the adapter's NOTIFY hook covers them automatically. Verify in code before merge.

---

## 9. Definition of done

- New `EventBusBackend` Protocol + `InMemoryEventBus` (renamed) + `PostgresEventBus` (new) all land.
- `RUNTIME_EVENT_BUS_BACKEND` settings field flows into the factory and adapter.
- All Stage 1 acceptance tests pass.
- This PRD's status flipped to `Shipped`.
- Roadmap status checklist for P2 ticked.
