import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { MAX_DISPLAY_CHARS } from "../_shared/path";
import type { SurfaceState } from "../_shared/specTypes";
import {
  LINEAR_RECORD_DATA,
  LINEAR_RECORD_DIFF,
  LINEAR_RECORD_SPEC,
  LINEAR_RECORD_STATE,
} from "./fixtures";
import { recordAdapter } from "./RecordRenderer";

describe("recordAdapter contract", () => {
  it("registers scheme 'record' with first-party metadata", () => {
    expect(recordAdapter.scheme).toBe("record");
    expect(recordAdapter.metadata.origin).toBe("first-party");
    expect(recordAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only record:// uris", () => {
    expect(recordAdapter.matches("record://linear/issue/1")).toBe(true);
    expect(recordAdapter.matches("table://x")).toBe(false);
    expect(recordAdapter.matches("")).toBe(false);
  });
});

// AC1 — golden render for linear_get_issue.
describe("recordAdapter.renderCurrent (golden: linear_get_issue)", () => {
  it("renders the title and subtitle from the spec paths", () => {
    render(recordAdapter.renderCurrent(LINEAR_RECORD_STATE));
    expect(screen.getByTestId("record-renderer")).toHaveAttribute(
      "data-spec",
      "present",
    );
    expect(screen.getByTestId("surface-title")).toHaveTextContent(
      "Fix login redirect loop",
    );
    expect(screen.getByTestId("surface-subtitle")).toHaveTextContent(
      "ENG-1421",
    );
  });

  it("renders every spec field with its resolved value", () => {
    render(recordAdapter.renderCurrent(LINEAR_RECORD_STATE));
    expect(
      screen.getByTestId("field-issue.state.name-value"),
    ).toHaveTextContent("In Progress");
    expect(
      screen.getByTestId("field-issue.assignee.displayName-value"),
    ).toHaveTextContent("Sarah Chen");
    expect(
      screen.getByTestId("field-issue.priorityLabel-value"),
    ).toHaveTextContent("High");
    // datetime is locale-formatted; assert it is non-empty and not the raw ISO.
    const updated = screen.getByTestId("field-issue.updatedAt-value");
    expect(updated.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    expect(updated.textContent).not.toContain("T10:00:00Z");
  });

  it("renders a validated http link as an anchor with href", () => {
    render(recordAdapter.renderCurrent(LINEAR_RECORD_STATE));
    const link = screen.getByTestId("surface-link");
    expect(link).toHaveAttribute(
      "href",
      "https://linear.app/acme/issue/ENG-1421",
    );
  });

  it("does not render host controls (Approve / Reject)", () => {
    render(recordAdapter.renderCurrent(LINEAR_RECORD_STATE));
    expect(
      screen.queryByRole("button", { name: /approve|reject/i }),
    ).not.toBeInTheDocument();
  });
});

// AC2 — spec-less fallback.
describe("recordAdapter.renderCurrent (spec-less fallback)", () => {
  const dataOnly: SurfaceState = { data: LINEAR_RECORD_DATA };

  it("renders the Preparing view hint and a generic field list, never blank", () => {
    expect(() => render(recordAdapter.renderCurrent(dataOnly))).not.toThrow();
    expect(screen.getByTestId("record-renderer")).toHaveAttribute(
      "data-spec",
      "absent",
    );
    expect(screen.getByTestId("surface-preparing-hint")).toHaveTextContent(
      "Preparing view…",
    );
    expect(screen.getByTestId("surface-generic-fields")).toBeInTheDocument();
  });

  it("does not throw on a bare-object state with no spec key", () => {
    expect(() =>
      render(recordAdapter.renderCurrent({ issue: { title: "hi" } } as never)),
    ).not.toThrow();
    expect(screen.getByTestId("surface-preparing-hint")).toBeInTheDocument();
  });
});

// AC3 — hostile input.
describe("recordAdapter hostile input", () => {
  it("renders 20-level-deep data resolved through a long path without throwing", () => {
    let node: Record<string, unknown> = { title: "Deep title" };
    const segments: string[] = ["title"];
    for (let i = 0; i < 20; i += 1) {
      node = { child: node };
      segments.unshift("child");
    }
    const state: SurfaceState = {
      spec: {
        spec_version: 1,
        archetype: "record",
        source: { server: "s", tool: "t" },
        title_path: segments.join("."),
      },
      data: node,
    };
    expect(() => render(recordAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-title")).toHaveTextContent("Deep title");
  });

  it("truncates a 10k-char field value at display", () => {
    const state: SurfaceState = {
      spec: {
        spec_version: 1,
        archetype: "record",
        source: { server: "s", tool: "t" },
        title_path: "title",
        fields: [{ label: "Blob", path: "blob" }],
      },
      data: { title: "T", blob: "x".repeat(10_000) },
    };
    render(recordAdapter.renderCurrent(state));
    const value = screen.getByTestId("field-blob-value");
    expect((value.textContent ?? "").length).toBeLessThanOrEqual(
      MAX_DISPLAY_CHARS + 1,
    );
  });

  it("renders a javascript: url_path as inert text, not an anchor", () => {
    const state: SurfaceState = {
      spec: {
        spec_version: 1,
        archetype: "record",
        source: { server: "s", tool: "t" },
        title_path: "title",
        link: { label: "Open", url_path: "evil" },
      },
      data: { title: "T", evil: "javascript:alert(1)" },
    };
    const { container } = render(recordAdapter.renderCurrent(state));
    expect(screen.queryByTestId("surface-link")).not.toBeInTheDocument();
    expect(screen.getByTestId("surface-link-text")).toBeInTheDocument();
    // The dangerous string is never used as an href anywhere.
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(container.querySelector("a[href]")).toBeNull();
  });
});

// AC4 — diff view.
describe("recordAdapter.renderDiff (3-field change)", () => {
  it("shows each field's old value struck-through and new value highlighted", () => {
    render(recordAdapter.renderDiff(LINEAR_RECORD_DIFF));
    expect(screen.getByTestId("record-renderer")).toHaveAttribute(
      "data-mode",
      "diff",
    );
    const rows = screen.getByTestId("record-diff-rows");
    expect(rows.children).toHaveLength(3);

    expect(
      screen.getByTestId("field-issue.state.name-previous"),
    ).toHaveTextContent("Todo");
    expect(screen.getByTestId("field-issue.state.name-next")).toHaveTextContent(
      "In Progress",
    );
    expect(
      screen.getByTestId("field-issue.priorityLabel-previous"),
    ).toHaveTextContent("Medium");
    expect(
      screen.getByTestId("field-issue.priorityLabel-next"),
    ).toHaveTextContent("High");
  });

  it("maps each change back to its spec field label", () => {
    render(recordAdapter.renderDiff(LINEAR_RECORD_DIFF));
    const stateRow = screen.getByTestId("field-issue.state.name");
    expect(stateRow).toHaveTextContent("State");
  });

  it("uses the accent line-through treatment for the previous value", () => {
    render(recordAdapter.renderDiff(LINEAR_RECORD_DIFF));
    const previous = screen.getByTestId("field-issue.state.name-previous");
    expect(previous).toHaveStyle({ textDecoration: "line-through" });
  });

  it("does not throw and shows empty state when there are no changes", () => {
    expect(() =>
      render(
        recordAdapter.renderDiff({ spec: LINEAR_RECORD_SPEC, changes: [] }),
      ),
    ).not.toThrow();
    expect(screen.getByTestId("surface-empty")).toHaveTextContent(
      "No pending changes.",
    );
  });
});
