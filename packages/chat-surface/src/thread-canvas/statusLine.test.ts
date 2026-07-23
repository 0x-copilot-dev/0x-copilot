// Unit tests for `projectStatusLine` (PRD-B2 D6). Pure fold to the run's latest
// consequential ledger beat.

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

describe("projectStatusLine", () => {
  it("is idle with no v2 events", () => {
    const line = projectStatusLine([ev("model_delta", { text: "hi" })]);
    expect(line.kind).toBe("idle");
    expect(line.ledgerId).toBeNull();
  });

  it("reports the latest op line with connector.op and ledger id", () => {
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
    // read.executed with no following surface ⇒ not assembling.
    expect(line.kind).toBe("op");
    expect(line.text).toBe("read.executed · linear.get_issue · ra7f·001");
    expect(line.ledgerId).toBe("ra7f·001");
  });

  it("is assembling while a surface has no derived view yet", () => {
    const line = projectStatusLine([
      ev("surface.created", {
        v: 1,
        surface_id: "s1",
        kind: "record",
        source: { connector: "linear", op: "get_issue" },
        title: "t",
        payload_ref: "call:c1",
      }),
    ]);
    expect(line.kind).toBe("assembling");
    expect(line.text).toContain("surface.created · linear.get_issue");
  });

  it("resolves connector.op for a latest view.derived via its surface.created", () => {
    const line = projectStatusLine([
      ev("surface.created", {
        v: 1,
        surface_id: "s1",
        kind: "record",
        source: { connector: "github", op: "get_pr" },
        title: "t",
        payload_ref: "call:c1",
      }),
      ev("view.derived", {
        v: 1,
        surface_id: "s1",
        tier: "generic",
        basis: "schema",
      }),
    ]);
    // view.derived is the latest; the surface now has a view ⇒ op (not assembling).
    expect(line.kind).toBe("op");
    expect(line.text).toBe("view.derived · github.get_pr · ra7f·002");
  });

  it("omits the connector.op segment when a view.derived cannot resolve it", () => {
    // view.derived for a surface whose create was never seen ⇒ no source.
    const line = projectStatusLine([
      ev("view.derived", {
        v: 1,
        surface_id: "ghost",
        tier: "generic",
        basis: "schema",
      }),
    ]);
    expect(line.text).toBe("view.derived · ra7f·001");
  });
});
