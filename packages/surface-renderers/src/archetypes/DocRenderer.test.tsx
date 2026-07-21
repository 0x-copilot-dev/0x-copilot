import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceState } from "../_shared/specTypes";
import { DOC_STATE } from "./fixtures";
import { docAdapter } from "./DocRenderer";

describe("docAdapter contract", () => {
  it("registers scheme 'doc' with first-party metadata", () => {
    expect(docAdapter.scheme).toBe("doc");
    expect(docAdapter.metadata.origin).toBe("first-party");
    expect(docAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only doc:// uris", () => {
    expect(docAdapter.matches("doc://notion/page")).toBe(true);
    expect(docAdapter.matches("board://x")).toBe(false);
  });
});

describe("docAdapter.renderCurrent", () => {
  it("renders the title and each section's heading + body", () => {
    render(docAdapter.renderCurrent(DOC_STATE));
    expect(screen.getByTestId("surface-title")).toHaveTextContent(
      "Q4 Renewal Playbook",
    );
    const first = screen.getByTestId("doc-section-0");
    expect(first).toHaveTextContent("Executive summary");
    expect(first).toHaveTextContent("Locked-price block holds to FY27.");
    const second = screen.getByTestId("doc-section-1");
    expect(second).toHaveTextContent("Risks");
    expect(second).toHaveTextContent("Two accounts flagged for churn review.");
  });

  it("renders the fallback without throwing when the spec is absent", () => {
    const state: SurfaceState = { data: { page: { title: "x" } } };
    expect(() => render(docAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-preparing-hint")).toBeInTheDocument();
  });
});

describe("docAdapter.renderDiff", () => {
  it("renders a before→after row per changed section field", () => {
    render(
      docAdapter.renderDiff({
        spec: DOC_STATE.spec,
        changes: [{ field: "heading", old: "Draft", new: "Executive summary" }],
      }),
    );
    expect(screen.getByTestId("doc-renderer")).toHaveAttribute(
      "data-mode",
      "diff",
    );
    expect(screen.getByTestId("field-heading-next")).toHaveTextContent(
      "Executive summary",
    );
  });
});
