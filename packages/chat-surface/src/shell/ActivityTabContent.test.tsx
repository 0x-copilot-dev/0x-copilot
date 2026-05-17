import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityEntry } from "../thread-canvas/eventProjector";

import { ActivityTabContent } from "./ActivityTabContent";

const NOW = Date.parse("2026-05-17T12:00:00.000Z");

function makeEntry(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    id: overrides.id ?? "evt-1",
    sequenceNo: overrides.sequenceNo ?? 0,
    kind: overrides.kind ?? "tool",
    title: overrides.title ?? "Fetch sheet",
    summary: overrides.summary,
    status: overrides.status,
    createdAt: overrides.createdAt ?? new Date(NOW - 60_000).toISOString(),
    subagentId: overrides.subagentId,
    surfaceUri: overrides.surfaceUri,
  };
}

describe("<ActivityTabContent>", () => {
  it("renders an EmptyState when there are no entries", () => {
    render(<ActivityTabContent entries={[]} now={NOW} />);
    expect(screen.getByTestId("activity-tab-content")).toHaveAttribute(
      "data-empty",
      "true",
    );
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "No activity yet",
    );
    expect(screen.queryByTestId("activity-tab-list")).not.toBeInTheDocument();
  });

  it("renders one row per entry with kind badge, title, and timestamp", () => {
    const entries: ActivityEntry[] = [
      makeEntry({
        id: "evt-a",
        sequenceNo: 1,
        kind: "tool",
        title: "Fetch sheet",
        summary: "rows=42",
      }),
      makeEntry({
        id: "evt-b",
        sequenceNo: 2,
        kind: "approval",
        title: "Surface diff requested",
      }),
    ];
    render(<ActivityTabContent entries={entries} now={NOW} />);
    expect(screen.getByTestId("activity-tab-content")).toHaveAttribute(
      "data-empty",
      "false",
    );
    expect(screen.getByTestId("activity-tab-list")).toHaveAttribute(
      "aria-label",
      "Thread activity",
    );
    const rowA = screen.getByTestId("activity-tab-row-evt-a");
    expect(rowA).toHaveAttribute("data-kind", "tool");
    expect(rowA).toHaveTextContent("Fetch sheet");
    expect(
      screen.getByTestId("activity-tab-row-summary-evt-a"),
    ).toHaveTextContent("rows=42");
    expect(screen.getByTestId("activity-tab-row-evt-b")).toHaveAttribute(
      "data-kind",
      "approval",
    );
  });

  it("orders entries newest first (reverse-chronological by sequenceNo)", () => {
    const entries: ActivityEntry[] = [
      makeEntry({ id: "first", sequenceNo: 1, title: "Oldest" }),
      makeEntry({ id: "second", sequenceNo: 2, title: "Middle" }),
      makeEntry({ id: "third", sequenceNo: 3, title: "Newest" }),
    ];
    render(<ActivityTabContent entries={entries} now={NOW} />);
    const list = screen.getByTestId("activity-tab-list");
    const titles = within(list)
      .getAllByRole("listitem")
      .map((li) => li.getAttribute("data-testid"));
    expect(titles).toEqual([
      "activity-tab-row-third",
      "activity-tab-row-second",
      "activity-tab-row-first",
    ]);
  });

  it("omits the summary line when the entry has no summary", () => {
    render(
      <ActivityTabContent
        entries={[makeEntry({ id: "evt-nosumm" })]}
        now={NOW}
      />,
    );
    expect(
      screen.queryByTestId("activity-tab-row-summary-evt-nosumm"),
    ).not.toBeInTheDocument();
  });

  it("renders the time element with a dateTime attribute carrying the ISO string", () => {
    const iso = new Date(NOW - 3_600_000).toISOString();
    render(
      <ActivityTabContent
        entries={[makeEntry({ id: "evt-time", createdAt: iso })]}
        now={NOW}
      />,
    );
    const time = screen.getByTestId("activity-tab-row-time-evt-time");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", iso);
  });
});
