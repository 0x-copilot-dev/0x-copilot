// PRD-B4 — the `shape.requested` / `shape.resolved` fold (`shapeRequest` state).
// Pins: idle by default; requested on `shape.requested`; `shaped` returns to idle
// (the paired view.derived already flipped the tier); `no_fit` surfaces the honest
// state; the fold NEVER advances `lastSeq` (parity with the Python SurfaceStore
// fold, which skips these) and tolerates a shape event for an unseen surface.

import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectLedger } from "./ledgerProjection";

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
    activity_kind: "event" as RuntimeEventEnvelope["activity_kind"],
    payload,
    created_at: "2026-07-23T10:00:00Z",
  };
}

function created(surface_id: string): RuntimeEventEnvelope {
  return ev("surface.created", {
    v: 1,
    surface_id,
    kind: "record",
    source: { connector: "customsrv", op: "custom_tool" },
    title: `Title ${surface_id}`,
    payload_ref: `payload/${surface_id}`,
  });
}

function generic(surface_id: string): RuntimeEventEnvelope {
  return ev("view.derived", {
    v: 1,
    surface_id,
    tier: "generic",
    basis: "schema",
  });
}

describe("shapeRequest fold (PRD-B4)", () => {
  it("defaults to idle when no shape event landed", () => {
    const p = projectLedger([created("s1"), generic("s1")]);
    expect(p.surfaces.get("s1")?.shapeRequest).toBe("idle");
  });

  it("shape.requested ⇒ requested", () => {
    const p = projectLedger([
      created("s1"),
      generic("s1"),
      ev("shape.requested", { v: 1, surface_id: "s1", actor: "user" }),
    ]);
    expect(p.surfaces.get("s1")?.shapeRequest).toBe("requested");
  });

  it("shape.resolved {shaped} returns to idle (the tier flip hides the button)", () => {
    const p = projectLedger([
      created("s1"),
      generic("s1"),
      ev("shape.requested", { v: 1, surface_id: "s1", actor: "user" }),
      ev("view.derived", {
        v: 1,
        surface_id: "s1",
        tier: "shaped",
        basis: "generated",
        gen: { model: "openai:gpt-5.4-mini", ms: 800 },
      }),
      ev("shape.resolved", { v: 1, surface_id: "s1", outcome: "shaped" }),
    ]);
    const surface = p.surfaces.get("s1");
    expect(surface?.shapeRequest).toBe("idle");
    expect(surface?.viewState?.effectiveTier).toBe("shaped");
  });

  it("shape.resolved {no_fit} ⇒ no_fit, view unchanged (stays generic)", () => {
    const p = projectLedger([
      created("s1"),
      generic("s1"),
      ev("shape.requested", { v: 1, surface_id: "s1", actor: "user" }),
      ev("shape.resolved", {
        v: 1,
        surface_id: "s1",
        outcome: "no_fit",
        reason: "no confident view fit",
      }),
    ]);
    const surface = p.surfaces.get("s1");
    expect(surface?.shapeRequest).toBe("no_fit");
    expect(surface?.viewState?.effectiveTier).toBe("generic");
  });

  it("never advances lastSeq (parity with the Python SurfaceStore fold)", () => {
    const withoutShape = projectLedger([created("s1"), generic("s1")]);
    const lastSeqBefore = withoutShape.surfaces.get("s1")?.lastSeq;
    seq = 0; // rebuild from seq 1 so the two folds are directly comparable
    const withShape = projectLedger([
      created("s1"),
      generic("s1"),
      ev("shape.requested", { v: 1, surface_id: "s1", actor: "user" }),
      ev("shape.resolved", { v: 1, surface_id: "s1", outcome: "no_fit" }),
    ]);
    // The two later shape events do NOT bump the surface's lastSeq...
    expect(withShape.surfaces.get("s1")?.lastSeq).toBe(lastSeqBefore);
    // ...but the run watermark still counts every event.
    expect(withShape.latestSequenceNo).toBe(4);
  });

  it("tolerates a shape event for an unseen surface", () => {
    const p = projectLedger([
      created("s1"),
      generic("s1"),
      ev("shape.resolved", { v: 1, surface_id: "ghost", outcome: "no_fit" }),
    ]);
    expect(p.surfaces.has("ghost")).toBe(false);
    expect(p.surfaces.get("s1")?.shapeRequest).toBe("idle");
  });
});
