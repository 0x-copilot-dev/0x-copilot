import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import corpus from "../../../../service-contracts/src/copilot_service_contracts/canvas_lifecycle_corpus.json";
import {
  PYTHON_CORPUS_TIMEOUT_MS,
  runPythonCorpus,
} from "../../test/repoPaths.testutil";

import { projectCanvasLifecycle } from "./canvasLifecycle";

type CorpusEvent = {
  readonly sequence_no: number;
  readonly event_type: string;
  readonly payload: Record<string, unknown>;
};

type CorpusCase = {
  readonly id: string;
  readonly events: readonly CorpusEvent[];
};

// Repo-root-relative; `runPythonCorpus` resolves the root from the test tree's
// own location, so this works from a main checkout and from a git worktree.
const PYTHON_RUNNER =
  "services/ai-backend/tests/unit/agent_runtime/presentation/lifecycle_corpus_runner.py";

function corpusEvents(events: readonly CorpusEvent[]): RuntimeEventEnvelope[] {
  return events.map((entry, index) =>
    event(entry.sequence_no, entry.event_type, entry.payload, index),
  );
}

function event(
  sequence_no: number,
  event_type: string,
  payload: Record<string, unknown> = {},
  sourceIndex = 0,
): RuntimeEventEnvelope {
  return {
    event_id: `evt-${sequence_no}-${sourceIndex}`,
    run_id: "run-1",
    conversation_id: "conv-1",
    sequence_no,
    event_type: event_type as RuntimeEventEnvelope["event_type"],
    activity_kind: "event",
    payload,
    created_at: new Date(1_700_000_000_000 + sequence_no * 1000).toISOString(),
  };
}

function projectCorpus() {
  return {
    schema_version: corpus.schema_version,
    cases: (corpus.cases as readonly CorpusCase[]).map((caseItem) => {
      const events = corpusEvents(caseItem.events);
      return {
        id: caseItem.id,
        prefixes: Array.from({ length: events.length + 1 }, (_, end) =>
          projectCanvasLifecycle(events.slice(0, end)),
        ),
      };
    }),
  };
}

function pythonCorpusProjection() {
  return runPythonCorpus<ReturnType<typeof projectCorpus>>(
    PYTHON_RUNNER,
    "Canvas lifecycle differential test",
  );
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

  // The original defect: `ls` was answered by the tombstone backend ("no folder
  // attached"), stamped failed, and — because `failure` outranked
  // `hasFinalResponse` — repainted the whole canvas as "This run needs
  // attention", beside a chat pane holding a complete, correct answer.
  it("reports a recovered step failure as answered in chat, not as an alarm", () => {
    const events = [
      event(1, "tool_call_started"),
      event(2, "tool_result", {
        status: "failed",
        safe_message: "Retry later",
      }),
      event(3, "final_response"),
      event(4, "run_completed", { status: "completed" }),
    ];

    const projection = projectCanvasLifecycle(events);

    expect(projection.lifecycle).toBe("chat_only");
    // The failure text survives for the chat stream's terminal beat; it just
    // no longer steers the canvas.
    expect(projection.failure).toBe("Retry later");
  });

  it("does not let a dead run invent a canvas state either", () => {
    const events = [
      event(1, "tool_call_started"),
      event(2, "run_failed", { safe_message: "Worker lost" }),
    ];

    const projection = projectCanvasLifecycle(events);

    // Nothing was produced, so the canvas says exactly that — the verdict on
    // the run itself is the chat stream's job.
    expect(projection.lifecycle).toBe("complete_empty");
    expect(projection.terminalStatus).toBe("failed");
  });

  it(
    "differentially matches the Python fold for every shared corpus prefix",
    () => {
      // This is deliberately not a checked-in expected projection. The Python
      // projection is executed now, over the same raw event corpus, so a
      // semantic drift in either implementation fails with the exact prefix.
      expect(projectCorpus()).toEqual(pythonCorpusProjection());
    },
    PYTHON_CORPUS_TIMEOUT_MS,
  );
});
