// projectPendingCards — the §5 pending predicate, TypeScript twin of the Python
// `PendingWorkFold`. Same case list PLUS the prefix property test against the
// SHARED A1 golden fixture (the ts⇄py pending-parity referee, and the DoD
// "cards appear/disappear exactly with ledger state" projection test, client
// side). Hostile title/purpose strings must survive as plain data.

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

// The SHARED A1 golden events (service-contracts) — the SAME fixture the Python
// fold's prefix property test folds, so the two languages agree transitively.
import goldenEvents from "../../../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";

import {
  projectPendingCards,
  type PendingCard,
} from "./pendingCardsProjection";

let seq = 0;

function ev(
  event_type: string,
  payload: Record<string, unknown>,
  sequence_no = ++seq,
): RuntimeEventEnvelope {
  return {
    event_id: `evt_${sequence_no}`,
    run_id: "run0abcdef0123456789abcdef01234567",
    sequence_no,
    event_type,
    payload,
    created_at: "2026-07-24T00:00:00+00:00",
  } as unknown as RuntimeEventEnvelope;
}

function gateOpened(
  gateId: string,
  purpose = "to read ENG-1",
): RuntimeEventEnvelope {
  return ev("gate.opened", {
    v: 1,
    gate_id: gateId,
    connector: "linear",
    purpose,
    scopes: ["read:issues"],
    auth_state: "missing",
  });
}

function gateResolved(
  gateId: string,
  outcome: "connected" | "cancelled" = "connected",
): RuntimeEventEnvelope {
  return ev("gate.resolved", { v: 1, gate_id: gateId, outcome });
}

function staged(stage: string, surface: string): RuntimeEventEnvelope {
  return ev("write.staged", {
    v: 1,
    stage_id: stage,
    surface_id: surface,
    target: { connector: "gmail", op: "send" },
    proposal_ref: "draft://abcdef0123456789abcdef0123456789/v1",
  });
}

function revision(stage: string, rev: number): RuntimeEventEnvelope {
  return ev("revision.added", {
    v: 1,
    stage_id: stage,
    rev,
    author: "agent",
    diff_ref: "draft://abcdef0123456789abcdef0123456789/v1..v1",
    proposal_ref: "draft://abcdef0123456789abcdef0123456789/v1",
    authorship_spans: [],
  });
}

function decision(stage: string, d: string, rev: number): RuntimeEventEnvelope {
  return ev("decision.recorded", {
    v: 1,
    stage_id: stage,
    decision: d,
    scope: { rev },
    actor: "user",
  });
}

function rowsetStaged(
  stage: string,
  surface: string,
  rows: number,
  holds: { row_key: string; reason: string }[] = [],
): RuntimeEventEnvelope {
  return ev("write.staged", {
    v: 1,
    stage_id: stage,
    surface_id: surface,
    target: { connector: "linear", op: "update_issue" },
    proposal_ref: `stage://${stage}/v1`,
    rows,
    agent_holds: holds,
  });
}

function rowsetRev(stage: string, keys: string[]): RuntimeEventEnvelope {
  return ev("revision.added", {
    v: 1,
    stage_id: stage,
    rev: 1,
    author: "agent",
    diff_ref: `stage://${stage}/v1`,
    proposal_ref: `stage://${stage}/v1`,
    rowset: {
      rows: keys.map((k) => ({
        row_key: k,
        title: `Row ${k}`,
        target_args: { id: k },
        changes: [{ field: "priority", old: 1, new: 2 }],
      })),
    },
  });
}

function rowDecision(
  stage: string,
  d: string,
  keys: string[],
): RuntimeEventEnvelope {
  return ev("decision.recorded", {
    v: 1,
    stage_id: stage,
    decision: d,
    scope: { row_keys: keys },
    actor: "user",
  });
}

function keysOf(cards: readonly PendingCard[]): [string, string][] {
  return cards
    .map((c) => [c.itemKind, c.gateId ?? c.stageId ?? ""] as [string, string])
    .sort((a, b) => (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
}

describe("projectPendingCards — gate predicate", () => {
  it("an open gate is pending", () => {
    seq = 0;
    const cards = projectPendingCards([gateOpened("g1")], "run_1");
    expect(cards).toHaveLength(1);
    expect(cards[0].itemKind).toBe("gate");
    expect(cards[0].gateId).toBe("g1");
    expect(cards[0].title).toBe("to read ENG-1");
    expect(cards[0].connector).toBe("linear");
    expect(cards[0].rowsPending).toBeNull();
  });

  it("a connected-resolved gate is absent", () => {
    seq = 0;
    const cards = projectPendingCards(
      [gateOpened("g1"), gateResolved("g1", "connected")],
      "run_1",
    );
    expect(cards).toHaveLength(0);
  });

  it("a cancelled-resolved gate is absent", () => {
    seq = 0;
    const cards = projectPendingCards(
      [gateOpened("g1"), gateResolved("g1", "cancelled")],
      "run_1",
    );
    expect(cards).toHaveLength(0);
  });
});

describe("projectPendingCards — single-artifact stage predicate", () => {
  it("a staged stage is pending with its surface + target line", () => {
    seq = 0;
    const cards = projectPendingCards(
      [staged("s1", "surf1"), revision("s1", 1)],
      "run_1",
    );
    expect(cards).toHaveLength(1);
    const card = cards[0];
    expect(card.itemKind).toBe("staged_write");
    expect(card.stageId).toBe("s1");
    expect(card.surfaceId).toBe("surf1");
    expect(card.title).toBe("gmail · send");
    expect(card.connector).toBe("gmail");
    expect(card.rowsTotal).toBeNull();
  });

  it("approved / rejected / applied stages are absent", () => {
    seq = 0;
    const approved = projectPendingCards(
      [staged("s1", "surf1"), revision("s1", 1), decision("s1", "approve", 1)],
      "run_1",
    );
    expect(approved).toHaveLength(0);
    seq = 0;
    const rejected = projectPendingCards(
      [staged("s2", "surf2"), revision("s2", 1), decision("s2", "reject", 1)],
      "run_1",
    );
    expect(rejected).toHaveLength(0);
  });

  it("restore returns a rejected stage to pending", () => {
    seq = 0;
    const cards = projectPendingCards(
      [
        staged("s1", "surf1"),
        revision("s1", 1),
        decision("s1", "reject", 1),
        decision("s1", "restore", 1),
      ],
      "run_1",
    );
    expect(keysOf(cards)).toEqual([["staged_write", "s1"]]);
  });
});

describe("projectPendingCards — row-set predicate (D3 accounting)", () => {
  it("a fresh row-set is pending with all rows waiting", () => {
    seq = 0;
    const cards = projectPendingCards(
      [rowsetStaged("r1", "surfR", 3), rowsetRev("r1", ["a", "b", "c"])],
      "run_1",
    );
    expect(cards).toHaveLength(1);
    expect(cards[0].rowsTotal).toBe(3);
    expect(cards[0].rowsPending).toBe(3);
  });

  it("partial user decisions reduce the pending count", () => {
    seq = 0;
    const cards = projectPendingCards(
      [
        rowsetStaged("r1", "surfR", 3),
        rowsetRev("r1", ["a", "b", "c"]),
        rowDecision("r1", "approve", ["a"]),
        rowDecision("r1", "hold", ["b"]),
      ],
      "run_1",
    );
    expect(cards).toHaveLength(1);
    expect(cards[0].rowsPending).toBe(1);
  });

  it("an agent pre-hold still counts as pending (FR-C7)", () => {
    seq = 0;
    const cards = projectPendingCards(
      [
        rowsetStaged("r1", "surfR", 3, [{ row_key: "b", reason: "ambiguous" }]),
        rowsetRev("r1", ["a", "b", "c"]),
      ],
      "run_1",
    );
    expect(cards[0].rowsPending).toBe(3);
  });

  it("a fully user-decided row-set drops from the queue", () => {
    seq = 0;
    const cards = projectPendingCards(
      [
        rowsetStaged("r1", "surfR", 3),
        rowsetRev("r1", ["a", "b", "c"]),
        rowDecision("r1", "hold", ["a", "b", "c"]),
      ],
      "run_1",
    );
    expect(cards).toHaveLength(0);
  });
});

describe("projectPendingCards — hostile input", () => {
  it("a hostile purpose survives as plain data (rendered as text only)", () => {
    seq = 0;
    const hostile = "<img src=x onerror=alert(1)>";
    const cards = projectPendingCards([gateOpened("g1", hostile)], "run_1");
    // The selector never sanitizes / never dereferences — it keeps the string
    // verbatim; the CARD renders it as a text node (no dangerouslySetInnerHTML).
    expect(cards[0].title).toBe(hostile);
  });

  it("malformed payloads are skipped, never thrown", () => {
    seq = 0;
    const cards = projectPendingCards(
      [
        ev("gate.opened", { v: 1 }), // no gate_id
        gateOpened("g_real"),
      ],
      "run_1",
    );
    expect(keysOf(cards)).toEqual([["gate", "g_real"]]);
  });
});

describe("projectPendingCards — golden fixture prefix property (ts⇄py parity)", () => {
  const events = (goldenEvents as { events: unknown[] })
    .events as unknown as RuntimeEventEnvelope[];

  it("the full golden fold has an empty queue (checked-in expectation)", () => {
    expect(projectPendingCards(events, null)).toHaveLength(0);
  });

  it("every prefix folds to exactly the incremental pending set", () => {
    // Byte-identical to the Python `test_every_event_prefix_matches_incremental_state`
    // expectation: gate_01 opens at seq 1 and resolves at seq 2 — the single
    // interval where the queue is non-empty.
    const expected: Record<number, [string, string][]> = {};
    for (let n = 0; n <= events.length; n++) expected[n] = [];
    expected[1] = [["gate", "gate_01"]];
    for (let n = 0; n <= events.length; n++) {
      const cards = projectPendingCards(events.slice(0, n), null);
      expect(keysOf(cards)).toEqual(expected[n]);
    }
  });
});
