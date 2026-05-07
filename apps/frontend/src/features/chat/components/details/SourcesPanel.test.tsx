import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SourceEntry } from "@enterprise-search/api-types";
import { emptySourceMap, seedSourceMap } from "../../chatModel/sourcesReducer";
import { SourcesPanel } from "./SourcesPanel";

function source(overrides: Partial<SourceEntry> = {}): SourceEntry {
  return {
    citation_id: "c1",
    source_connector: "notion",
    source_doc_id: "page_123",
    source_url: "https://example.com/notion/page_123",
    title: "Aurora 4.0 — Approved Positioning v3",
    snippet: "Aurora 4.0 brings agentic search to every desk.",
    freshness_at: null,
    citation_count: 1,
    last_cited_at: "2026-05-05T12:00:00Z",
    ...overrides,
  };
}

describe("SourcesPanel", () => {
  it("renders the empty state when no citations are present", () => {
    render(<SourcesPanel sources={emptySourceMap()} onClose={() => {}} />);
    expect(screen.getByText(/No citations yet\./)).toBeInTheDocument();
  });

  it("orders sources by citation_count then last_cited_at desc", () => {
    const sources = seedSourceMap([
      source({
        source_doc_id: "second",
        title: "Second",
        citation_count: 2,
        last_cited_at: "2026-05-05T12:00:00Z",
      }),
      source({
        source_doc_id: "first",
        title: "First",
        citation_count: 5,
        last_cited_at: "2026-05-05T11:00:00Z",
      }),
    ]);
    render(<SourcesPanel sources={sources} onClose={() => {}} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("First");
    expect(items[1]).toHaveTextContent("Second");
  });

  it("renders the title as a link when source_url is present", () => {
    const sources = seedSourceMap([
      source({ source_url: "https://example.com/x" }),
    ]);
    render(<SourcesPanel sources={sources} onClose={() => {}} />);
    const link = screen.getByRole("link", {
      name: "Aurora 4.0 — Approved Positioning v3",
    });
    expect(link).toHaveAttribute("href", "https://example.com/x");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("falls back to plain text when source_url is missing", () => {
    const sources = seedSourceMap([
      source({ source_url: null, title: "Plain title" }),
    ]);
    render(<SourcesPanel sources={sources} onClose={() => {}} />);
    expect(screen.queryByRole("link", { name: "Plain title" })).toBeNull();
    expect(screen.getByText("Plain title")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<SourcesPanel sources={emptySourceMap()} onClose={onClose} />);
    await user.click(
      screen.getByRole("button", { name: /close sources panel/i }),
    );
    expect(onClose).toHaveBeenCalledOnce();
  });
});
