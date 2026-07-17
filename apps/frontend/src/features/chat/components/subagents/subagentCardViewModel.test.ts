// PR 3.2.2 — adapter unit tests.

import { describe, expect, it } from "vitest";
import type { SubagentEntry } from "@0x-copilot/api-types";
import {
  subagentCardFromArgs,
  subagentCardFromEntry,
} from "./subagentCardViewModel";

function entry(overrides: Partial<SubagentEntry> = {}): SubagentEntry {
  return {
    task_id: "task_1",
    parent_run_id: "run_1",
    subagent_name: "doc_reader",
    status: "completed",
    display_title: "Doc reader",
    objective_summary: "Read positioning + GTM plan, extract claims",
    started_at: "2026-05-06T10:00:00Z",
    completed_at: "2026-05-06T10:00:18Z",
    duration_ms: 18000,
    result_summary:
      "Hero claim: time-to-answer + citation trust. Key proof points pulled into draft.",
    safe_error_code: null,
    safe_error_message: null,
    token_usage: null,
    ...overrides,
  };
}

describe("subagentCardFromEntry", () => {
  it("builds a clean view model for a completed subagent", () => {
    const vm = subagentCardFromEntry(entry());
    expect(vm).toMatchObject({
      taskId: "task_1",
      // PR 4.4.7 — name now prefers ``display_title`` when present so
      // the row carries the orchestrator's short task label
      // ("Doc reader") instead of the title-cased role.
      name: "Doc reader",
      status: "completed",
      terminal: true,
      task: "Read positioning + GTM plan, extract claims",
      finding:
        "Hero claim: time-to-answer + citation trust. Key proof points pulled into draft.",
      durationMs: 18000,
      isError: false,
    });
  });

  it("strips markdown code fences from the finding", () => {
    const vm = subagentCardFromEntry(
      entry({
        result_summary:
          "Wrote a 30-line `is_prime` checker:\n\n```python\ndef is_prime(n):\n  return n > 1\n```\nDone.",
      }),
    );
    expect(vm.finding).not.toContain("```");
    expect(vm.finding).not.toContain("def is_prime");
    expect(vm.finding).toContain("Wrote a 30-line");
  });

  it("collapses whitespace and newlines into single spaces in the task", () => {
    const vm = subagentCardFromEntry(
      entry({
        objective_summary:
          "Read   positioning\n\nextract\tclaims    from   approved sources",
        display_title: null,
      }),
    );
    expect(vm.task).toBe(
      "Read positioning extract claims from approved sources",
    );
  });

  it("truncates the task to ≤ 160 chars", () => {
    const longText = "X ".repeat(200).trim();
    const vm = subagentCardFromEntry(
      entry({ display_title: null, objective_summary: longText }),
    );
    expect(vm.task!.length).toBeLessThanOrEqual(160);
    expect(vm.task!.endsWith("...")).toBe(true);
  });

  it("truncates the finding to ≤ 280 chars and the full result to ≤ 600 chars", () => {
    const longText = "Y ".repeat(500).trim();
    const vm = subagentCardFromEntry(entry({ result_summary: longText }));
    expect(vm.finding!.length).toBeLessThanOrEqual(280);
    expect(vm.fullResult!.length).toBeLessThanOrEqual(600);
  });

  it("hides the finding for non-terminal subagents", () => {
    const vm = subagentCardFromEntry(
      entry({ status: "running", completed_at: null, duration_ms: null }),
    );
    expect(vm.terminal).toBe(false);
    expect(vm.finding).toBeNull();
    expect(vm.fullResult).toBeNull();
  });

  it("normalises status + errors", () => {
    expect(subagentCardFromEntry(entry({ status: "failed" })).status).toBe(
      "failed",
    );
    expect(subagentCardFromEntry(entry({ status: "failed" })).isError).toBe(
      true,
    );
    expect(subagentCardFromEntry(entry({ status: "cancelled" })).status).toBe(
      "cancelled",
    );
    expect(subagentCardFromEntry(entry({ status: "queued" })).status).toBe(
      "queued",
    );
  });

  it("uses display_title as the row name when present", () => {
    // PR 4.4.7 — display_title drives the row name (orchestrator's
    // short task label). Task line falls back to objective_summary
    // alone, leaving null when missing rather than echoing the name.
    const vm = subagentCardFromEntry(
      entry({
        objective_summary: null,
        display_title: "Read approved positioning",
      }),
    );
    expect(vm.name).toBe("Read approved positioning");
    expect(vm.task).toBeNull();
  });
});

describe("subagentCardFromArgs", () => {
  it("builds a view model from the run_subagent tool part args", () => {
    const vm = subagentCardFromArgs(
      {
        subagent_name: "research",
        task_id: "task_2",
        summary: "Found Aurora 4 leads on agentic search.",
        short_summary: "Read positioning + extract claims",
        status: "completed",
      },
      "complete",
      undefined,
    );
    expect(vm).toMatchObject({
      taskId: "task_2",
      name: "Research",
      status: "completed",
      terminal: true,
      task: "Read positioning + extract claims",
      finding: "Found Aurora 4 leads on agentic search.",
    });
  });

  it("treats props.isError as failed regardless of payload status", () => {
    const vm = subagentCardFromArgs(
      { subagent_name: "research", status: "completed" },
      "complete",
      true,
    );
    expect(vm.status).toBe("failed");
    expect(vm.isError).toBe(true);
  });

  it("defaults to running when status is missing", () => {
    const vm = subagentCardFromArgs(
      { subagent_name: "research" },
      "running",
      false,
    );
    expect(vm.status).toBe("running");
    expect(vm.terminal).toBe(false);
    expect(vm.finding).toBeNull();
  });

  it("returns a sane default name when subagent_name is missing", () => {
    const vm = subagentCardFromArgs({}, "running", false);
    // No subagent_name means we don't pass through `formatAgentName`; we
    // return a stable "Subagent" placeholder so the card head still renders.
    expect(vm.name).toBe("Subagent");
  });

  // PR 3.2.7 — pause overlay derived from the workspace `SubagentEntry`.
  describe("pause overlay (PR 3.2.7)", () => {
    it("projects pause_reason + pause_source_event_id from a paused entry", () => {
      const vm = subagentCardFromEntry(
        entry({
          status: "paused",
          pause_reason: "approval",
          pause_source_event_id: "evt_42",
        }),
      );
      expect(vm.status).toBe("paused");
      expect(vm.pauseReason).toBe("approval");
      expect(vm.pauseSourceEventId).toBe("evt_42");
    });

    it("clears pauseReason when the entry has terminal status (paused→completed)", () => {
      const vm = subagentCardFromEntry(
        entry({
          status: "completed",
          pause_reason: "approval",
          pause_source_event_id: "evt_42",
        }),
      );
      expect(vm.status).toBe("completed");
      expect(vm.pauseReason).toBeUndefined();
      expect(vm.pauseSourceEventId).toBeUndefined();
    });

    it("subagentCardFromArgs honours an explicit pause overlay (status + reason + source)", () => {
      const vm = subagentCardFromArgs(
        { subagent_name: "research", task_id: "t1" },
        "running",
        false,
        {
          statusOverride: "paused",
          pauseReason: "mcp_auth",
          pauseSourceEventId: "evt_99",
        },
      );
      expect(vm.status).toBe("paused");
      expect(vm.pauseReason).toBe("mcp_auth");
      expect(vm.pauseSourceEventId).toBe("evt_99");
    });

    it("subagentCardFromArgs ignores the overlay when args report a terminal status", () => {
      // Terminal wins: a completed args row never re-paints as paused.
      const vm = subagentCardFromArgs(
        { subagent_name: "research", status: "completed" },
        "complete",
        false,
        { statusOverride: "paused", pauseReason: "approval" },
      );
      expect(vm.status).toBe("completed");
      expect(vm.pauseReason).toBeUndefined();
    });

    it("subagentCardFromArgs without overlay yields no pause hints", () => {
      const vm = subagentCardFromArgs(
        { subagent_name: "research" },
        "running",
        false,
      );
      expect(vm.pauseReason).toBeUndefined();
      expect(vm.pauseSourceEventId).toBeUndefined();
    });
  });
});
