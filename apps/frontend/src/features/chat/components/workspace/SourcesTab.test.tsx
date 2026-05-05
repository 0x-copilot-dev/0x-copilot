import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SourceEntry } from "@enterprise-search/api-types";
import { emptySourceMap, seedSourceMap } from "../../chatModel/sourcesReducer";
import { SourcesTab } from "./SourcesTab";

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

describe("SourcesTab", () => {
  it("shows the empty hint when no sources have been ingested", () => {
    render(<SourcesTab sources={emptySourceMap()} />);
    expect(
      screen.getByText(/Sources will appear here as Atlas finds them\./),
    ).toBeInTheDocument();
  });

  it("shows a loading hint when archive is still fetching", () => {
    render(<SourcesTab sources={emptySourceMap()} loading />);
    expect(screen.getByText(/Loading sources/)).toBeInTheDocument();
  });

  it("surfaces an alert when the archive read failed and there are no sources", () => {
    render(<SourcesTab sources={emptySourceMap()} error="500 boom" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/500 boom/);
  });

  it("renders one row per unique source ordered by citation count", () => {
    const sources = seedSourceMap([
      source({
        source_doc_id: "second",
        title: "Second",
        citation_count: 2,
      }),
      source({
        source_doc_id: "first",
        title: "First",
        citation_count: 5,
      }),
    ]);
    render(<SourcesTab sources={sources} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("First");
    expect(items[1]).toHaveTextContent("Second");
  });

  it("invokes onSelect with the source on row click", async () => {
    const onSelect = vi.fn();
    const sources = seedSourceMap([source()]);
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<SourcesTab sources={sources} onSelect={onSelect} />);
    await user.click(
      screen.getByRole("button", {
        name: /Open citation 1 — Aurora 4\.0 — Approved Positioning v3/,
      }),
    );
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0]).toMatchObject({
      source_doc_id: "page_123",
    });
  });

  it("renders a stale banner when both archive seed and live data are present and the archive errored", () => {
    const sources = seedSourceMap([source()]);
    render(<SourcesTab sources={sources} error="reload me" />);
    expect(screen.getByTestId("workspace-sources-tab-stale")).toHaveTextContent(
      /reload me/,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});
