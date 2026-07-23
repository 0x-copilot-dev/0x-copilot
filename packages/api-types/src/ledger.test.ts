// @vitest-environment node
import { describe, expect, it } from "vitest";

import contract from "../../service-contracts/src/copilot_service_contracts/work_ledger.json";
import golden from "../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
import {
  LEDGER_EVENT_TYPES,
  formatLedgerId,
  isLedgerEventType,
  isPendingWorkResponse,
  isSurfaceEventV2,
  parseLedgerId,
  type ActionClass,
  type ApplyResult,
  type ClassificationBasis,
  type DecisionActor,
  type DecisionKind,
  type GateAuthState,
  type GateOutcome,
  type RevisionAuthor,
  type SurfaceKind,
  type UsagePurpose,
  type ViewBasis,
  type ViewKeep,
  type ViewTier,
  type WritePolicy,
} from "./ledger";

// Local tuples pinned to their unions by `satisfies` and to the JSON by the
// runtime deep-equal below. This is the ts↔py parity pin, transitively through
// the shared SSOT JSON: union ⇄ tuple ⇄ JSON ⇄ pydantic.
const ENUM_TUPLES = {
  auth_state: [
    "missing",
    "expired",
    "insufficient",
  ] as const satisfies readonly GateAuthState[],
  gate_outcome: [
    "connected",
    "cancelled",
  ] as const satisfies readonly GateOutcome[],
  write_policy: [
    "ask_first",
    "allow_always",
  ] as const satisfies readonly WritePolicy[],
  action_class: [
    "read",
    "write",
    "unknown",
  ] as const satisfies readonly ActionClass[],
  classification_basis: [
    "catalog",
    "annotation",
    "default",
  ] as const satisfies readonly ClassificationBasis[],
  surface_kind: [
    "record",
    "message",
    "table",
    "call",
    "raw",
    "receipt",
    "gate",
  ] as const satisfies readonly SurfaceKind[],
  view_tier: [
    "raw",
    "generic",
    "shaped",
  ] as const satisfies readonly ViewTier[],
  view_basis: [
    "schema",
    "registry",
    "generated",
  ] as const satisfies readonly ViewBasis[],
  view_keep: ["generic", "shaped"] as const satisfies readonly ViewKeep[],
  revision_author: [
    "agent",
    "user",
  ] as const satisfies readonly RevisionAuthor[],
  decision_kind: [
    "approve",
    "reject",
    "hold",
    "restore",
  ] as const satisfies readonly DecisionKind[],
  decision_actor: [
    "user",
    "policy",
  ] as const satisfies readonly DecisionActor[],
  apply_result: [
    "applied",
    "partial",
    "failed",
  ] as const satisfies readonly ApplyResult[],
  usage_purpose: [
    "run",
    "subagent",
    "view_shaping",
    "shape_request",
  ] as const satisfies readonly UsagePurpose[],
} as const;

interface GoldenEvent {
  event_type: string;
  run_id: string;
  sequence_no: number;
  created_at: string;
  payload: Record<string, unknown>;
}

const goldenEvents = golden.events as unknown as GoldenEvent[];

function cloneEvent(event: GoldenEvent): GoldenEvent {
  return JSON.parse(JSON.stringify(event)) as GoldenEvent;
}

describe("LEDGER_EVENT_TYPES", () => {
  it("matches the service-contracts JSON events, in order", () => {
    expect([...LEDGER_EVENT_TYPES]).toEqual(Object.keys(contract.events));
  });

  it("covers all 14 event types", () => {
    expect(LEDGER_EVENT_TYPES).toHaveLength(14);
  });

  it("isLedgerEventType accepts every listed type and rejects others", () => {
    for (const type of LEDGER_EVENT_TYPES) {
      expect(isLedgerEventType(type)).toBe(true);
    }
    expect(isLedgerEventType("gate.exploded")).toBe(false);
    expect(isLedgerEventType(42)).toBe(false);
    expect(isLedgerEventType(undefined)).toBe(false);
  });
});

describe("enum unions match the contract enums", () => {
  it("every enum union tuple matches contract.enums (values + order)", () => {
    const contractEnums = contract.enums as Record<string, readonly string[]>;
    // Key sets agree — no enum in the JSON without a ts tuple and vice versa.
    expect(Object.keys(ENUM_TUPLES).sort()).toEqual(
      Object.keys(contractEnums).sort(),
    );
    for (const [key, tuple] of Object.entries(ENUM_TUPLES)) {
      expect([...tuple]).toEqual(contractEnums[key]);
    }
  });
});

describe("isSurfaceEventV2", () => {
  it("accepts every golden event", () => {
    for (const event of goldenEvents) {
      expect(isSurfaceEventV2(event)).toBe(true);
    }
  });

  it("rejects an unknown event_type", () => {
    const bad = cloneEvent(goldenEvents[0]);
    bad.event_type = "gate.exploded";
    expect(isSurfaceEventV2(bad)).toBe(false);
  });

  it("rejects a payload missing a required key", () => {
    const bad = cloneEvent(goldenEvents[0]);
    // gate.opened requires `connector`.
    delete bad.payload.connector;
    expect(isSurfaceEventV2(bad)).toBe(false);
  });

  it("rejects a payload whose v !== 1", () => {
    const bad = cloneEvent(goldenEvents[0]);
    bad.payload.v = 2;
    expect(isSurfaceEventV2(bad)).toBe(false);
  });

  it("rejects a non-positive-integer sequence_no", () => {
    const zero = cloneEvent(goldenEvents[0]);
    zero.sequence_no = 0;
    expect(isSurfaceEventV2(zero)).toBe(false);
    const frac = cloneEvent(goldenEvents[0]);
    frac.sequence_no = 1.5;
    expect(isSurfaceEventV2(frac)).toBe(false);
  });

  it("rejects non-objects", () => {
    expect(isSurfaceEventV2(null)).toBe(false);
    expect(isSurfaceEventV2("gate.opened")).toBe(false);
    expect(isSurfaceEventV2(undefined)).toBe(false);
  });
});

describe("ledger-id codec", () => {
  const triples = golden.golden_ids as ReadonlyArray<{
    run_id: string;
    sequence_no: number;
    ledger_id: string;
  }>;

  function normalizedShort(runId: string): string {
    return runId.toLowerCase().replaceAll("-", "").slice(0, 3);
  }

  it("formatLedgerId reproduces every golden triple", () => {
    expect(triples.length).toBeGreaterThan(0);
    for (const { run_id, sequence_no, ledger_id } of triples) {
      expect(formatLedgerId(run_id, sequence_no)).toBe(ledger_id);
    }
  });

  it("parseLedgerId round-trips every golden triple", () => {
    for (const { run_id, sequence_no, ledger_id } of triples) {
      expect(parseLedgerId(ledger_id)).toEqual({
        run_short: normalizedShort(run_id),
        sequence_no,
      });
    }
  });

  it("formatLedgerId pads to three and grows without truncation", () => {
    const runId = "a7f3c9d2e5b14f60a7f3c9d2e5b14f60";
    expect(formatLedgerId(runId, 7)).toBe("ra7f·007");
    expect(formatLedgerId(runId, 42)).toBe("ra7f·042");
    expect(formatLedgerId(runId, 1042)).toBe("ra7f·1042");
  });

  it("formatLedgerId strips dashes and lower-cases", () => {
    expect(formatLedgerId("A-B-C-D-E-F", 1)).toBe("rabc·001");
  });

  it("formatLedgerId throws RangeError for sequence_no < 1", () => {
    const runId = "a7f3c9d2e5b14f60a7f3c9d2e5b14f60";
    expect(() => formatLedgerId(runId, 0)).toThrow(RangeError);
    expect(() => formatLedgerId(runId, -1)).toThrow(RangeError);
    expect(() => formatLedgerId(runId, 1.5)).toThrow(RangeError);
  });

  it("formatLedgerId throws RangeError for a too-short run id", () => {
    expect(() => formatLedgerId("ab", 5)).toThrow(RangeError);
    expect(() => formatLedgerId("", 5)).toThrow(RangeError);
    expect(() => formatLedgerId("--", 5)).toThrow(RangeError);
  });

  it("parseLedgerId returns null for malformed input", () => {
    const malformed = [
      "",
      "xa7f·007",
      "ra7f.007",
      "ra7f*007",
      "rA7F·007",
      "ra7·007",
      "ra7f·07",
      "ra7f·007xx",
      "ra7f·007 ",
      "ra7f-007",
      "ra7f·",
    ];
    for (const text of malformed) {
      expect(parseLedgerId(text)).toBeNull();
    }
  });
});

describe("isPendingWorkResponse (PRD-E2)", () => {
  it("accepts a well-formed empty response", () => {
    expect(isPendingWorkResponse({ v: 1, items: [], agents: [] })).toBe(true);
  });

  it("accepts a populated response", () => {
    const resp = {
      v: 1,
      items: [
        {
          v: 1,
          item_kind: "gate",
          run_id: "run_1",
          conversation_id: "conv_1",
          conversation_title: "Read issue",
          gate_id: "g1",
          stage_id: null,
          surface_id: null,
          title: "to read ENG-1",
          connector: "linear",
          op: null,
          ledger_id: "ra7f·001",
          opened_sequence_no: 1,
          opened_at: "2026-07-24T00:00:00+00:00",
          rows_pending: null,
          rows_total: null,
        },
      ],
      agents: [
        {
          v: 1,
          run_id: "run_1",
          conversation_id: "conv_1",
          conversation_title: "Read issue",
          run_status: "waiting_for_approval",
          pending_count: 1,
        },
      ],
    };
    expect(isPendingWorkResponse(resp)).toBe(true);
  });

  it("rejects a wrong version or missing collections", () => {
    expect(isPendingWorkResponse({ v: 2, items: [], agents: [] })).toBe(false);
    expect(isPendingWorkResponse({ v: 1, items: [] })).toBe(false);
    expect(isPendingWorkResponse({ v: 1, agents: [] })).toBe(false);
    expect(isPendingWorkResponse(null)).toBe(false);
    expect(isPendingWorkResponse("nope")).toBe(false);
  });
});
