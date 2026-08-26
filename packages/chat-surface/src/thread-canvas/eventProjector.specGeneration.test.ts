// `surface_spec_requested` → `ProjectedState.surfaceSpecGeneration`.
//
// The event is a plain progress signal: `surface_spec_generated` is still what
// ENDS the operation, and nothing may depend on the requested event arriving.
// That last property is what most of this file tests — every session already on
// disk was written before the event existed, so "no such event" is not an edge
// case, it is the majority of the corpus.

import { describe, expect, it } from "vitest";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { project, projectAt, projectSurfaceTabs } from "./eventProjector";

const URI = "record://linear/get_issue/42";
const OTHER_URI = "table://linear/list_issues/7";
const SPEC = {
  spec_version: 1,
  archetype: "record",
  source: { server: "linear", tool: "get_issue" },
  title_path: "issue.title",
};

let nextSeq = 0;

/**
 * `type` is a bare string, not `RuntimeApiEventType`: `surface_spec_requested`
 * is not in that closed union yet (the wire contract landed ahead of the
 * api-types mirror), so the cast happens once, here, rather than at every call.
 */
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
    event_type: type as RuntimeApiEventType,
    activity_kind: "event",
    payload: {},
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
    ...overrides,
  };
}

function requested(
  surfaceId: string | null,
  modelId: string | null = "claude-haiku-4-5",
  seq?: number,
): RuntimeEventEnvelope {
  return makeEnvelope("surface_spec_requested", {
    payload: { surface_id: surfaceId, model_id: modelId },
    ...(seq === undefined ? {} : { sequence_no: seq }),
  });
}

function generated(
  uri: string,
  spec: unknown = SPEC,
  seq?: number,
): RuntimeEventEnvelope {
  return makeEnvelope("surface_spec_generated", {
    payload: { surface_uri: uri, archetype: "record", spec },
    ...(seq === undefined ? {} : { sequence_no: seq }),
  });
}

/** A `tool_result` carrying the PRD-01 `payload.surface` envelope. */
function surfaceEvent(
  uri: string,
  data: unknown = {},
  seq?: number,
): RuntimeEventEnvelope {
  return makeEnvelope("tool_result", {
    payload: {
      surface: { surface_uri: uri, archetype: "record", state: { data } },
    },
    ...(seq === undefined ? {} : { sequence_no: seq }),
  });
}

describe("eventProjector — surface spec generation (surface_spec_requested)", () => {
  it("opens a generation for the requested surface, carrying the model id", () => {
    nextSeq = 0;
    const state = project([surfaceEvent(URI), requested(URI)]);
    expect(state.surfaceSpecGeneration.get(URI)).toEqual({
      modelId: "claude-haiku-4-5",
    });
  });

  it("opens a generation for a surface with no payload yet (the blank window)", () => {
    // The whole point of the signal is the gap BEFORE any content lands, so a
    // surface the projector has never seen state for must still be able to
    // report that its view is being generated.
    nextSeq = 0;
    const state = project([requested(URI)]);
    expect(state.surfaceSpecGeneration.get(URI)).toEqual({
      modelId: "claude-haiku-4-5",
    });
    expect(state.surfaceState.size).toBe(0);
  });

  it("keeps a null model_id rather than inventing one", () => {
    nextSeq = 0;
    const state = project([requested(URI, null)]);
    expect(state.surfaceSpecGeneration.get(URI)).toEqual({ modelId: null });
  });

  it("records nothing for an unattributed (null surface_id) request", () => {
    // No surface can honestly claim a generation the runtime declined to name.
    nextSeq = 0;
    const state = project([surfaceEvent(URI), requested(null)]);
    expect(state.surfaceSpecGeneration.size).toBe(0);
  });

  it("closes the generation on its terminal surface_spec_generated", () => {
    nextSeq = 0;
    const state = project([surfaceEvent(URI), requested(URI), generated(URI)]);
    expect(state.surfaceSpecGeneration.size).toBe(0);
    // …and the spec merge it always did is untouched.
    const surface = state.surfaceState.get(URI) as Record<string, unknown>;
    expect(surface.spec).toEqual(SPEC);
  });

  it("closes the generation even when the generated spec is malformed", () => {
    // The model call is over either way; only the UPGRADE is in doubt. Closing
    // after the spec guard would shimmer for the rest of the run over a surface
    // nothing further is happening to.
    nextSeq = 0;
    const state = project([
      surfaceEvent(URI),
      requested(URI),
      generated(URI, "not-an-object"),
    ]);
    expect(state.surfaceSpecGeneration.size).toBe(0);
    const surface = state.surfaceState.get(URI) as Record<string, unknown>;
    expect(surface.spec).toBeUndefined();
  });

  it("closes only the surface that finished", () => {
    nextSeq = 0;
    const state = project([
      requested(URI),
      requested(OTHER_URI, "gpt-5-mini"),
      generated(URI),
    ]);
    expect(state.surfaceSpecGeneration.has(URI)).toBe(false);
    expect(state.surfaceSpecGeneration.get(OTHER_URI)).toEqual({
      modelId: "gpt-5-mini",
    });
  });

  it.each(["run_completed", "run_failed", "run_cancelled"])(
    "closes every open generation when the run ends with %s",
    (terminal) => {
      // A generation that dies with its run never emits its own terminal event,
      // and a "generating…" state that outlives the run cannot be dismissed.
      nextSeq = 0;
      const state = project([
        requested(URI),
        requested(OTHER_URI),
        makeEnvelope(terminal),
      ]);
      expect(state.surfaceSpecGeneration.size).toBe(0);
    },
  );

  it("is idempotent on replay (dedup by event_id)", () => {
    nextSeq = 0;
    const events = [surfaceEvent(URI), requested(URI)];
    const once = project(events);
    const twice = project([...events, ...events]);
    expect(twice.surfaceSpecGeneration).toEqual(once.surfaceSpecGeneration);
    expect(twice.surfaceState).toEqual(once.surfaceState);
  });

  it("time-travels: underway at the request, closed at the terminal", () => {
    nextSeq = 0;
    const events = [surfaceEvent(URI), requested(URI), generated(URI)];
    const atRequest = projectAt(events, 1);
    const atGenerated = projectAt(events, 2);
    expect(atRequest.surfaceSpecGeneration.get(URI)).toEqual({
      modelId: "claude-haiku-4-5",
    });
    expect(atGenerated.surfaceSpecGeneration.size).toBe(0);
  });
});

describe("eventProjector — spec generation degrades to today when absent", () => {
  it("leaves the map empty for a stream that never emits the event", () => {
    // Every session written before the event existed replays through this path.
    nextSeq = 0;
    const state = project([
      makeEnvelope("run_started"),
      surfaceEvent(URI, { issue: { title: "Fix login" } }),
      generated(URI),
      makeEnvelope("run_completed"),
    ]);
    expect(state.surfaceSpecGeneration.size).toBe(0);
  });

  it("leaves every other projected slice byte-identical to the same stream", () => {
    // The strongest form of "nothing may depend on it": add the event to a
    // stream and everything the cockpit already read must not move. Only
    // `activity` is exempt — the projector shows every non-internal event the
    // backend sends, so a NEW event legitimately adds a row there.
    //
    // Sequence numbers are PINNED rather than auto-assigned. Letting the fixture
    // renumber makes the inserted event push `surface_spec_generated` one slot
    // later, `surfaceTabs.lastSeq` follows it, and the diff that surfaces is the
    // fixture's, not the projector's — a false red that reads exactly like a real
    // regression.
    nextSeq = 0;
    const baseline = project([
      surfaceEvent(URI, {}, 0),
      generated(URI, SPEC, 10),
    ]);

    nextSeq = 0;
    const enriched = project([
      surfaceEvent(URI, {}, 0),
      requested(URI, "claude-haiku-4-5", 5),
      generated(URI, SPEC, 10),
    ]);

    expect(enriched.surfaceState).toEqual(baseline.surfaceState);
    expect(enriched.surfaceTabs).toEqual(baseline.surfaceTabs);
    expect(enriched.chat).toEqual(baseline.chat);
    expect(enriched.beads).toEqual(baseline.beads);
    expect(enriched.approvals).toEqual(baseline.approvals);
    expect(enriched.surfaceSpecGeneration.size).toBe(0);
  });

  it("empty projection exposes the slice, so consumers need no undefined check", () => {
    expect(project([]).surfaceSpecGeneration.size).toBe(0);
    expect(projectAt([], 0).surfaceSpecGeneration.size).toBe(0);
  });

  it("projectSurfaceTabs still matches project().surfaceTabs with the event present", () => {
    // The tab-strip selector shares `applySurfaceEvent`; the new branch must not
    // make the two folds disagree.
    nextSeq = 0;
    const events = [surfaceEvent(URI), requested(URI), surfaceEvent(OTHER_URI)];
    expect(projectSurfaceTabs(events)).toEqual(project(events).surfaceTabs);
    // A request alone mints no tab — there is no surface content to show yet.
    expect(projectSurfaceTabs(events).map((t) => t.uri)).toEqual([
      OTHER_URI,
      URI,
    ]);
  });
});
