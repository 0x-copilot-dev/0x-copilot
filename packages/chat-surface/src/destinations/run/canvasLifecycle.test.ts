import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectCanvasLifecycle } from "./canvasLifecycle";

function event(
  sequence_no: number,
  event_type: string,
  payload: Record<string, unknown> = {},
): RuntimeEventEnvelope {
  return {
    event_id: `evt-${sequence_no}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no,
    event_type: event_type as RuntimeEventEnvelope["event_type"],
    activity_kind: "event",
    payload,
    created_at: new Date(1_700_000_000_000 + sequence_no * 1000).toISOString(),
  };
}

describe("projectCanvasLifecycle (PRD-B3)", () => {
  it("keeps ordinary arithmetic/chat responses out of the canvas", () => {
    const projection = projectCanvasLifecycle([
      event(1, "final_response", { text: "The ball costs 5 cents." }),
      event(2, "run_completed", { status: "completed" }),
    ]);
    expect(projection.lifecycle).toBe("chat_only");
    expect(projection.tabs).toEqual([]);
  });

  it("requires a durable presentation fact for an artifact and keeps revision identity", () => {
    const events = [
      event(1, "artifact.created", {
        artifact_id: "art_1",
        kind: "code",
        revision: 1,
      }),
      event(2, "artifact.presentation_decided", {
        artifact_id: "art_1",
        decision: "canvas",
      }),
      event(3, "artifact.revised", { artifact_id: "art_1", revision: 2 }),
    ];
    const projection = projectCanvasLifecycle(events);
    expect(projection.tabs).toMatchObject([
      { key: "artifact:art_1", kind: "artifact", revision: 2 },
    ]);
    expect(projection.activeSubjectKey).toBe("artifact:art_1");
  });

  it("projects a table surface, stage, and gate with deterministic priority", () => {
    const projection = projectCanvasLifecycle([
      event(1, "surface.created", {
        surface_id: "table://deals",
        kind: "table",
        title: "Top deals",
      }),
      event(2, "write.staged", {
        stage_id: "stage_1",
        display_target: "Update deal",
        revision: 1,
      }),
      event(3, "gate.opened", { gate_id: "gate_1" }),
    ]);
    expect(projection.lifecycle).toBe("parked");
    expect(projection.tabs.map((subject) => subject.key)).toEqual([
      "effect:stage_1",
      "surface:table://deals",
    ]);
    expect(projection.pendingSubjectKeys).toEqual([
      "effect:stage_1",
      "gate:gate_1",
    ]);
  });

  it("retains a terminal receipt without adding or selecting a receipt tab", () => {
    const projection = projectCanvasLifecycle([
      event(1, "surface.created", {
        surface_id: "record://issue-1",
        kind: "record",
        title: "Issue 1",
      }),
      event(2, "surface.created", {
        surface_id: "receipt://run-1",
        kind: "receipt",
        title: "Run receipt",
      }),
      event(3, "receipt.emitted", { surface_id: "receipt://run-1" }),
      event(4, "run_completed", { status: "completed" }),
    ]);
    expect(projection.tabs.map((subject) => subject.kind)).toEqual(["surface"]);
    expect(projection.activeSubjectKey).toBe("surface:record://issue-1");
    expect(projection.terminalReceipt).toMatchObject({ kind: "receipt" });
  });

  it("is byte-equivalent for every replay prefix, including failures and retryable raw state", () => {
    const events = [
      event(1, "tool_call_started"),
      event(2, "tool_result", {
        status: "failed",
        error_message: "Retry later",
      }),
      event(3, "surface.created", {
        surface_id: "raw://response",
        kind: "raw",
        title: "Raw response",
      }),
      event(4, "run_failed"),
    ];
    for (let end = 0; end <= events.length; end += 1) {
      expect(projectCanvasLifecycle(events.slice(0, end))).toEqual(
        projectCanvasLifecycle([...events.slice(0, end)]),
      );
    }
    expect(projectCanvasLifecycle(events).lifecycle).toBe("presenting");
  });
});
