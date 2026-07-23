// projectLedgerSources tests (Generative Surfaces v2, PRD-E1 / FR-E3).
//
// One row per `read.executed`, grouped by connector in first-seen order, rows in
// sequence order, each with a codec ledger id + latency (null-tolerant) + the
// "auto-ran (read)" qualifier. Hostile connector/op strings survive as plain
// strings; malformed payloads are skipped.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { formatLedgerId } from "@0x-copilot/api-types";

import { projectLedgerSources } from "./projectLedgerSources";

const RUN = "run00000001abcdef";

function read(
  seq: number,
  connector: string,
  op: string,
  extra: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return {
    event_type: "read.executed",
    run_id: RUN,
    sequence_no: seq,
    created_at: `2026-01-01T00:00:${String(seq).padStart(2, "0")}Z`,
    payload: {
      v: 1,
      call_id: `c${seq}`,
      connector,
      op,
      latency_ms: 20,
      payload_ref: `call:c${seq}`,
      ...extra,
    },
  } as unknown as RuntimeEventEnvelope;
}

describe("projectLedgerSources", () => {
  it("groups reads by connector in first-seen order, rows in sequence order", () => {
    const projection = projectLedgerSources([
      read(1, "linear", "get_issue"),
      read(2, "gmail", "list_messages"),
      read(3, "linear", "list_issues"),
    ]);
    expect(projection.total).toBe(3);
    expect(projection.groups.map((g) => g.connector)).toEqual([
      "linear",
      "gmail",
    ]);
    expect(projection.groups[0].rows.map((r) => r.op)).toEqual([
      "get_issue",
      "list_issues",
    ]);
    expect(projection.groups[0].rows[0].qualifier).toBe("auto-ran (read)");
  });

  it("formats ledger ids via the A1 codec", () => {
    const projection = projectLedgerSources([read(7, "linear", "get_issue")]);
    expect(projection.groups[0].rows[0].ledgerId).toBe(formatLedgerId(RUN, 7));
  });

  it("tolerates a missing latency (null)", () => {
    const projection = projectLedgerSources([
      read(1, "linear", "get_issue", { latency_ms: "oops" }),
    ]);
    expect(projection.groups[0].rows[0].latencyMs).toBeNull();
  });

  it("resolves a title from the surface sharing the read's payload_ref", () => {
    const surface = {
      event_type: "surface.created",
      run_id: RUN,
      sequence_no: 2,
      created_at: "2026-01-01T00:00:02Z",
      payload: {
        v: 1,
        surface_id: "s1",
        kind: "record",
        source: { connector: "linear", op: "get_issue" },
        title: "ENG-142 Fix reconnect",
        payload_ref: "shared/ref",
      },
    } as unknown as RuntimeEventEnvelope;
    const projection = projectLedgerSources([
      read(1, "linear", "get_issue", { payload_ref: "shared/ref" }),
      surface,
    ]);
    expect(projection.groups[0].rows[0].title).toBe("ENG-142 Fix reconnect");
  });

  it("keeps a hostile connector/op as a plain string", () => {
    const projection = projectLedgerSources([
      read(1, "<img src=x onerror=alert(1)>", "</script>"),
    ]);
    expect(projection.groups[0].connector).toBe("<img src=x onerror=alert(1)>");
    expect(projection.groups[0].rows[0].op).toBe("</script>");
  });

  it("returns empty on no reads", () => {
    const projection = projectLedgerSources([
      {
        event_type: "final_response",
        run_id: RUN,
        sequence_no: 1,
        created_at: "x",
        payload: {},
      } as unknown as RuntimeEventEnvelope,
    ]);
    expect(projection.total).toBe(0);
    expect(projection.groups).toEqual([]);
  });
});
