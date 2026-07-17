import { describe, expect, it } from "vitest";
import type { RuntimeEventPresentation } from "@0x-copilot/api-types";
import { preferredPresentation } from "./presentation";

function presentation(
  override: Partial<RuntimeEventPresentation>,
): RuntimeEventPresentation {
  return {
    title: "Calling tool",
    summary: null,
    status_label: "Running",
    kind: "progress",
    group_key: null,
    primary_entity: null,
    action_label: null,
    result_preview: [],
    debug_label: null,
    ...override,
  };
}

describe("preferredPresentation", () => {
  it("returns next when current is null", () => {
    const next = presentation({ status_label: "Running" });
    expect(preferredPresentation(null, next)).toBe(next);
  });

  it("returns current when next is null", () => {
    const current = presentation({ status_label: "Running" });
    expect(preferredPresentation(current, null)).toBe(current);
  });

  it("lets a later terminal envelope replace an earlier in-progress card", () => {
    const current = presentation({
      status_label: "Running",
      kind: "progress",
    });
    const next = presentation({
      title: "web_search result",
      status_label: "Done",
      kind: "result",
    });
    expect(preferredPresentation(current, next)).toBe(next);
  });

  it("does not regress a terminal card back to progress", () => {
    // After a card has settled (done / failed / approval / auth), a later
    // progress envelope for the same call_id must not pull it back to
    // Running. Lifecycle is monotonic.
    const current = presentation({
      title: "Done",
      status_label: "Done",
      kind: "result",
    });
    const next = presentation({
      title: "Working on step",
      status_label: "Running",
      kind: "progress",
    });
    expect(preferredPresentation(current, next)).toBe(current);
  });

  it("accepts the latest progress envelope when both are progress", () => {
    const current = presentation({
      title: "Calling web_search",
      status_label: "Running",
      kind: "progress",
    });
    const next = presentation({
      title: "Calling web_search (page 2)",
      status_label: "Running",
      kind: "progress",
    });
    expect(preferredPresentation(current, next)).toBe(next);
  });

  it("accepts a terminal envelope replacing another terminal envelope", () => {
    // LLM polish patch event ships a new terminal envelope (body fields
    // refined). Newer wins.
    const current = presentation({
      title: "Web Search",
      summary: null,
      status_label: "Done",
      kind: "result",
    });
    const next = presentation({
      title: "Web Search",
      summary: "Found 12 results matching your query.",
      status_label: "Done",
      kind: "result",
    });
    expect(preferredPresentation(current, next)).toBe(next);
  });
});
