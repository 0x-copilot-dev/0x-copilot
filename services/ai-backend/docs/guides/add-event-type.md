# Guide: Add a New Event Type

Events are the primary interface between the worker and the frontend. Adding a new
event type means touching the schema, the worker emitter, the SSE presentation
projector, and possibly the frontend consumer.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `RuntimeEventEnvelope` structure
- [reference/event-types.md](../reference/event-types.md) — full event type enum
- [features/streaming-sse.md](../features/streaming-sse.md) — how events reach the browser

---

## Step 1 — Add to the enum

`runtime_api/schemas/common.py` — `RuntimeApiEventType`

```python
class RuntimeApiEventType(str, Enum):
    ...
    MY_NEW_EVENT = "my_new_event"
```

The string value is the `event_type` field in the persisted envelope and the SSE
`data` payload. It must be snake_case and stable — renaming it is a breaking change
because clients and replayed events reference it.

---

## Step 2 — Define the payload shape

`runtime_api/schemas/events.py` — add a method to `RuntimeEventPresentationProjector`
if the payload needs normalisation:

```python
@classmethod
def _my_new_event_payload(cls, payload: JsonObject) -> JsonObject:
    # extract only the fields the frontend should receive
    return {
        "field_a": payload.get("field_a"),
        "field_b": payload.get("field_b"),
    }
```

Then wire it in `payload_for_event()`:

```python
if event_type is RuntimeApiEventType.MY_NEW_EVENT:
    return cls._my_new_event_payload(payload)
```

If your event needs a specific `activity_kind`, `display_title`, or `status` projected
into `presentation`, add a case in `presentation_fields()`.

---

## Step 3 — Emit from the worker

Find the right place in the worker stream path:

| If triggered by…           | File to edit                                               |
| -------------------------- | ---------------------------------------------------------- |
| LangGraph stream chunk     | `runtime_worker/stream_events.py` — `StreamOrchestrator`   |
| A tool invocation          | `runtime_worker/stream_tools.py`                           |
| A subagent lifecycle event | `runtime_worker/stream_subagents.py`                       |
| A handler directly         | `runtime_worker/handlers/run.py` or `handlers/approval.py` |
| A background job           | The relevant job file in `runtime_worker/jobs/`            |

Emit via `RuntimeEventProducer.append_api_event()`:

```python
await self.event_producer.append_api_event(
    run=run,
    event_type=RuntimeApiEventType.MY_NEW_EVENT,
    source=StreamEventSource.WORKER,
    payload={"field_a": ..., "field_b": ...},
    metadata={},
)
```

`append_api_event` persists the envelope, assigns the next `sequence_no`, and
calls `event_bus.notify_sync(run_id)` to wake SSE adapters. You do not need
to do these steps manually.

---

## Step 4 — Set visibility

`RuntimeEventVisibility` controls who receives the event:

| Visibility | Meaning                                                            |
| ---------- | ------------------------------------------------------------------ |
| `USER`     | Sent to the browser via SSE (default)                              |
| `INTERNAL` | Persisted but not sent to the browser                              |
| `AUDIT`    | Persisted for compliance; not sent to browser; different retention |

Set it in `append_api_event(visibility=RuntimeEventVisibility.INTERNAL)` if the
event is not meant for the frontend.

---

## Step 5 — Write tests

`tests/unit/runtime_worker/` or `tests/unit/agent_runtime/api/`

Test that:

1. The event is emitted with the correct `event_type` value.
2. The payload shape matches what you defined in Step 2.
3. The `presentation` fields are projected correctly.

```python
@pytest.mark.asyncio
async def test_my_new_event_is_emitted():
    store = FakeEventStore()
    producer = RuntimeEventProducer(event_store=store, ...)

    await producer.append_api_event(
        run=_fake_run(),
        event_type=RuntimeApiEventType.MY_NEW_EVENT,
        source=StreamEventSource.WORKER,
        payload={"field_a": "x"},
        metadata={},
    )

    [envelope] = store.events
    assert envelope.event_type == RuntimeApiEventType.MY_NEW_EVENT
    assert envelope.payload["field_a"] == "x"
```

---

## Step 6 — Update docs

Add a row to [reference/event-types.md](../reference/event-types.md).

---

## Checklist

- [ ] `RuntimeApiEventType.MY_NEW_EVENT = "my_new_event"` added to enum
- [ ] Payload shape defined / projected in `RuntimeEventPresentationProjector`
- [ ] Emitted via `RuntimeEventProducer.append_api_event()` — not directly to the store
- [ ] Visibility set correctly (`USER` / `INTERNAL` / `AUDIT`)
- [ ] Unit test verifies event type, payload shape, and presentation fields
- [ ] [reference/event-types.md](../reference/event-types.md) updated
