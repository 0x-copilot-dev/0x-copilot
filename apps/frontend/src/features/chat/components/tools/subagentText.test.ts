import { describe, expect, it } from "vitest";
import {
  subagentCardTitle,
  subagentFallbackProgress,
  subagentInlineTitle,
  subagentStatusLabel,
  summarizeSubagentResult,
} from "./subagentText";

describe("subagentCardTitle", () => {
  it("uses display title when present", () => {
    expect(subagentCardTitle("Plan a trip", null, false)).toBe("Plan a trip");
  });
  it("falls back to task summary when no display title", () => {
    expect(subagentCardTitle(null, "investigate logs", false)).toBe(
      "investigate logs",
    );
  });
  it("returns a generic completed label when nothing is provided and completed", () => {
    expect(subagentCardTitle(null, null, true)).toBe(
      "Background task finished",
    );
  });
});

describe("subagentInlineTitle", () => {
  it("returns failure label when failed", () => {
    expect(subagentInlineTitle(false, true, false)).toBe("Subagent failed");
  });
  it("returns cancelled label when cancelled", () => {
    expect(subagentInlineTitle(false, false, true)).toBe("Subagent cancelled");
  });
  it("returns finished label when completed", () => {
    expect(subagentInlineTitle(true, false, false)).toBe("Subagent finished");
  });
  it("returns working label by default", () => {
    expect(subagentInlineTitle(false, false, false)).toBe("Subagent working");
  });
});

describe("subagentStatusLabel", () => {
  it("returns 'could not complete' when isError is true", () => {
    expect(subagentStatusLabel("running", true, 5)).toBe("could not complete");
  });
  it("returns 'done' for terminal statuses", () => {
    expect(subagentStatusLabel("succeeded", false, 1)).toBe("done");
    expect(subagentStatusLabel("complete", false, 1)).toBe("done");
  });
  it("returns 'still working' once elapsed reaches 35s", () => {
    expect(subagentStatusLabel("running", false, 40)).toBe("still working");
  });
  it("returns 'starting' for queued or started", () => {
    expect(subagentStatusLabel("queued", false, 1)).toBe("starting");
  });
});

describe("subagentFallbackProgress", () => {
  it("escalates message as elapsed time grows", () => {
    expect(subagentFallbackProgress(0)).toBe("Starting task...");
    expect(subagentFallbackProgress(6)).toBe("Gathering context...");
    expect(subagentFallbackProgress(20)).toBe("Working through the details...");
    expect(subagentFallbackProgress(40)).toBe(
      "Still working. Larger tasks can take about a minute.",
    );
  });
});

describe("summarizeSubagentResult", () => {
  it("returns undefined when summary is missing", () => {
    expect(summarizeSubagentResult(null, "task")).toBeUndefined();
  });
  it("returns undefined when summary equals task summary", () => {
    expect(summarizeSubagentResult("same", "same")).toBeUndefined();
  });
  it("truncates long summaries", () => {
    const long = "x".repeat(200);
    const result = summarizeSubagentResult(long, "task");
    expect(typeof result).toBe("string");
    expect((result ?? "").length).toBeLessThanOrEqual(140);
  });
});
