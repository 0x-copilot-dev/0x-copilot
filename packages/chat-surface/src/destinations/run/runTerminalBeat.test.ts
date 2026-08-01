import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectRunTerminalBeat } from "./runTerminalBeat";

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

describe("projectRunTerminalBeat", () => {
  it("says nothing while the run is still going", () => {
    expect(projectRunTerminalBeat([event(1, "tool_call_started")])).toBeNull();
  });

  it("says nothing about a run that completed", () => {
    expect(
      projectRunTerminalBeat([
        event(1, "final_response"),
        event(2, "run_completed", { status: "completed" }),
      ]),
    ).toBeNull();
  });

  // The whole point of the rework: a run that hit a bad step and still
  // answered gets NO verdict. A card here would be the same contradiction the
  // canvas panel used to produce, just in a new location.
  it("says nothing when the run failed but still answered", () => {
    expect(
      projectRunTerminalBeat([
        event(1, "tool_result", { status: "failed" }),
        event(2, "final_response"),
        event(3, "run_failed", { error_code: "RUN_WORKER_LOST" }),
      ]),
    ).toBeNull();
  });

  it("says nothing when the user cancelled — their own decision needs no verdict", () => {
    expect(projectRunTerminalBeat([event(1, "run_cancelled")])).toBeNull();
  });

  it("reports a run that died with the runtime's typed cause", () => {
    const beat = projectRunTerminalBeat([
      event(1, "tool_call_started"),
      event(2, "run_failed", {
        presentation: {
          title: "Run interrupted",
          summary:
            "0xCopilot stopped this run because the worker became unresponsive.",
          code: "RUN_WORKER_LOST",
          retryable: true,
        },
      }),
    ]);

    expect(beat).toMatchObject({
      title: "Run interrupted",
      code: "RUN_WORKER_LOST",
      retryable: true,
      status: "failed",
    });
  });

  // `presentation` rides on the ENVELOPE, not inside `payload`. Reading the
  // wrong one yields no code and no retryability — silently reproducing the
  // generic, un-actionable card this work exists to remove.
  it("reads the presentation off the envelope, not the payload", () => {
    const failed = event(1, "run_failed");
    const beat = projectRunTerminalBeat([
      {
        ...failed,
        presentation: {
          title: "Run interrupted",
          status_label: "Failed",
          kind: "error",
          code: "RUN_WORKER_LOST",
          retryable: true,
        },
      },
    ]);

    expect(beat).toMatchObject({
      title: "Run interrupted",
      code: "RUN_WORKER_LOST",
      retryable: true,
    });
  });

  it("refuses to claim retryable when the runtime did not say so", () => {
    const beat = projectRunTerminalBeat([
      event(1, "run_failed", {
        presentation: { title: "Not allowed", code: "PERMISSION_DENIED" },
      }),
    ]);

    // Absent means unknown, and unknown must not become a button.
    expect(beat?.retryable).toBe(false);
  });

  it("falls back to honest copy when no presentation was attached", () => {
    const beat = projectRunTerminalBeat([event(1, "run_timed_out")]);

    expect(beat).toMatchObject({
      title: "Run timed out",
      copy: "This run ran out of time before it finished.",
      status: "timed_out",
      retryable: false,
    });
  });
});
