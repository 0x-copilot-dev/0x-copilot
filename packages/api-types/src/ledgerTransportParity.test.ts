// Every ledger event the backend can persist to a run must survive the
// client's envelope gate.
//
// `useRunSession` parses each SSE frame through `isRuntimeEventEnvelope`, which
// rejects any `event_type` missing from `RUNTIME_API_EVENT_TYPES` — and a
// rejected frame is dropped with no error, no warning, and no trace. A gap here
// is therefore invisible at every layer: the backend logs a successful append,
// the stream carries the frame, and the client simply never sees it.
//
// That is not hypothetical. `RUNTIME_LEDGER_V21_EVENT_TYPES` was assembled from
// hand-picked positional indices and skipped `artifact.presentation_decided`.
// The Studio canvas needs exactly that event to promote a published artifact
// into a canvas tab, so a real, correctly-persisted CSV artifact rendered as
// "This run completed in chat. No artifact was created."
//
// Parity, not a snapshot: this asserts the relationship between the two tuples,
// so adding a ledger event without making it transportable fails here rather
// than in a user's canvas.

import { describe, expect, it } from "vitest";

import { LEDGER_EVENT_TYPES } from "./ledger";
import {
  RUNTIME_API_EVENT_TYPES,
  isRuntimeApiEventType,
  isRuntimeEventEnvelope,
} from "./index";

describe("ledger → transport parity", () => {
  it("every ledger event type is transportable", () => {
    const missing = LEDGER_EVENT_TYPES.filter(
      (eventType) => !isRuntimeApiEventType(eventType),
    );
    expect(missing).toEqual([]);
  });

  it("every ledger event type is present in the transport tuple", () => {
    const transport = new Set<string>(RUNTIME_API_EVENT_TYPES);
    const missing = LEDGER_EVENT_TYPES.filter(
      (eventType) => !transport.has(eventType),
    );
    expect(missing).toEqual([]);
  });

  it("a real artifact envelope survives the gate for every ledger type", () => {
    // The full envelope shape the runtime emits, exercised per event type so a
    // gap fails as a named event rather than one opaque boolean.
    const rejected = LEDGER_EVENT_TYPES.filter(
      (eventType) =>
        !isRuntimeEventEnvelope({
          event_id: "evt_1",
          run_id: "run_1",
          conversation_id: "conv_1",
          sequence_no: 1,
          event_type: eventType,
          source: "runtime",
          activity_kind: "event",
          payload: {},
          metadata: {},
          created_at: "2026-07-28T00:00:00Z",
        }),
    );
    expect(rejected).toEqual([]);
  });
});
