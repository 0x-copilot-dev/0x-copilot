// @vitest-environment node
// PRD-B4 — suggest-a-shape wire contracts. Guards the three new public types
// (`ShapeRequestBody`, `ShapeRequestAccepted`, `ShapeResolvedPayload`) against
// the shared ledger SSOT + the golden fixture, mirroring `ledger.test.ts`.
import { describe, expect, it } from "vitest";

import golden from "../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
import {
  LEDGER_EVENT_TYPES,
  isLedgerEventType,
  isSurfaceEventV2,
  type ShapeOutcome,
  type ShapeRequestAccepted,
  type ShapeRequestBody,
  type ShapeResolvedPayload,
} from "./ledger";

interface GoldenEvent {
  event_type: string;
  run_id: string;
  sequence_no: number;
  created_at: string;
  payload: Record<string, unknown>;
}

const goldenEvents = golden.events as unknown as GoldenEvent[];

describe("shape.resolved is a first-class ledger event type", () => {
  it("is in the event-type tuple + accepted by the guard", () => {
    expect(LEDGER_EVENT_TYPES).toContain("shape.resolved");
    expect(isLedgerEventType("shape.resolved")).toBe(true);
    // The prior standalone request event remains valid.
    expect(isLedgerEventType("shape.requested")).toBe(true);
  });

  it("accepts the golden shaped + no_fit sequences via isSurfaceEventV2", () => {
    const resolved = goldenEvents.filter(
      (event) => event.event_type === "shape.resolved",
    );
    expect(resolved).toHaveLength(2);
    const outcomes = resolved.map((event) => event.payload.outcome);
    expect(new Set(outcomes)).toEqual(new Set(["shaped", "no_fit"]));
    for (const event of resolved) {
      expect(isSurfaceEventV2(event)).toBe(true);
    }
  });

  it("rejects a shape.resolved payload missing the required outcome", () => {
    const base = goldenEvents.find(
      (event) => event.event_type === "shape.resolved",
    );
    expect(base).toBeDefined();
    const bad = JSON.parse(JSON.stringify(base)) as GoldenEvent;
    delete bad.payload.outcome;
    expect(isSurfaceEventV2(bad)).toBe(false);
  });
});

describe("B4 wire type shapes", () => {
  it("ShapeRequestBody carries run_id", () => {
    const body: ShapeRequestBody = { run_id: "run_123" };
    expect(body.run_id).toBe("run_123");
  });

  it("ShapeRequestAccepted pins status to the literal 'requested'", () => {
    const accepted: ShapeRequestAccepted = {
      surface_id: "surface_x",
      status: "requested",
    };
    expect(accepted.status).toBe("requested");
  });

  it("ShapeResolvedPayload round-trips both outcomes; reason optional", () => {
    const shaped: ShapeResolvedPayload = {
      v: 1,
      surface_id: "surface_x",
      outcome: "shaped",
    };
    const noFit: ShapeResolvedPayload = {
      v: 1,
      surface_id: "surface_x",
      outcome: "no_fit",
      reason: "no confident view fit",
    };
    const outcomes: ShapeOutcome[] = [shaped.outcome, noFit.outcome];
    expect(outcomes).toEqual(["shaped", "no_fit"]);
    expect(shaped.reason).toBeUndefined();
    expect(noFit.reason).toBe("no confident view fit");
  });
});
