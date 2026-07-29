// The client's canvas fold consumes ledger events by name. If the transport
// tuple omits one, `isRuntimeEventEnvelope` rejects the frame and
// `parseEnvelope` drops it — with no error, no warning, and no trace at any
// layer. The projection that depends on it then becomes unreachable code while
// still looking correct.
//
// That is not hypothetical. `RUNTIME_LEDGER_V21_EVENT_TYPES` was assembled from
// hand-picked positional indices and skipped `artifact.presentation_decided`.
// The Studio canvas needs exactly that event to promote a published artifact
// into a canvas tab, so a real, correctly-persisted CSV rendered as "This run
// completed in chat. No artifact was created."
//
// These assert per-family reachability rather than snapshotting a list, so
// adding a ledger event that the fold consumes but the transport drops fails
// here instead of in a user's canvas. Cross-language equality against the
// backend enum is asserted separately, by
// `services/ai-backend/tests/unit/runtime_api/test_api_type_contracts.py`.

import { describe, expect, it } from "vitest";

import {
  ARTIFACT_EVENT_TYPES,
  EFFECT_EVENT_TYPES,
  GATE_V2_EVENT_TYPES,
  OPERATION_EVENT_TYPES,
} from "./ledger";
import { isRuntimeApiEventType, isRuntimeEventEnvelope } from "./index";

const envelopeFor = (eventType: string): unknown => ({
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
});

describe("ledger → transport reachability", () => {
  it.each([
    ["artifact", ARTIFACT_EVENT_TYPES],
    ["operation", OPERATION_EVENT_TYPES],
    ["effect", EFFECT_EVENT_TYPES],
    // The v2 gate pair joined this list in PRD-01. It was previously pinned as
    // deliberately unreachable, which was an accurate description of a defect
    // rather than a decision: the backend emitted GATE_OPENED_V2 from the
    // workspace grant-block path the whole time, and the emission raised.
    ["v2 gate", GATE_V2_EVENT_TYPES],
  ])("every %s event the fold consumes is transportable", (_family, types) => {
    const unreachable = types.filter((t) => !isRuntimeApiEventType(t));
    expect(unreachable).toEqual([]);
  });

  it("a full envelope survives the gate for each of those families", () => {
    const rejected = [
      ...ARTIFACT_EVENT_TYPES,
      ...OPERATION_EVENT_TYPES,
      ...EFFECT_EVENT_TYPES,
      ...GATE_V2_EVENT_TYPES,
    ].filter((t) => !isRuntimeEventEnvelope(envelopeFor(t)));
    expect(rejected).toEqual([]);
  });

  it("makes projectCanvasLifecycle's parked branch reachable", () => {
    // The fold drives `parked` from GATE_OPENED_V2 / GATE_RESOLVED_V2. While
    // those were undeliverable that branch was dead code that still compiled and
    // still passed its own unit tests against hand-built fixtures — the exact
    // shape of failure this file exists to prevent.
    for (const eventType of GATE_V2_EVENT_TYPES) {
      expect(isRuntimeEventEnvelope(envelopeFor(eventType))).toBe(true);
    }
  });
});
