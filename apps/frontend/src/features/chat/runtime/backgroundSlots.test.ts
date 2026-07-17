// PR 2.2.1 — background slot reducer tests.
//
// Cover the pure-data helpers that sit underneath
// `useBackgroundChatStreams`: applying events into a slot, marking a
// run terminal, deriving the live-set, and the LRU eviction policy.
//
// The hook itself wires these helpers into React state + the SSE
// registry; we test the helpers directly so the contract is stable
// regardless of how the hook is composed.

import { describe, expect, it } from "vitest";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import {
  applyEventToSlot,
  emptySlot,
  evictColdContent,
  liveConversationIds,
  markRunTerminal,
  type BackgroundSlot,
} from "./backgroundSlots";

function makeRunCompleted(
  runId: string,
  sequence: number,
): RuntimeEventEnvelope {
  return {
    event_id: `evt-${runId}-${sequence}`,
    run_id: runId,
    sequence_no: sequence,
    timestamp: "2026-05-05T12:00:00Z",
    event_type: "run_completed",
    payload: { status: "completed" },
    activity_kind: null,
    display_title: null,
    summary: null,
    status: null,
  } as unknown as RuntimeEventEnvelope;
}

describe("backgroundSlots", () => {
  it("applyEventToSlot bumps latest sequence and preserves citations registry shape", () => {
    const slot: BackgroundSlot = {
      ...emptySlot(),
      activeRunId: "run-1",
    };
    const next = applyEventToSlot(slot, makeRunCompleted("run-1", 7));
    expect(next.latestSequenceByRunId.get("run-1")).toBe(7);
    expect(next.activeRunId).toBe("run-1"); // markRunTerminal is a separate step
  });

  it("markRunTerminal clears activeRunId only when it matches", () => {
    const slot: BackgroundSlot = {
      ...emptySlot(),
      activeRunId: "run-1",
    };
    const cleared = markRunTerminal(slot, "run-1", "Done");
    expect(cleared.activeRunId).toBeNull();
    expect(cleared.status).toBe("Done");

    const otherRun = markRunTerminal(slot, "run-2", "Other run done");
    expect(otherRun.activeRunId).toBe("run-1");
    expect(otherRun.status).toBe("Other run done");
  });

  it("liveConversationIds derives the set from slots with active runs", () => {
    const slots = new Map<string, BackgroundSlot>([
      ["c1", { ...emptySlot(), activeRunId: "r1" }],
      ["c2", { ...emptySlot() }],
      ["c3", { ...emptySlot(), activeRunId: "r3" }],
    ]);
    const live = liveConversationIds(slots);
    expect([...live].sort()).toEqual(["c1", "c3"]);
  });

  it("evictColdContent drops least-recently-visible terminal slots over the cap", () => {
    const slots = new Map<string, BackgroundSlot>([
      ["c1", { ...emptySlot(), lastVisibleAt: 1 }], // oldest, terminal
      ["c2", { ...emptySlot(), lastVisibleAt: 2 }],
      ["c3", { ...emptySlot(), lastVisibleAt: 3, activeRunId: "r3" }],
      ["c4", { ...emptySlot(), lastVisibleAt: 4 }],
    ]);
    const protectedSet = new Set<string>(["c4"]); // visible
    const out = evictColdContent(slots, 2, protectedSet);
    // c3 (live) and c4 (visible) survive; c1 evicts before c2.
    expect([...out.keys()].sort()).toEqual(["c3", "c4"]);
  });

  it("evictColdContent never drops a slot with an active run", () => {
    const slots = new Map<string, BackgroundSlot>([
      ["c1", { ...emptySlot(), lastVisibleAt: 1, activeRunId: "r1" }],
      ["c2", { ...emptySlot(), lastVisibleAt: 2, activeRunId: "r2" }],
      ["c3", { ...emptySlot(), lastVisibleAt: 3 }],
    ]);
    const protectedSet = new Set<string>(["c3"]); // visible
    const out = evictColdContent(slots, 1, protectedSet);
    // No eligible victim — every cold slot has a live run. Cap softens.
    expect([...out.keys()].sort()).toEqual(["c1", "c2", "c3"]);
  });
});
