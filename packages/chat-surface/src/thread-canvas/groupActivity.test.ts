import { describe, expect, it } from "vitest";

import {
  GROUP_MIN_MEMBERS,
  groupActivityStream,
  summariseGroup,
} from "./groupActivity";

/** A stand-in for `TcChat`'s `StreamItem` — the fold is generic over it. */
type Item = { readonly kind: string; readonly id: string };

const msg = (id: string): Item => ({ kind: "message", id });
const approval = (id: string): Item => ({ kind: "approval", id });
const tool = (id: string): Item => ({ kind: "tool", id });
const fleet = (id: string): Item => ({ kind: "fleet", id });

/** The opt-in the cockpit uses: only tool + fleet are groupable. */
const OPTS = {
  isGroupable: (i: Item) => i.kind === "tool" || i.kind === "fleet",
  idOf: (i: Item) => i.id,
};
const fold = (items: readonly Item[]) => groupActivityStream(items, OPTS);

const kinds = (out: readonly { kind: string }[]) => out.map((o) => o.kind);

describe("groupActivityStream", () => {
  it("groups a consecutive run of activity between two messages", () => {
    const out = fold([
      msg("u1"),
      tool("t1"),
      tool("t2"),
      tool("t3"),
      msg("a1"),
    ]);
    expect(kinds(out)).toEqual(["passthrough", "group", "passthrough"]);
    const group = out[1] as unknown as { members: unknown[] };
    expect(group.members).toHaveLength(3);
  });

  it("leaves a LONE activity item unwrapped (D-3.4)", () => {
    // Wrapping one card in a group adds a frame to save nothing.
    const out = fold([msg("u1"), tool("t1"), msg("a1")]);
    expect(kinds(out)).toEqual(["passthrough", "solo", "passthrough"]);
    expect(GROUP_MIN_MEMBERS).toBe(2);
  });

  it("starts a new group per turn — a message always breaks the run", () => {
    const out = fold([
      msg("u1"),
      tool("t1"),
      tool("t2"),
      msg("a1"),
      msg("u2"),
      tool("t3"),
      tool("t4"),
      msg("a2"),
    ]);
    expect(kinds(out)).toEqual([
      "passthrough",
      "group",
      "passthrough",
      "passthrough",
      "group",
      "passthrough",
    ]);
  });

  it("mixes fleets and tool calls into the same run", () => {
    const out = fold([tool("t1"), fleet("f1"), tool("t2")]);
    expect(kinds(out)).toEqual(["group"]);
    expect((out[0] as unknown as { members: unknown[] }).members).toHaveLength(
      3,
    );
  });

  it("never reorders the transcript", () => {
    const input = [msg("u1"), tool("t1"), tool("t2"), msg("a1"), tool("t3")];
    const out = fold(input);
    // Flatten back out and the original order must be recovered exactly — the
    // fold changes framing, never reading order.
    const flat: string[] = [];
    for (const entry of out) {
      if (entry.kind === "group")
        for (const m of entry.members) flat.push(m.id);
      else flat.push(entry.item.id);
    }
    expect(flat).toEqual(["u1", "t1", "t2", "a1", "t3"]);
  });

  // The reason the fold is opt-in. While PRD-03 was being written, `mergeStream`
  // gained an `approval` kind; a fold that enumerated BOUNDARIES would have
  // swallowed it into a collapsed group and hidden a parked run's only control.
  it("never folds an approval into a group, and lets it break the run", () => {
    const out = fold([
      tool("t1"),
      tool("t2"),
      approval("ap1"),
      tool("t3"),
      tool("t4"),
    ]);
    expect(kinds(out)).toEqual(["group", "passthrough", "group"]);
    const flat = out.flatMap((e) =>
      e.kind === "group" ? e.members.map((x) => x.id) : [e.item.id],
    );
    expect(flat).toEqual(["t1", "t2", "ap1", "t3", "t4"]);
  });

  it("passes an UNKNOWN kind through rather than hiding it", () => {
    // A kind added later defaults to safe: visible and ungrouped.
    const out = fold([tool("t1"), { kind: "brand-new", id: "x" }, tool("t2")]);
    expect(kinds(out)).toEqual(["solo", "passthrough", "solo"]);
  });

  it("handles an empty transcript and an activity-only transcript", () => {
    expect(fold([])).toEqual([]);
    expect(kinds(fold([tool("t1"), tool("t2")]))).toEqual(["group"]);
  });
});

const m = (over: Record<string, unknown> = {}) => ({
  status: "complete",
  createdAtMs: 1000,
  durationMs: 200,
  ...over,
});

describe("summariseGroup", () => {
  it("reports running while any member is in flight", () => {
    const s = summariseGroup([
      m(),
      m({ status: "running", durationMs: undefined }),
    ]);
    expect(s.state).toBe("running");
    expect(s.done).toBe(1);
    expect(s.total).toBe(2);
  });

  it("settles once every member has", () => {
    const s = summariseGroup([m(), m()]);
    expect(s.state).toBe("settled");
    expect(s.done).toBe(2);
  });

  it("reports failed from the RUN's terminal state, not a member's", () => {
    // A single errored step the agent worked around must NOT hold the group
    // open — that is a `retried`, and PRD-04 governs how it looks inside.
    const recovered = summariseGroup([m({ status: "error" }), m()]);
    expect(recovered.state).toBe("settled");
    expect(recovered.retried).toBe(1);

    const failed = summariseGroup([m({ status: "error" })], true);
    expect(failed.state).toBe("failed");
    // Nothing is claimed as "retried" when the run did not in fact recover.
    expect(failed.retried).toBe(0);
  });

  it("derives elapsed across the whole run, not per member", () => {
    const s = summariseGroup([
      m({ createdAtMs: 1000, durationMs: 200 }),
      m({ createdAtMs: 1500, durationMs: 700 }),
    ]);
    expect(s.elapsedMs).toBe(1200); // 1000 → 2200
  });

  it("returns null elapsed when timestamps are unknowable", () => {
    const s = summariseGroup([m({ createdAtMs: null, durationMs: undefined })]);
    expect(s.elapsedMs).toBeNull();
  });
});
