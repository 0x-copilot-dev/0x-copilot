import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectSurfaceDiffs } from "./_surfaceDiffs";

let nextSeq = 0;

function makeEnvelope(
  type: string,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  const seq = nextSeq;
  nextSeq += 1;
  return {
    event_id: overrides.event_id ?? `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: overrides.sequence_no ?? seq,
    event_type: type as RuntimeEventEnvelope["event_type"],
    activity_kind: "approval",
    payload: {},
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
    ...overrides,
  };
}

function diffApproval(
  approvalId: string,
  uri: string,
  changes: readonly Record<string, unknown>[] = [{ field: "title", new: "x" }],
  extra: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return makeEnvelope("approval_requested", {
    payload: {
      approval_id: approvalId,
      display_name: "Update the record",
      server_name: "LINEAR",
      surface: {
        surface_uri: uri,
        archetype: "record",
        state: { data: {} },
        diff: { changes },
      },
      ...extra,
    },
  });
}

describe("projectSurfaceDiffs", () => {
  it("opens a pending diff from an approval_requested carrying a surface diff", () => {
    nextSeq = 0;
    const { diffs } = projectSurfaceDiffs([
      diffApproval("appr-1", "record://seed/get_issue/1"),
    ]);
    expect(diffs).toHaveLength(1);
    expect(diffs[0].diffId).toBe("appr-1");
    expect(diffs[0].uri).toBe("record://seed/get_issue/1");
    expect(diffs[0].title).toBe("Update the record");
    expect(diffs[0].provenance).toBe("LINEAR");
    expect(diffs[0].diff.changes).toEqual([{ field: "title", new: "x" }]);
  });

  it("ignores approvals without a surface diff", () => {
    nextSeq = 0;
    const { diffs } = projectSurfaceDiffs([
      // No `surface` envelope at all.
      makeEnvelope("approval_requested", {
        payload: { approval_id: "appr-plain", display_name: "Post to Slack" },
      }),
      // A surface envelope but no `diff`.
      makeEnvelope("approval_requested", {
        payload: {
          approval_id: "appr-view",
          surface: {
            surface_uri: "record://x",
            archetype: "record",
            state: { data: {} },
          },
        },
      }),
    ]);
    expect(diffs).toHaveLength(0);
  });

  it("drops a diff once its approval resolves", () => {
    nextSeq = 0;
    const { diffs } = projectSurfaceDiffs([
      diffApproval("appr-1", "record://a"),
      makeEnvelope("approval_resolved", {
        payload: { approval_id: "appr-1", decision: "approved" },
      }),
    ]);
    expect(diffs).toHaveLength(0);
  });

  it("keeps the latest unresolved diff per uri (a newer proposal supersedes)", () => {
    nextSeq = 0;
    const { diffs } = projectSurfaceDiffs([
      diffApproval("appr-1", "record://a", [{ field: "title", new: "old" }]),
      diffApproval("appr-2", "record://a", [{ field: "title", new: "new" }]),
    ]);
    expect(diffs).toHaveLength(1);
    expect(diffs[0].diffId).toBe("appr-2");
    expect(diffs[0].diff.changes).toEqual([{ field: "title", new: "new" }]);
  });

  it("orders diffs across uris newest-first", () => {
    nextSeq = 0;
    const { diffs } = projectSurfaceDiffs([
      diffApproval("appr-a", "record://a"),
      diffApproval("appr-b", "record://b"),
    ]);
    expect(diffs.map((d) => d.uri)).toEqual(["record://b", "record://a"]);
  });

  it("is idempotent on replay (dedup by event_id)", () => {
    nextSeq = 0;
    const events = [diffApproval("appr-1", "record://a")];
    const once = projectSurfaceDiffs(events);
    const twice = projectSurfaceDiffs([...events, ...events]);
    expect(twice).toEqual(once);
  });

  it("returns an empty projection for zero events", () => {
    nextSeq = 0;
    expect(projectSurfaceDiffs([]).diffs).toEqual([]);
  });
});
