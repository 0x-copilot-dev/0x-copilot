import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import {
  project,
  projectAt,
  projectSurfaceTabs,
  projectToolCalls,
  selectors,
  TOOL_OUTPUT_PREVIEW_CAP,
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

  it("hides receipt.emitted from activity feed", () => {
    nextSeq = 0;
    const state = project([
      makeEnvelope("run_started", { display_title: "Visible" }),
      makeEnvelope("receipt.emitted", {
        display_title: "Run Receipt",
        payload: { receipt: { status: "completed" } },
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

describe("eventProjector.projectToolCalls", () => {
  it("returns an empty array for zero events", () => {
    nextSeq = 0;
    expect(projectToolCalls([])).toEqual([]);
  });

  // A live desktop journey caught this: the backend had stopped calling a
  // declined capability a failure, but the client collapsed every non-success
  // status into `error`, so `ls` with no folder shared still rendered "Failed".
  it("keeps a declined capability out of the error status", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: { call_id: "call-1", tool_name: "ls" },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-1",
          tool_name: "ls",
          status: "unavailable",
          error_code: "workspace_no_grants",
          safe_message:
            "No host folders have been shared with this workspace yet.",
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("unavailable");
    // The explanation survives — it is the whole value of the card.
    expect(entries[0].errorMessage).toBe(
      "No host folders have been shared with this workspace yet.",
    );
  });

  // The second producer of `unavailable`, found the same way — journey phase
  // CB-7 watched a per-run tool cap refuse two `web_search` calls exactly as
  // configured, and the cards said "Failed". A refusal is decided BEFORE the
  // tool runs: nothing was attempted, so nothing broke.
  it("keeps a tool call refused by the budget out of the error status", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: { call_id: "toolu_01W7", tool_name: "web_search" },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "toolu_01W7",
          tool_name: "web_search",
          status: "unavailable",
          error_code: "tool_budget_exceeded",
          retryable: false,
          safe_message:
            "The tool call budget for 'web_search' is exhausted (4 of 4 " +
            "calls used). Do not call this tool again; finalize now with " +
            "what you have.",
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("unavailable");
    // Without this the card renders blank, which is what the live run showed.
    expect(entries[0].errorMessage).toContain("budget");
  });

  it("still reports a genuine tool failure as an error", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_result", {
        payload: { call_id: "call-2", tool_name: "ls", status: "failed" },
      }),
    ]);

    expect(entries[0].status).toBe("error");
  });

  it("collapses a started→result pair into one complete card with the right fields", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        display_title: "Search the web",
        payload: {
          call_id: "call-1",
          tool_name: "web_search",
          args: { query: "0xcopilot launch" },
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-1",
          tool_name: "web_search",
          status: "completed",
          output: { hits: 3 },
          summary: "3 results",
        },
      }),
    ]);
    expect(entries).toHaveLength(1);
    const entry = entries[0];
    expect(entry.id).toBe("call-1");
    expect(entry.toolName).toBe("web_search");
    expect(entry.title).toBe("Search the web");
    expect(entry.status).toBe("complete");
    expect(entry.args).toEqual({ query: "0xcopilot launch" });
    expect(entry.result).toEqual({ hits: 3 });
    expect(entry.summary).toBe("3 results");
    // Anchor is the started frame's sequence + timestamp, not the result's.
    expect(entry.sequenceNo).toBe(0);
    expect(entry.createdAtMs).toBe(1700000000000);
  });

  it("keeps the agent-authored presentation title through terminal lifecycle labels", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        display_title: "web_search started",
        presentation: {
          title: "Web search running",
          summary: "Running a web search",
          status_label: "Running",
          kind: "progress",
        },
        payload: {
          call_id: "call-human-title",
          tool_name: "web_search",
          args: {
            display_title: "PEP 8 documentation",
            display_summary: "Find the official Python style guide",
            query: "official PEP 8 documentation",
          },
        },
      }),
      makeEnvelope("tool_result", {
        display_title: "web_search completed",
        presentation: {
          title: "Web search completed",
          status_label: "Done",
          kind: "result",
        },
        payload: {
          call_id: "call-human-title",
          tool_name: "web_search",
          status: "completed",
          output: { hits: 1 },
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      title: "PEP 8 documentation",
      status: "complete",
    });
  });

  it("keeps a started-only call in the running state", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: { call_id: "call-x", tool_name: "get_issue" },
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("running");
    expect(entries[0].result).toBeUndefined();
  });

  it("updates a started card with the latest streamed tool-call args", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: {
          call_id: "call-streamed-args",
          tool_name: "web_search",
          // This is emitted before the streaming JSON can be parsed.
          args: {},
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-streamed-args",
          tool_name: "web_search",
          // Runtime deltas carry the current accumulated args snapshot.
          args: { query: "0xcopilot launch", page: 2 },
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-streamed-args",
          tool_name: "web_search",
          status: "completed",
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      status: "complete",
      args: { query: "0xcopilot launch", page: 2 },
    });
  });

  it("merges a partial args_delta without losing prior streamed args", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: {
          call_id: "call-partial-args",
          tool_name: "web_search",
          args: { query: "0xcopilot launch" },
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-partial-args",
          args_delta: { page: 2 },
        },
      }),
    ]);

    expect(entries[0]).toMatchObject({
      status: "running",
      args: { query: "0xcopilot launch", page: 2 },
    });
  });

  // ── live command output (PRD-shell-execution §14.2) ──────────────────────
  //
  // Ships dark: no tool emits `output_preview` today. These pin the projector
  // half of the seam so the field cannot arrive later and land in `result`.

  it("collects a streamed output preview WITHOUT settling the card", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: {
          call_id: "call-cmd",
          tool_name: "run_command",
          args: { command: "pytest -q" },
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd",
          tool_name: "run_command",
          output_preview: "collecting ...\n",
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd",
          tool_name: "run_command",
          // The producer sends a rolling TAIL in full, so the newest frame
          // supersedes the previous one rather than being appended to it.
          output_preview: "collecting ...\n12 passed\n",
        },
      }),
    ]);

    expect(entries[0].outputPreview).toBe("collecting ...\n12 passed\n");
    // The whole reason the field is separate: a card with a `result` reads as
    // finished, and this one is not.
    expect(entries[0].result).toBeUndefined();
    expect(entries[0].status).toBe("running");
    // Output is not arguments. The command survives untouched.
    expect(entries[0].args).toEqual({ command: "pytest -q" });
  });

  it("keeps the last tail when a delta carries an empty preview", () => {
    // The same trap `{delta: ""}` set for streamed ARGUMENTS: a producer that
    // opens the stream with an empty string means "nothing yet", and storing
    // that as content blanks a card that had output.
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd-empty",
          tool_name: "run_command",
          output_preview: "building...\n",
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd-empty",
          tool_name: "run_command",
          output_preview: "",
        },
      }),
      makeEnvelope("tool_call_delta", {
        payload: { call_id: "call-cmd-empty", tool_name: "run_command" },
      }),
    ]);

    expect(entries[0].outputPreview).toBe("building...\n");
  });

  it("clips an oversized preview to the cap, keeping the TAIL", () => {
    nextSeq = 0;
    const head = "H".repeat(2000);
    const tail = "T".repeat(TOOL_OUTPUT_PREVIEW_CAP);
    const entries = projectToolCalls([
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd-huge",
          tool_name: "run_command",
          output_preview: `${head}${tail}`,
        },
      }),
    ]);

    const preview = entries[0].outputPreview ?? "";
    expect(preview.length).toBe(TOOL_OUTPUT_PREVIEW_CAP);
    expect(preview).toBe(tail);
    expect(preview.startsWith("H")).toBe(false);
  });

  it("never splits an astral codepoint at the clip boundary", () => {
    // A fixed-offset slice can land between the halves of a surrogate pair,
    // and a lone low surrogate paints as U+FFFD. The UTF-16 form of §13's
    // continuation-byte walk.
    nextSeq = 0;
    const pairs = "🙂".repeat(TOOL_OUTPUT_PREVIEW_CAP);
    const entries = projectToolCalls([
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd-astral",
          tool_name: "run_command",
          output_preview: pairs,
        },
      }),
    ]);

    const preview = entries[0].outputPreview ?? "";
    expect(preview.length).toBeLessThanOrEqual(TOOL_OUTPUT_PREVIEW_CAP);
    // No replacement character means no half a codepoint survived the clip.
    expect(preview.includes("�")).toBe(false);
    expect([...preview].every((ch) => ch === "🙂")).toBe(true);
  });

  it("keeps the live tail when the terminal frame carries no output", () => {
    // A run cancelled mid-command: the tail is the only record of what ran.
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_delta", {
        payload: {
          call_id: "call-cmd-cancelled",
          tool_name: "run_command",
          output_preview: "step 1 done\n",
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-cmd-cancelled",
          tool_name: "run_command",
          status: "cancelled",
        },
      }),
    ]);

    expect(entries[0].status).toBe("error");
    expect(entries[0].outputPreview).toBe("step 1 done\n");
  });

  it("carries only safe supplied provenance, authority, duration, and delegated task ids", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: {
          call_id: "call-safe-metadata",
          tool_name: "web_search",
          provenance: { source: "mcp", server_name: "Brave Search" },
          access_mode: "read_act",
          subagent_task_ids: ["task-research", "task-research", 7, ""],
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-safe-metadata",
          tool_name: "web_search",
          status: "completed",
          duration_ms: 1200,
          // Unknown presentation data must not overwrite or fabricate the
          // facts accepted from the started frame.
          provenance: { source: "unknown", server_name: "guess" },
          access_mode: "write",
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      provenance: { source: "mcp", serverName: "Brave Search" },
      accessMode: "read_act",
      durationMs: 1200,
      subagentTaskIds: ["task-research"],
    });
  });

  it("marks a failed result as error and carries the safe message", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: { call_id: "c9", tool_name: "send_email" },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "c9",
          tool_name: "send_email",
          status: "failed",
          error_message: "connector timed out",
        },
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0].status).toBe("error");
    expect(entries[0].errorMessage).toBe("connector timed out");
  });

  it("repairs a legacy JSON-encoded typed error mislabeled as completed", () => {
    nextSeq = 0;
    const embedded = {
      error: {
        code: "connection_failed",
        safe_message: "The MCP server could not be reached.",
        retryable: true,
      },
    };
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        presentation: {
          title: "Load Linear tools",
          summary: "Connecting to Linear",
          status_label: "Running",
          kind: "progress",
        },
        payload: {
          call_id: "call-legacy-error",
          tool_name: "load_mcp_server",
        },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "call-legacy-error",
          tool_name: "load_mcp_server",
          // This is the old worker bug: LangChain called a returned error data
          // object successful and the worker trusted that lifecycle status.
          status: "completed",
          output: { content: JSON.stringify(embedded) },
        },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      title: "Load Linear tools",
      summary: "Connecting to Linear",
      status: "error",
      errorMessage: "The MCP server could not be reached.",
      result: embedded,
    });
  });

  it("keeps an earlier terminal failure when its completion receipt has no status", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        payload: { call_id: "c10", tool_name: "send_email" },
      }),
      makeEnvelope("tool_result", {
        payload: {
          call_id: "c10",
          tool_name: "send_email",
          status: "timed_out",
          safe_message: "The email connector timed out.",
        },
      }),
      // Older/replayed completion receipts may not repeat the result status.
      makeEnvelope("tool_call_completed", {
        payload: { call_id: "c10", tool_name: "send_email" },
      }),
    ]);

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      status: "error",
      errorMessage: "The email connector timed out.",
    });
  });

  it("excludes subagent tool calls (they belong to the subagent views)", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        subagent_id: "sub-7",
        payload: { call_id: "sc-1", tool_name: "read_doc" },
      }),
      makeEnvelope("tool_call_started", {
        payload: { call_id: "mc-1", tool_name: "web_search" },
      }),
    ]);
    expect(entries.map((e) => e.id)).toEqual(["mc-1"]);
  });

  it("excludes tools the server marked internal", () => {
    // THE regression behind the raw "Calling write_todos" tile: the backend
    // already stamps `visibility: "internal"` on both frames of every tool in
    // its internal set, and `project()` honoured it — this pass did not, so the
    // card rendered anyway, beside the surface built to replace it.
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        visibility: "internal",
        payload: { call_id: "todo-1", tool_name: "write_todos" },
      }),
      makeEnvelope("tool_result", {
        visibility: "internal",
        payload: {
          call_id: "todo-1",
          tool_name: "write_todos",
          status: "completed",
        },
      }),
      makeEnvelope("tool_call_started", {
        payload: { call_id: "mc-1", tool_name: "web_search" },
      }),
    ]);
    expect(entries.map((e) => e.id)).toEqual(["mc-1"]);
  });

  it("excludes audit-visibility tools alongside internal ones", () => {
    nextSeq = 0;
    const entries = projectToolCalls([
      makeEnvelope("tool_call_started", {
        visibility: "audit",
        payload: { call_id: "aud-1", tool_name: "record_something" },
      }),
    ]);
    expect(entries).toEqual([]);
  });

  it("is idempotent on replay (deduplicates by event_id)", () => {
    nextSeq = 0;
    const started = makeEnvelope("tool_call_started", {
      event_id: "evt-started",
      payload: { call_id: "call-1", tool_name: "web_search" },
    });
    const result = makeEnvelope("tool_result", {
      event_id: "evt-result",
      payload: {
        call_id: "call-1",
        tool_name: "web_search",
        status: "completed",
      },
    });
    const once = projectToolCalls([started, result]);
    const twice = projectToolCalls([started, result, started, result]);
    expect(twice).toEqual(once);
    expect(twice).toHaveLength(1);
  });
});
