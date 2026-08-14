// The host-write projection: the unit a person acts on, and the receipt.
//
// Every assertion here is pinned to something the SERVER does, because the two
// halves have to agree or the surface promises an undo the route will not
// deliver. Each `it` names the producer rule it mirrors.

import type {
  HostWriteEntry,
  HostWriteRevertReport,
} from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import {
  groupHostWrites,
  hostWriteFileName,
  hostWriteKindLabel,
  summariseRevert,
  UNBOUND_HOST_WRITE_KEY,
} from "./hostWrites";

function entry(over: Partial<HostWriteEntry> & { sequence: number }) {
  return {
    entry_id: `e${over.sequence}`,
    tool_call_id: "call_a",
    path: "/Users/x/notes.md",
    kind: "modified",
    prior_size: 12,
    revertible: true,
    captured_at: "2026-01-01T00:00:00Z",
    ...over,
  } as HostWriteEntry;
}

describe("groupHostWrites — the unit is the tool call, because that is what the route takes", () => {
  it("puts one group per tool call, oldest group first", () => {
    const groups = groupHostWrites([
      entry({ sequence: 3, tool_call_id: "call_b", path: "/a/b.txt" }),
      entry({ sequence: 1, tool_call_id: "call_a", path: "/a/a.txt" }),
      entry({ sequence: 2, tool_call_id: "call_a", path: "/a/c.txt" }),
    ]);
    expect(groups.map((g) => g.toolCallId)).toEqual(["call_a", "call_b"]);
    expect(groups[0]!.entries).toHaveLength(2);
    expect(groups[0]!.firstSequence).toBe(1);
  });

  // `HostWriteReverter.select` keeps the OLDEST record per PATH, so two writes
  // to one file collapse to one restore. A count of records would print a
  // number the Undo button cannot deliver.
  it("counts distinct PATHS, not journal records", () => {
    const [group] = groupHostWrites([
      entry({ sequence: 1, path: "/a/same.txt" }),
      entry({ sequence: 2, path: "/a/same.txt" }),
      entry({ sequence: 3, path: "/a/other.txt" }),
    ]);
    expect(group!.entries).toHaveLength(3);
    expect(group!.pathCount).toBe(2);
  });

  // `tool_call_id: null` means the write happened outside a bound tool call and
  // is reachable ONLY through a whole-run revert — which this surface does not
  // offer. It is still listed; it just carries no control.
  it("buckets unattributed writes under a sentinel that cannot be undone here", () => {
    const [group] = groupHostWrites([
      entry({ sequence: 1, tool_call_id: null }),
    ]);
    expect(group!.key).toBe(UNBOUND_HOST_WRITE_KEY);
    expect(group!.toolCallId).toBeNull();
    expect(group!.undoable).toBe(false);
  });

  // The server already knows it stored no pre-image; a revert would answer
  // `not_revertible` for every row. Offering the button would be promising an
  // undo the backend has said it cannot perform.
  it("withholds the control when nothing in the group has a stored pre-image", () => {
    const [group] = groupHostWrites([
      entry({ sequence: 1, revertible: false, path: "/a/1" }),
      entry({ sequence: 2, revertible: false, path: "/a/2" }),
    ]);
    expect(group!.undoable).toBe(false);
  });

  it("offers it when even one entry is revertible", () => {
    const [group] = groupHostWrites([
      entry({ sequence: 1, revertible: false, path: "/a/1" }),
      entry({ sequence: 2, revertible: true, path: "/a/2" }),
    ]);
    expect(group!.undoable).toBe(true);
  });
});

describe("summariseRevert — the receipt, which is the only record the user gets", () => {
  function report(
    outcomes: HostWriteRevertReport["outcomes"],
  ): HostWriteRevertReport {
    return { run_id: "run-1", tool_call_id: "call_a", outcomes };
  }

  it("counts only restored/removed as undone — mirroring HostWriteRevertReport.reverted", () => {
    const summary = summariseRevert(
      report([
        { path: "/a/1", kind: "modified", status: "restored" },
        { path: "/a/2", kind: "created", status: "removed" },
        { path: "/a/3", kind: "modified", status: "refused" },
      ]),
    );
    expect(summary.undone).toBe(2);
    expect(summary.total).toBe(3);
    expect(summary.complete).toBe(false);
    expect(summary.headline).toContain("Partly undone");
  });

  // A client that treated an unrecognised status as success would tell the user
  // their file came back when the server said something else entirely.
  it("treats a status it has never heard of as NOT undone", () => {
    const summary = summariseRevert(
      report([{ path: "/a/1", kind: "modified", status: "quarantined" }]),
    );
    expect(summary.rows[0]!.undone).toBe(false);
    expect(summary.undone).toBe(0);
    expect(summary.headline).toContain("Nothing was undone");
  });

  it("says nothing came back rather than looking successful", () => {
    const summary = summariseRevert(
      report([
        {
          path: "/a/1",
          kind: "modified",
          status: "not_revertible",
          detail: "no captured prior content",
        },
      ]),
    );
    expect(summary.complete).toBe(false);
    expect(summary.rows[0]!.detail).toBe("no captured prior content");
  });

  it("keeps the server's status word verbatim rather than re-spelling it", () => {
    const summary = summariseRevert(
      report([{ path: "/a/1", kind: "deleted", status: "restored" }]),
    );
    expect(summary.rows[0]!.status).toBe("restored");
    expect(summary.complete).toBe(true);
  });

  it("reports an empty selection as nothing to undo, not as a success", () => {
    const summary = summariseRevert(report([]));
    expect(summary.complete).toBe(false);
    expect(summary.headline).toContain("Nothing to undo");
  });
});

describe("labels", () => {
  it("names the three kinds the journal can record", () => {
    expect(hostWriteKindLabel("created")).toBe("Created");
    expect(hostWriteKindLabel("deleted")).toBe("Deleted");
    expect(hostWriteKindLabel("modified")).toBe("Modified");
  });

  it("takes the trailing filename without inventing an ellipsis", () => {
    expect(hostWriteFileName("/Users/x/Notes/plan.md")).toBe("plan.md");
    expect(hostWriteFileName("/Users/x/Notes/")).toBe("Notes");
    expect(hostWriteFileName("plan.md")).toBe("plan.md");
  });
});
