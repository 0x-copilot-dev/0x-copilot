// MemoryPanel tests (P12-B2).
//
// Covers: kind/scope FilterTabs rendering + callbacks, tag chips, Add
// memory CTA, ARIA roles.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MemoryPanel } from "./MemoryPanel";

describe("MemoryPanel", () => {
  it("renders kind + scope tablists with All / Skills / Facts / Preferences / My / Workspace", () => {
    render(<MemoryPanel />);
    expect(
      screen.getByRole("tablist", { name: /memory kind filter/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tablist", { name: /memory scope filter/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Skills")).toBeInTheDocument();
    expect(screen.getByText("Facts")).toBeInTheDocument();
    expect(screen.getByText("Preferences")).toBeInTheDocument();
    expect(screen.getByText(/^My$/)).toBeInTheDocument();
    expect(screen.getByText(/^Workspace$/)).toBeInTheDocument();
  });

  it("fires onKindFilterChange when a kind tab is clicked", () => {
    const onKindFilterChange = vi.fn();
    render(<MemoryPanel onKindFilterChange={onKindFilterChange} />);
    // FilterTabs share slug ids but with idPrefix, so we look up by
    // tablist name and then click the tab inside.
    const kindTablist = screen.getByRole("tablist", {
      name: /memory kind filter/i,
    });
    fireEvent.click(
      kindTablist.querySelector('[data-testid="filter-tab-skill"]')!,
    );
    expect(onKindFilterChange).toHaveBeenCalledWith("skill");
  });

  it("fires onScopeFilterChange when a scope tab is clicked", () => {
    const onScopeFilterChange = vi.fn();
    render(<MemoryPanel onScopeFilterChange={onScopeFilterChange} />);
    const scopeTablist = screen.getByRole("tablist", {
      name: /memory scope filter/i,
    });
    fireEvent.click(
      scopeTablist.querySelector('[data-testid="filter-tab-workspace"]')!,
    );
    expect(onScopeFilterChange).toHaveBeenCalledWith("workspace");
  });

  it("renders tag chips with counts and fires onTagFilterChange", () => {
    const onTagFilterChange = vi.fn();
    render(
      <MemoryPanel
        tags={[
          { tag: "python", count: 4 },
          { tag: "billing", count: 2 },
        ]}
        onTagFilterChange={onTagFilterChange}
      />,
    );
    fireEvent.click(screen.getByTestId("memory-panel-tag-python"));
    expect(onTagFilterChange).toHaveBeenCalledWith("python");
    fireEvent.click(screen.getByTestId("memory-panel-tag-all"));
    expect(onTagFilterChange).toHaveBeenLastCalledWith(null);
  });

  it("does not render the tags section when tags is empty", () => {
    render(<MemoryPanel />);
    expect(
      screen.queryByTestId("memory-panel-section-tags"),
    ).not.toBeInTheDocument();
  });

  it("renders the Add memory CTA when onCreateMemory is supplied", () => {
    const onCreateMemory = vi.fn();
    render(<MemoryPanel onCreateMemory={onCreateMemory} />);
    // The ContextPanel renders its primary action as a button — we
    // identify by accessible name rather than testid.
    fireEvent.click(screen.getByRole("button", { name: /add memory/i }));
    expect(onCreateMemory).toHaveBeenCalledTimes(1);
  });
});
