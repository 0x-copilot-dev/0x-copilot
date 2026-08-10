// Unit tests for `projectStatusLine` (PRD-B2 D6). Pure predicate: is a surface
// still being built?

import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectStatusLine } from "./statusLine";

const RUN = "a7f3c9d2e5b14f60";

let seq = 0;
beforeEach(() => {
  seq = 0;
});
function ev(
  event_type: string,
  payload: Record<string, unknown>,
): RuntimeEventEnvelope {
  seq += 1;
  return {
    event_id: `evt_${seq}`,
    run_id: RUN,
    conversation_id: "c1",
    sequence_no: seq,
    event_type: event_type as RuntimeEventEnvelope["event_type"],
    activity_kind: "tool" as RuntimeEventEnvelope["activity_kind"],
    payload,
    created_at: "2026-07-23T10:00:00Z",
  };
}

function created(surfaceId: string, connector = "linear", op = "get_issue") {
  return ev("surface.created", {
    v: 1,
    surface_id: surfaceId,
    kind: "record",
    source: { connector, op },
    title: "t",
    payload_ref: "call:c1",
  });
}

function derived(surfaceId: string) {
  return ev("view.derived", {
    v: 1,
    surface_id: surfaceId,
    tier: "generic",
    basis: "schema",
  });
}

describe("projectStatusLine", () => {
  it("is idle with no v2 events", () => {
    expect(projectStatusLine([ev("model_delta", { text: "hi" })]).kind).toBe(
      "idle",
    );
  });

  it("is assembling while a surface has no derived view yet", () => {
    expect(projectStatusLine([created("s1")]).kind).toBe("assembling");
  });

  it("goes QUIET once the view lands — the strip has nothing left to say", () => {
    // The behaviour this file exists to pin. A settled surface used to draw
    // `view.derived · linear.get_issue · ra7f·002`, which restated the
    // provenance footer above it and added a ledger coordinate no reader can
    // use. Everything it said is said better one line up.
    expect(projectStatusLine([created("s1"), derived("s1")]).kind).toBe("idle");
  });

  it("is idle for a read that produced no surface", () => {
    const line = projectStatusLine([
      ev("read.executed", {
        v: 1,
        call_id: "c1",
        connector: "linear",
        op: "get_issue",
        latency_ms: 10,
        payload_ref: "call:c1",
      }),
    ]);
    expect(line.kind).toBe("idle");
  });

  it("stays assembling while ANY surface is unresolved", () => {
    const line = projectStatusLine([
      created("s1"),
      derived("s1"),
      created("s2", "github", "get_pr"),
    ]);
    expect(line.kind).toBe("assembling");
  });

  it("is order-independent — a view seen before its create still settles", () => {
    // Set membership, not a fold over the latest event, so out-of-order
    // delivery cannot make a finished surface look unfinished.
    expect(projectStatusLine([derived("s1"), created("s1")]).kind).toBe("idle");
  });

  it("ignores a view for a surface whose create was never seen", () => {
    expect(projectStatusLine([derived("ghost")]).kind).toBe("idle");
  });

  it("degrades rather than throwing on a malformed payload", () => {
    const bad = ev("surface.created", {});
    const nulled = {
      ...ev("surface.created", {}),
      payload: null,
    } as unknown as RuntimeEventEnvelope;
    expect(() => projectStatusLine([bad, nulled])).not.toThrow();
    expect(projectStatusLine([bad, nulled]).kind).toBe("idle");
  });
});
