// PR 1.1-rev2 — citationLinkReducer test pins.
//
// The reducer keys CitationLink rows by (run_id, message_id, prose_offset)
// so:
//   - re-deliveries of the same `citation_made` event are idempotent,
//   - the same ordinal cited at two distinct prose offsets produces two
//     distinct entries (the chips on screen point to the same source),
//   - cross-message + cross-run lookups stay isolated.

import { describe, expect, it } from "vitest";
import type { CitationLink, RuntimeEventEnvelope } from "@0x-copilot/api-types";
import {
  applyCitationLinkEvent,
  anyLinkForOrdinalInRun,
  buildCitationLinkRegistry,
  emptyCitationLinkRegistry,
  linkForOrdinal,
  linksForMessage,
  linksForRun,
  upsertCitationLink,
} from "./citationLinkReducer";

const RUN = "run_1";
const RUN_OTHER = "run_2";
const MSG_A = "msg_a";
const MSG_B = "msg_b";

function link(overrides: Partial<CitationLink>): CitationLink {
  return {
    conversation_ordinal: 1,
    message_id: MSG_A,
    prose_offset: 0,
    prose_length: 5,
    source_tool_call_id: "call_one",
    ...overrides,
  };
}

function citationMadeEvent(
  link: CitationLink,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  return {
    event_id: "ev1",
    event_type: "citation_made",
    run_id: RUN,
    conversation_id: "conv_1",
    org_id: "org_1",
    sequence_no: 1,
    created_at: "2025-01-01T00:00:00Z",
    payload: { link },
    metadata: {},
    activity_kind: "tool",
    visibility: "user",
    redaction_state: "none",
    ...overrides,
  } as unknown as RuntimeEventEnvelope;
}

describe("applyCitationLinkEvent", () => {
  it("indexes a link by (run_id, message_id, prose_offset)", () => {
    const before = emptyCitationLinkRegistry();
    const after = applyCitationLinkEvent(
      before,
      citationMadeEvent(link({ conversation_ordinal: 1 })),
    );
    const offsets = linksForMessage(after, RUN, MSG_A);
    expect(offsets.size).toBe(1);
    expect(offsets.get(0)?.conversation_ordinal).toBe(1);
  });

  it("is idempotent on duplicate event delivery", () => {
    const a = applyCitationLinkEvent(
      emptyCitationLinkRegistry(),
      citationMadeEvent(link({ conversation_ordinal: 7 })),
    );
    const b = applyCitationLinkEvent(
      a,
      citationMadeEvent(link({ conversation_ordinal: 7 })),
    );
    // Same row, same registry reference — no churn for downstream
    // memoization.
    expect(b).toBe(a);
  });

  it("two offsets in one message produce two entries for the same ordinal", () => {
    let registry = emptyCitationLinkRegistry();
    registry = applyCitationLinkEvent(
      registry,
      citationMadeEvent(link({ conversation_ordinal: 3, prose_offset: 0 })),
    );
    registry = applyCitationLinkEvent(
      registry,
      citationMadeEvent(link({ conversation_ordinal: 3, prose_offset: 9 })),
    );
    const offsets = linksForMessage(registry, RUN, MSG_A);
    expect(offsets.size).toBe(2);
  });

  it("isolates by run_id and message_id", () => {
    let registry = emptyCitationLinkRegistry();
    registry = applyCitationLinkEvent(
      registry,
      citationMadeEvent(link({ conversation_ordinal: 1, message_id: MSG_A })),
    );
    registry = applyCitationLinkEvent(
      registry,
      citationMadeEvent(link({ conversation_ordinal: 2, message_id: MSG_B })),
    );
    registry = applyCitationLinkEvent(
      registry,
      citationMadeEvent(link({ conversation_ordinal: 9, message_id: MSG_A }), {
        run_id: RUN_OTHER,
      }),
    );
    expect(linksForMessage(registry, RUN, MSG_A).size).toBe(1);
    expect(linksForMessage(registry, RUN, MSG_B).size).toBe(1);
    expect(linksForMessage(registry, RUN_OTHER, MSG_A).size).toBe(1);
  });

  it("rejects malformed citation_made payloads", () => {
    const before = emptyCitationLinkRegistry();
    const malformed = {
      ...citationMadeEvent(link({})),
      payload: { link: { conversation_ordinal: "not a number" } },
    } as unknown as RuntimeEventEnvelope;
    const after = applyCitationLinkEvent(before, malformed);
    expect(after).toBe(before);
  });

  it("ignores events that are not citation_made", () => {
    const before = emptyCitationLinkRegistry();
    const event = {
      ...citationMadeEvent(link({})),
      event_type: "model_delta",
    } as unknown as RuntimeEventEnvelope;
    const after = applyCitationLinkEvent(before, event);
    expect(after).toBe(before);
  });

  it("accepts events that omit source_tool_call_id (replay of pre-fix data)", () => {
    // Regression pin: events persisted before the projector defaulted
    // ``source_tool_call_id`` to ``""`` arrive without the field on
    // replay. Old conversations whose chips currently render as ``?``
    // must resolve once the FE picks up the lenient guard.
    const before = emptyCitationLinkRegistry();
    const event = {
      ...citationMadeEvent(link({})),
      payload: {
        link: {
          conversation_ordinal: 4,
          message_id: "msg_old",
          prose_offset: 0,
          prose_length: 5,
          // source_tool_call_id intentionally omitted.
        },
      },
    } as unknown as RuntimeEventEnvelope;
    const after = applyCitationLinkEvent(before, event);
    const offsets = linksForMessage(after, RUN, "msg_old");
    expect(offsets.size).toBe(1);
    const stored = offsets.get(0);
    expect(stored?.conversation_ordinal).toBe(4);
    // Coerced to empty string so downstream consumers (chip,
    // ``citedToolSources``) can rely on a ``string`` typing.
    expect(stored?.source_tool_call_id).toBe("");
  });

  it("accepts events whose source_tool_call_id is null", () => {
    const before = emptyCitationLinkRegistry();
    const event = {
      ...citationMadeEvent(link({})),
      payload: {
        link: {
          conversation_ordinal: 5,
          message_id: "msg_null",
          prose_offset: 0,
          prose_length: 5,
          source_tool_call_id: null,
        },
      },
    } as unknown as RuntimeEventEnvelope;
    const after = applyCitationLinkEvent(before, event);
    const offsets = linksForMessage(after, RUN, "msg_null");
    expect(offsets.get(0)?.source_tool_call_id).toBe("");
  });
});

describe("buildCitationLinkRegistry", () => {
  it("replays a stream of events into a registry", () => {
    const registry = buildCitationLinkRegistry([
      citationMadeEvent(link({ conversation_ordinal: 1, prose_offset: 0 })),
      citationMadeEvent(link({ conversation_ordinal: 2, prose_offset: 9 })),
    ]);
    expect(linksForRun(registry, RUN)).toHaveLength(2);
  });
});

describe("upsertCitationLink", () => {
  it("preserves the existing run map when no change is needed", () => {
    const link1 = link({ conversation_ordinal: 1 });
    const initial = upsertCitationLink(emptyCitationLinkRegistry(), RUN, link1);
    const same = upsertCitationLink(initial, RUN, link1);
    expect(same).toBe(initial);
  });
});

describe("linkForOrdinal / anyLinkForOrdinalInRun", () => {
  it("looks up a link by ordinal within a single message", () => {
    const registry = upsertCitationLink(
      emptyCitationLinkRegistry(),
      RUN,
      link({ conversation_ordinal: 5 }),
    );
    expect(linkForOrdinal(registry, RUN, MSG_A, 5)?.source_tool_call_id).toBe(
      "call_one",
    );
    expect(linkForOrdinal(registry, RUN, MSG_A, 6)).toBeUndefined();
  });

  it("looks up the first matching ordinal across all messages of a run", () => {
    let registry = emptyCitationLinkRegistry();
    registry = upsertCitationLink(
      registry,
      RUN,
      link({ conversation_ordinal: 1, message_id: MSG_A }),
    );
    registry = upsertCitationLink(
      registry,
      RUN,
      link({
        conversation_ordinal: 5,
        message_id: MSG_B,
        source_tool_call_id: "call_other",
      }),
    );
    const found = anyLinkForOrdinalInRun(registry, RUN, 5);
    expect(found?.source_tool_call_id).toBe("call_other");
    expect(anyLinkForOrdinalInRun(registry, RUN, 99)).toBeUndefined();
  });
});
