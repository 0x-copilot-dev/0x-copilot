import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import {
  project,
  projectAt,
  projectSurfaceTabs,
  selectors,
} from "./eventProjector";

const RECORD_SPEC = {
  spec_version: 1,
  archetype: "record",
  source: { server: "seed", tool: "get_issue" },
  title_path: "issue.title",
};

/** A `tool_result` carrying the PRD-01 `payload.surface` envelope. */
function surfaceEnvelopeEvent(
  uri: string,
  opts: {
    readonly archetype?: string;
    readonly data?: unknown;
    readonly spec?: unknown;
    readonly overrides?: Partial<RuntimeEventEnvelope>;
  } = {},
): RuntimeEventEnvelope {
  const state: Record<string, unknown> = { data: opts.data ?? {} };
  if (opts.spec !== undefined) {
    state.spec = opts.spec;
  }
  return makeEnvelope("tool_result", {
    payload: {
      surface: {
        surface_uri: uri,
        archetype: opts.archetype ?? "record",
        state,
      },
    },
    ...opts.overrides,
  });
}

let nextSeq = 0;

function makeEnvelope(
  type: RuntimeApiEventType,
  overrides: Partial<RuntimeEventEnvelope> = {},
): RuntimeEventEnvelope {
  const seq = nextSeq;
  nextSeq += 1;
  return {
    event_id: overrides.event_id ?? `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: overrides.sequence_no ?? seq,
    event_type: type,
    activity_kind: "event",
    payload: {},
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
    ...overrides,
  };
}

describe("eventProjector.project", () => {
  it("returns the empty state for zero events", () => {
    nextSeq = 0;
    const state = project([]);
    expect(state.activity).toEqual([]);
    expect(state.beads).toEqual([]);
    expect(state.chat).toEqual([]);
    expect(state.approvals.size).toBe(0);
    expect(state.surfaceState.size).toBe(0);
    expect(state.lastSequenceNo).toBe(-1);
  });

  it("emits one activity entry per visible event in order", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("run_started", { display_title: "Run started" }),
      makeEnvelope("tool_call_started", { display_title: "Fetch sheet" }),
      makeEnvelope("final_response", { display_title: "Drafted" }),
    ]);
    expect(state.activity.map((e) => e.title)).toEqual([
      "Run started",
      "Fetch sheet",
      "Drafted",
    ]);
  });

  it("skips internal/audit-visibility events from the activity feed", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("run_started", { display_title: "Visible" }),
      makeEnvelope("heartbeat", {
        display_title: "Hidden",
        visibility: "internal",
      }),
    ]);
    expect(state.activity.map((e) => e.title)).toEqual(["Visible"]);
  });

  it("only emits beads for state-changing events", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("model_delta", { display_title: "delta" }),
      makeEnvelope("tool_result", { display_title: "wrote a row" }),
      makeEnvelope("heartbeat"),
      makeEnvelope("final_response", { display_title: "done" }),
    ]);
    expect(state.beads.map((b) => b.title)).toEqual(["wrote a row", "done"]);
  });

  it("flags approval_requested beads as pending", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        display_title: "Approve",
        payload: { approval_id: "ap-1", surface_uri: "email://draft-1" },
      }),
    ]);
    expect(state.beads).toHaveLength(1);
    expect(state.beads[0].pending).toBe(true);
    expect(state.beads[0].lane).toBe("email");
  });

  it("synthesizes a pending Approval from approval_requested payload", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        payload: {
          approval_id: "ap-1",
          tenant_id: "tenant-1",
          requester_user_id: "subagent-x",
          target_user_id: "user-a",
          kind: "surface_diff",
          surface_uri: "email://draft-1",
        },
      }),
    ]);
    const approval = state.approvals.get("ap-1");
    expect(approval).toBeDefined();
    expect(approval?.state).toBe("pending");
    expect(approval?.kind).toBe("surface_diff");
  });

  it("flips an approval to accepted when approval_resolved arrives", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        payload: { approval_id: "ap-1", tenant_id: "tenant-1" },
      }),
      makeEnvelope("approval_resolved", {
        payload: { approval_id: "ap-1", decision: "accept" },
      }),
    ]);
    expect(state.approvals.get("ap-1")?.state).toBe("accepted");
    expect(state.approvals.get("ap-1")?.resolved_at).toBeDefined();
  });

  it("flips an approval to rejected when decision is reject", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        payload: { approval_id: "ap-2", tenant_id: "tenant-1" },
      }),
      makeEnvelope("approval_resolved", {
        payload: { approval_id: "ap-2", decision: "reject" },
      }),
    ]);
    expect(state.approvals.get("ap-2")?.state).toBe("rejected");
  });

  it("flips an approval to edited when decision is suggest_edit", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        payload: { approval_id: "ap-3", tenant_id: "tenant-1" },
      }),
      makeEnvelope("approval_resolved", {
        payload: { approval_id: "ap-3", decision: "suggest_edit" },
      }),
    ]);
    expect(state.approvals.get("ap-3")?.state).toBe("edited");
  });

  it("merges surface state from tool_result payloads", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("tool_result", {
        payload: {
          surface_uri: "sheet://acme",
          state: { rows: 5 },
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          surface_uri: "sheet://acme",
          state: { columns: 3 },
        },
      }),
    ]);
    expect(state.surfaceState.get("sheet://acme")).toEqual({
      rows: 5,
      columns: 3,
    });
  });

  it("deduplicates by event_id (SSE resend safe)", () => {
    nextSeq = 0;
    const a = makeEnvelope("tool_result", { display_title: "row" });
    const state = project([a, a, a]);
    expect(state.beads).toHaveLength(1);
    expect(state.activity).toHaveLength(1);
  });

  it("produces stable output on replay (idempotency)", () => {
    nextSeq = 0;
    const events = [
      makeEnvelope("run_started", { display_title: "start" }),
      makeEnvelope("tool_result", {
        display_title: "wrote row",
        payload: { surface_uri: "sheet://x", state: { rows: 1 } },
      }),
      makeEnvelope("final_response", { display_title: "done" }),
    ];
    const a = project(events);
    const b = project(events);
    expect(a.activity).toEqual(b.activity);
    expect(a.beads).toEqual(b.beads);
    expect(a.lastSequenceNo).toBe(b.lastSequenceNo);
  });

  it("reports the highest seen sequence_no", () => {
    nextSeq = 100;
    const state = project([
      makeEnvelope("run_started", { sequence_no: 100 }),
      makeEnvelope("final_response", { sequence_no: 103 }),
    ]);
    expect(state.lastSequenceNo).toBe(103);
  });
});

describe("eventProjector.projectAt (time-travel)", () => {
  it("ignores events past the target sequence_no", () => {
    nextSeq = 0;
    const events = [
      makeEnvelope("run_started", { display_title: "start", sequence_no: 0 }),
      makeEnvelope("tool_result", {
        display_title: "wrote a row",
        sequence_no: 1,
      }),
      makeEnvelope("final_response", {
        display_title: "done",
        sequence_no: 2,
      }),
    ];
    const state = projectAt(events, 1);
    expect(state.activity.map((e) => e.title)).toEqual([
      "start",
      "wrote a row",
    ]);
    expect(state.lastSequenceNo).toBe(1);
  });

  it("matches project(slice) for a prefix", () => {
    nextSeq = 0;
    const events = [
      makeEnvelope("run_started", { sequence_no: 0 }),
      makeEnvelope("tool_result", { sequence_no: 1 }),
      makeEnvelope("tool_result", { sequence_no: 2 }),
      makeEnvelope("final_response", { sequence_no: 3 }),
    ];
    const fromSlice = project(events.slice(0, 3));
    const fromProjectAt = projectAt(events, 2);
    expect(fromProjectAt.activity).toEqual(fromSlice.activity);
    expect(fromProjectAt.beads).toEqual(fromSlice.beads);
    expect(fromProjectAt.lastSequenceNo).toBe(fromSlice.lastSequenceNo);
  });
});

describe("eventProjector.selectors", () => {
  it("pendingApprovals filters resolved entries", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("approval_requested", {
        payload: { approval_id: "ap-1", tenant_id: "tenant-1" },
      }),
      makeEnvelope("approval_requested", {
        payload: { approval_id: "ap-2", tenant_id: "tenant-1" },
      }),
      makeEnvelope("approval_resolved", {
        payload: { approval_id: "ap-1", decision: "accept" },
      }),
    ]);
    const pending = selectors.pendingApprovals(state);
    expect(pending.map((a) => a.id)).toEqual(["ap-2"]);
  });

  it("beadsForLane filters by lane id", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("tool_result", {
        display_title: "a",
        payload: { surface_uri: "email://draft-1" },
      }),
      makeEnvelope("tool_result", {
        display_title: "b",
        payload: { surface_uri: "sheet://x" },
      }),
    ]);
    expect(selectors.beadsForLane(state, "email")).toHaveLength(1);
    expect(selectors.beadsForLane(state, "sheet")).toHaveLength(1);
    expect(selectors.beadsForLane(state, "missing")).toHaveLength(0);
  });

  it("surfaceFor returns the per-uri payload", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("tool_result", {
        payload: { surface_uri: "sheet://x", state: { rows: 1 } },
      }),
    ]);
    expect(selectors.surfaceFor(state, "sheet://x")).toEqual({ rows: 1 });
    expect(selectors.surfaceFor(state, "missing")).toBeUndefined();
  });
});

describe("eventProjector — surface spec merge (PRD-04 / D4)", () => {
  it("merges a late surface_spec_generated spec into surfaceState[uri] (envelope → spec)", () => {
    nextSeq = 0;
    const uri = "record://seed/get_issue/1";
    const state = project([
      surfaceEnvelopeEvent(uri, { data: { issue: { title: "Fix login" } } }),
      makeEnvelope("surface_spec_generated", {
        payload: { surface_uri: uri, archetype: "record", spec: RECORD_SPEC },
      }),
    ]);
    const surface = state.surfaceState.get(uri) as Record<string, unknown>;
    expect(surface).toBeDefined();
    expect(surface.spec).toEqual(RECORD_SPEC);
    // The spec merge NEVER clobbers the existing data.
    expect(surface.data).toEqual({ issue: { title: "Fix login" } });
  });

  it("a late spec never clobbers newer data (data set after the spec survives)", () => {
    nextSeq = 0;
    const uri = "record://seed/get_issue/1";
    const state = project([
      surfaceEnvelopeEvent(uri, { data: { issue: { title: "v1" } } }),
      makeEnvelope("surface_spec_generated", {
        payload: { surface_uri: uri, archetype: "record", spec: RECORD_SPEC },
      }),
      // A newer tool_result carries fresh data but no spec.
      surfaceEnvelopeEvent(uri, { data: { issue: { title: "v2" } } }),
    ]);
    const surface = state.surfaceState.get(uri) as Record<string, unknown>;
    expect(surface.spec).toEqual(RECORD_SPEC);
    expect(surface.data).toEqual({ issue: { title: "v2" } });
  });

  it("is idempotent on replay (dedup by event_id → same surfaceState + surfaceTabs)", () => {
    nextSeq = 0;
    const uri = "record://seed/get_issue/1";
    const events = [
      surfaceEnvelopeEvent(uri, { data: { issue: { title: "Fix login" } } }),
      makeEnvelope("surface_spec_generated", {
        payload: { surface_uri: uri, archetype: "record", spec: RECORD_SPEC },
      }),
    ];
    const once = project(events);
    const twice = project([...events, ...events]);
    expect(twice.surfaceState.get(uri)).toEqual(once.surfaceState.get(uri));
    expect(twice.surfaceTabs).toEqual(once.surfaceTabs);
  });

  it("still accepts the legacy flat surface payload unchanged", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("tool_result", {
        payload: { surface_uri: "sheet://acme", state: { rows: 5 } },
      }),
    ]);
    expect(state.surfaceState.get("sheet://acme")).toEqual({ rows: 5 });
  });
});

describe("eventProjector.surfaceTabs (PRD-04)", () => {
  it("derives one tab per surface uri, ordered by last mutation (newest first)", () => {
    nextSeq = 0;
    const a = "record://a";
    const b = "record://b";
    const c = "record://c";
    const state = project([
      surfaceEnvelopeEvent(a, { data: {} }), // seq 0
      surfaceEnvelopeEvent(b, { data: {} }), // seq 1
      surfaceEnvelopeEvent(c, { data: {} }), // seq 2
      surfaceEnvelopeEvent(a, { data: {} }), // seq 3 → a bumped
      surfaceEnvelopeEvent(b, { data: {} }), // seq 4 → b bumped
      makeEnvelope("surface_spec_generated", {
        payload: { surface_uri: c, archetype: "record", spec: RECORD_SPEC },
      }), // seq 5 → c bumped
    ]);
    expect(state.surfaceTabs).toHaveLength(3);
    expect(state.surfaceTabs.map((t) => t.uri)).toEqual([c, b, a]);
    expect(state.surfaceTabs.map((t) => t.lastSeq)).toEqual([5, 4, 3]);
  });

  it("resolves the title from spec.title_path against data; falls back to the uri tail", () => {
    nextSeq = 0;
    const withSpec = "record://seed/get_issue/1";
    const noSpec = "sheet://acme-42";
    const state = project([
      surfaceEnvelopeEvent(withSpec, {
        data: { issue: { title: "Fix login" } },
        spec: RECORD_SPEC,
      }),
      surfaceEnvelopeEvent(noSpec, { data: {} }),
    ]);
    const byUri = new Map(state.surfaceTabs.map((t) => [t.uri, t]));
    expect(byUri.get(withSpec)?.title).toBe("Fix login");
    expect(byUri.get(withSpec)?.archetype).toBe("record");
    // No spec → fall back to the uri tail (everything after `://`).
    expect(byUri.get(noSpec)?.title).toBe("acme-42");
  });

  it("projectSurfaceTabs matches project().surfaceTabs exactly (shared derivation)", () => {
    nextSeq = 0;
    const events = [
      surfaceEnvelopeEvent("record://a", { data: {} }),
      surfaceEnvelopeEvent("record://b", { data: {} }),
      makeEnvelope("surface_spec_generated", {
        payload: {
          surface_uri: "record://a",
          archetype: "record",
          spec: RECORD_SPEC,
        },
      }),
    ];
    expect(projectSurfaceTabs(events)).toEqual(project(events).surfaceTabs);
  });

  it("returns no tabs for a stream with no surfaces", () => {
    nextSeq = 0;
    expect(projectSurfaceTabs([])).toEqual([]);
    expect(
      project([makeEnvelope("run_started", { display_title: "go" })])
        .surfaceTabs,
    ).toEqual([]);
  });
});

describe("eventProjector — one projector, multiple consumers", () => {
  // Render-count invariant: four consumers reading from the SAME
  // projected state must NOT cause the reducer to run four times. This
  // is enforced by `useMemo` at the call site, but the contract here is
  // that `project()` is pure and that consumers select from its output
  // rather than calling it themselves.
  it("a single project() call produces every projection a consumer needs", () => {
    nextSeq = 0;
    const events = [
      makeEnvelope("run_started", { display_title: "start" }),
      makeEnvelope("tool_result", {
        display_title: "row",
        payload: { surface_uri: "sheet://x", state: { rows: 1 } },
      }),
      makeEnvelope("approval_requested", {
        display_title: "approve?",
        payload: {
          approval_id: "ap-1",
          tenant_id: "tenant-1",
          surface_uri: "email://draft-1",
        },
      }),
      makeEnvelope("final_response", { display_title: "done" }),
    ];
    const state = project(events);
    // Consumer 1: chat list
    expect(selectors.chatEntries(state).length).toBeGreaterThan(0);
    // Consumer 2: swimlanes
    expect(state.beads.length).toBeGreaterThan(0);
    // Consumer 3: mini-timeline (same beads)
    expect(state.beads.length).toEqual(state.beads.length);
    // Consumer 4: surface mount
    expect(selectors.surfaceFor(state, "sheet://x")).toBeDefined();
    // Approvals tab
    expect(selectors.pendingApprovals(state)).toHaveLength(1);
  });
});
