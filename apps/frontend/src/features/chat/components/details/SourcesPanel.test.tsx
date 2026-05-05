import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CitationSourceRef } from "@enterprise-search/api-types";
import { SourcesPanel } from "./SourcesPanel";

function citation(
  overrides: Partial<CitationSourceRef> = {},
): CitationSourceRef {
  return {
    citation_id: "c1",
    ordinal: 1,
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/notion/page_123",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    source_tool_call_id: null,
    ...overrides,
  };
}

describe("SourcesPanel", () => {
  it("renders the empty state when no citations are present", () => {
    render(<SourcesPanel citations={new Map()} onClose={() => {}} />);
    expect(screen.getByText(/No citations yet\./)).toBeInTheDocument();
  });

  it("orders sources by ordinal", () => {
    const lookup = new Map<string, CitationSourceRef>([
      ["c2", citation({ citation_id: "c2", ordinal: 2, title: "Second" })],
      ["c1", citation({ citation_id: "c1", ordinal: 1, title: "First" })],
    ]);
    render(<SourcesPanel citations={lookup} onClose={() => {}} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("First");
    expect(items[1]).toHaveTextContent("Second");
  });

  it("renders the title as a link when source_url is present", () => {
    const lookup = new Map<string, CitationSourceRef>([
      ["c1", citation({ source_url: "https://example.com/x" })],
    ]);
    render(<SourcesPanel citations={lookup} onClose={() => {}} />);
    const link = screen.getByRole("link", {
      name: "Aurora 4.0 — Approved Positioning v3",
    });
    expect(link).toHaveAttribute("href", "https://example.com/x");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("falls back to plain text when source_url is missing", () => {
    const lookup = new Map<string, CitationSourceRef>([
      ["c1", citation({ source_url: null, title: "Plain title" })],
    ]);
    render(<SourcesPanel citations={lookup} onClose={() => {}} />);
    expect(screen.queryByRole("link", { name: "Plain title" })).toBeNull();
    expect(screen.getByText("Plain title")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<SourcesPanel citations={new Map()} onClose={onClose} />);
    await user.click(
      screen.getByRole("button", { name: /close sources panel/i }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });
});
