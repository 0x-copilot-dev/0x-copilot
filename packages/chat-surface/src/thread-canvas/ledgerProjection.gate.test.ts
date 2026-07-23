// PRD-C2 — gate fold tests for `projectLedger`. Peers of B1's surface-fold
// tests: `gate.opened` folds to a gate card, `gate.resolved` resolves it and
// feeds the posture signal, and every branch tolerates malformed payloads.

import { beforeEach, describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectLedger } from "./ledgerProjection";

const RUN = "a7f3c9d2e5b14f60";
const GATE_ID = `mcp_auth:${RUN}:seed:linear`;

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

function opened(extra: Record<string, unknown> = {}): RuntimeEventEnvelope {
  return ev("gate.opened", {
    v: 1,
    gate_id: GATE_ID,
    connector: "linear",
    purpose: "to run create_issue on Linear",
    scopes: ["docs:read", "docs:write"],
    auth_state: "missing",
    ...extra,
  });
}

describe("projectLedger — gates", () => {
  it("folds gate.opened into an open gate card", () => {
    const p = projectLedger([opened()]);
    const gate = p.gates.get(GATE_ID);
    expect(gate).toBeDefined();
    expect(gate!.connector).toBe("linear");
    expect(gate!.purpose).toContain("create_issue");
    expect(gate!.scopes).toEqual(["docs:read", "docs:write"]);
    expect(gate!.authState).toBe("missing");
    // op_class is not on the ledger row — fail closed to write.
    expect(gate!.opClass).toBe("write");
    // server id recovered from the deterministic gate id (colons preserved).
    expect(gate!.serverId).toBe("seed:linear");
    expect(gate!.resolved).toBe(false);
    expect(p.openGates).toHaveLength(1);
    expect(p.bypassFromLedger).toBe(false);
    expect(gate!.ledgerId).toMatch(/^r[0-9a-f]+·\d+$/);
  });

  it("resolves the gate and drops it from openGates", () => {
    const p = projectLedger([
      opened(),
      ev("gate.resolved", {
        v: 1,
        gate_id: GATE_ID,
        outcome: "connected",
        write_policy: "ask_first",
      }),
    ]);
    const gate = p.gates.get(GATE_ID)!;
    expect(gate.resolved).toBe(true);
    expect(gate.outcome).toBe("connected");
    expect(gate.writePolicy).toBe("ask_first");
    expect(p.openGates).toHaveLength(0);
    expect(p.bypassFromLedger).toBe(false);
  });

  it("derives the bypass posture from an allow_always resolution", () => {
    const p = projectLedger([
      opened(),
      ev("gate.resolved", {
        v: 1,
        gate_id: GATE_ID,
        outcome: "connected",
        write_policy: "allow_always",
      }),
    ]);
    expect(p.bypassFromLedger).toBe(true);
  });

  it("cancelled resolution is not a bypass and drops the open gate", () => {
    const p = projectLedger([
      opened(),
      ev("gate.resolved", { v: 1, gate_id: GATE_ID, outcome: "cancelled" }),
    ]);
    const gate = p.gates.get(GATE_ID)!;
    expect(gate.outcome).toBe("cancelled");
    expect(gate.writePolicy).toBeNull();
    expect(p.openGates).toHaveLength(0);
    expect(p.bypassFromLedger).toBe(false);
  });

  it("ignores a gate.resolved for an unseen gate", () => {
    const p = projectLedger([
      ev("gate.resolved", { v: 1, gate_id: "unknown", outcome: "connected" }),
    ]);
    expect(p.gates.size).toBe(0);
  });

  it("tolerates malformed gate payloads without throwing", () => {
    const p = projectLedger([
      ev("gate.opened", { v: 1 }), // no gate_id
      ev("gate.opened", { v: 1, gate_id: GATE_ID, scopes: "nope" }),
    ]);
    const gate = p.gates.get(GATE_ID)!;
    expect(gate.scopes).toEqual([]);
    expect(gate.authState).toBe("missing");
  });

  it("does not touch surfaces or the watermark semantics", () => {
    const p = projectLedger([opened()]);
    expect(p.surfaces.size).toBe(0);
    expect(p.latestSequenceNo).toBe(1);
    // gate events do not advance the surface hydration trigger.
    expect(p.lastLedgerSeq).toBe(0);
  });
});
