import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type {
  RuntimeApiEventType,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { TcSurfaceFrame } from "./TcSurfaceFrame";
import { project } from "./eventProjector";
import type { SurfaceProvenance } from "./provenance";

const prov = (over: Partial<SurfaceProvenance> = {}): SurfaceProvenance => ({
  surfaceId: "s1",
  ledgerId: "r7f3·042",
  connector: "linear",
  op: "get_issue",
  kind: "record",
  latencyMs: 120,
  accessClass: "read",
  tier: "shaped",
  openIn: null,
  ...over,
});

const child = <div data-testid="b1-pane">surface content</div>;

describe("TcSurfaceFrame", () => {
  it("renders children bare (no frame chrome) when provenance is null", () => {
    render(<TcSurfaceFrame provenance={null}>{child}</TcSurfaceFrame>);
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-surface-frame")).toBeNull();
    expect(screen.queryByTestId("tc-provenance-footer")).toBeNull();
  });

  it("shows the skeleton (not children) while pending, with the footer pinned", () => {
    render(
      <TcSurfaceFrame provenance={prov({ tier: "pending" })}>
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-surface-skeleton")).toHaveTextContent(
      "Linear · assembling record view…",
    );
    expect(screen.queryByTestId("b1-pane")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("renders children for generic/shaped with the footer", () => {
    render(
      <TcSurfaceFrame provenance={prov({ tier: "generic" })}>
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("renders the raw fallback (not children) for the raw tier", () => {
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "raw" })}
        rawPayload={{ data: { id: 1 } }}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-raw-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("b1-pane")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
  });

  it("resolves a deep link from the hydrated payload for the footer", () => {
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "generic" })}
        rawPayload={{
          spec: { link: { label: "Open", url_path: "data.url" } },
          data: { url: "https://linear.app/x" },
        }}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-provenance-open-in")).toHaveAttribute(
      "href",
      "https://linear.app/x",
    );
  });
});

// ---------------------------------------------------------------------------
// The "generating…" signal, driven end-to-end through the real projector.
//
// These build a `RuntimeEventEnvelope[]`, run the ONE projection, and hand the
// frame the slice a host would hand it. Constructing the `SurfaceSpecGeneration`
// by hand would test the frame against a shape the projector might not produce;
// going through `project()` is what proves the two halves fit.
// ---------------------------------------------------------------------------

const SURFACE_URI = "record://linear/get_issue/42";

let nextSeq = 0;

function makeEnvelope(
  type: RuntimeApiEventType,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  const seq = nextSeq;
  nextSeq += 1;
  return {
    event_id: `evt-${seq}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no: seq,
    event_type: type as RuntimeApiEventType,
    activity_kind: "event",
    payload,
    created_at: new Date(1700000000000 + seq * 1000).toISOString(),
  };
}

/** The slice a host binder reads for the active surface. */
function generationFor(events: readonly RuntimeEventEnvelope[]) {
  return project(events).surfaceSpecGeneration.get(SURFACE_URI) ?? null;
}

describe("TcSurfaceFrame — spec-generation signal", () => {
  it("shows the skeleton and NAMES the model once generation is requested", () => {
    nextSeq = 0;
    const generation = generationFor([
      makeEnvelope("surface_spec_requested", {
        surface_id: SURFACE_URI,
        model_id: "claude-haiku-4-5",
      }),
    ]);
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "pending" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-surface-skeleton")).toBeInTheDocument();
    // The point of the change: words, not just shimmer.
    expect(screen.getByTestId("tc-surface-skeleton-detail")).toHaveTextContent(
      "Asking claude-haiku-4-5 to lay out this record.",
    );
  });

  it("still says what is happening when the runtime named no model", () => {
    nextSeq = 0;
    const generation = generationFor([
      makeEnvelope("surface_spec_requested", {
        surface_id: SURFACE_URI,
        model_id: null,
      }),
    ]);
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "pending" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("tc-surface-skeleton-detail")).toHaveTextContent(
      "Choosing a layout for this record.",
    );
  });

  it("drops the skeleton when surface_spec_generated lands", () => {
    nextSeq = 0;
    const generation = generationFor([
      makeEnvelope("surface_spec_requested", {
        surface_id: SURFACE_URI,
        model_id: "claude-haiku-4-5",
      }),
      makeEnvelope("surface_spec_generated", {
        surface_uri: SURFACE_URI,
        archetype: "record",
        spec: { spec_version: 1, archetype: "record" },
      }),
    ]);
    expect(generation).toBeNull();
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "shaped" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
  });

  it("drops the skeleton when the run ends without a generated event", () => {
    // A generation that dies with its run emits no terminal of its own; the
    // skeleton must not outlive the run that could have finished it.
    nextSeq = 0;
    const generation = generationFor([
      makeEnvelope("surface_spec_requested", {
        surface_id: SURFACE_URI,
        model_id: "claude-haiku-4-5",
      }),
      makeEnvelope("run_failed", {}),
    ]);
    expect(generation).toBeNull();
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "raw" })}
        specGeneration={generation}
        rawPayload={{ data: { id: 1 } }}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
    expect(screen.getByTestId("tc-raw-fallback")).toBeInTheDocument();
  });

  it("never masks an already-rendered surface with a stale signal", () => {
    // THE SIGNAL IS NOT A MATCHED PAIR. `_emit_requested` fires unconditionally
    // at the top of `_generate`, but only the SUCCESS exit emits
    // `surface_spec_generated` — a raise and a `GenFailure` (a normal outcome)
    // both return without a terminal, and generation is fire-and-forget, so the
    // request can even land after the run ends.
    //
    // So an entry CAN legitimately still be set over a surface that is already
    // drawn. When it is, the frame must keep showing the surface: a progress
    // hint that replaces rendered content with a shimmer for the rest of the
    // run is worse than no hint at all.
    nextSeq = 0;
    const generation = generationFor([
      makeEnvelope("surface_spec_requested", {
        surface_id: SURFACE_URI,
        model_id: "claude-haiku-4-5",
      }),
      // No terminal — the generator failed, or has not come back.
    ]);
    expect(generation).not.toBeNull();
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "shaped" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
  });
});

describe("TcSurfaceFrame — a runtime that never emits the signal", () => {
  // Every session already on disk was written before `surface_spec_requested`
  // existed, so this is not an edge case: it is most of the corpus.
  const legacyStream = () => {
    nextSeq = 0;
    return [
      makeEnvelope("run_started", {}),
      makeEnvelope("tool_result", {
        surface: {
          surface_uri: SURFACE_URI,
          archetype: "record",
          state: { data: { id: 1 } },
        },
      }),
    ];
  };

  it("projects no generation, so the frame renders exactly today's skeleton", () => {
    const generation = generationFor(legacyStream());
    expect(generation).toBeNull();
    const { container } = render(
      <TcSurfaceFrame
        provenance={prov({ tier: "pending" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    const skeleton = screen.getByTestId("tc-surface-skeleton");
    // The pre-change contract, asserted node by node: the one caption with the
    // original copy, then the three shimmer bars, and NOTHING else. A stray
    // fourth child is how a "harmless" addition would slip into every replayed
    // session.
    expect(skeleton.textContent).toBe("Linear · assembling record view…");
    expect(skeleton.children).toHaveLength(4);
    expect(screen.queryByTestId("tc-surface-skeleton-detail")).toBeNull();
    expect(screen.queryByTestId("b1-pane")).toBeNull();
    expect(screen.getByTestId("tc-provenance-footer")).toBeInTheDocument();
    expect(container.innerHTML).toContain("assembling record view");
  });

  it("renders byte-identically whether the prop is omitted or projected-empty", () => {
    const omitted = render(
      <TcSurfaceFrame provenance={prov({ tier: "pending" })}>
        {child}
      </TcSurfaceFrame>,
    );
    const empty = render(
      <TcSurfaceFrame
        provenance={prov({ tier: "pending" })}
        specGeneration={generationFor(legacyStream())}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(empty.container.innerHTML).toBe(omitted.container.innerHTML);
  });

  it("leaves the settled tiers untouched with no signal", () => {
    const generation = generationFor(legacyStream());
    render(
      <TcSurfaceFrame
        provenance={prov({ tier: "generic" })}
        specGeneration={generation}
      >
        {child}
      </TcSurfaceFrame>,
    );
    expect(screen.getByTestId("b1-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("tc-surface-skeleton")).toBeNull();
  });
});
