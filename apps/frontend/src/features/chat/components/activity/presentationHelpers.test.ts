import { describe, expect, it } from "vitest";
import type { RuntimeEventPresentation } from "@enterprise-search/api-types";
import {
  activityVariantForPresentation,
  presentationFromArgs,
  presentationRows,
} from "./presentationHelpers";

function presentation(
  overrides: Partial<RuntimeEventPresentation> = {},
): RuntimeEventPresentation {
  return {
    title: "Search results",
    summary: null,
    status_label: "Done",
    kind: "result",
    group_key: null,
    primary_entity: null,
    action_label: null,
    result_preview: [],
    debug_label: null,
    confidence: "high",
    ...overrides,
  };
}

describe("activityVariantForPresentation", () => {
  it("maps approval kind to approval variant", () => {
    expect(
      activityVariantForPresentation(presentation({ kind: "approval" })),
    ).toBe("approval");
  });
  it("maps auth kind to connector variant", () => {
    expect(activityVariantForPresentation(presentation({ kind: "auth" }))).toBe(
      "connector",
    );
  });
  it("maps progress kind to progress variant", () => {
    expect(
      activityVariantForPresentation(presentation({ kind: "progress" })),
    ).toBe("progress");
  });
  it("maps result kind to tool variant", () => {
    expect(
      activityVariantForPresentation(presentation({ kind: "result" })),
    ).toBe("tool");
  });
});

describe("presentationFromArgs", () => {
  it("returns null when title/status_label/kind are missing", () => {
    expect(presentationFromArgs({})).toBeNull();
    expect(presentationFromArgs({ presentation: { title: "hi" } })).toBeNull();
  });
  it("parses a complete presentation", () => {
    const result = presentationFromArgs({
      presentation: {
        title: "Repo search",
        status_label: "complete",
        kind: "tool",
        summary: "found 3 results",
        result_preview: [{ title: "row 1" }],
        confidence: "high",
      },
    });
    expect(result?.title).toBe("Repo search");
    expect(result?.status_label).toBe("complete");
    expect(result?.kind).toBe("tool");
    expect(result?.summary).toBe("found 3 results");
    expect(result?.result_preview).toHaveLength(1);
  });
});

describe("presentationRows", () => {
  it("returns an empty array for non-array input", () => {
    expect(presentationRows(null)).toEqual([]);
    expect(presentationRows("not array")).toEqual([]);
  });
  it("filters rows missing a title", () => {
    expect(presentationRows([{ subtitle: "x" }, { title: "kept" }])).toEqual([
      { title: "kept", subtitle: null, url: null, badge: null },
    ]);
  });
});
