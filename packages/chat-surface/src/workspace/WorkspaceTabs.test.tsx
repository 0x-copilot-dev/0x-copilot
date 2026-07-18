// PR 3.2 — WorkspaceTabs ARIA + keyboard contract.
// PR-1.7 — moved down with the component; the same assertions run from
// chat-surface.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceTabs, type WorkspaceTabsItem } from "./WorkspaceTabs";

type TabId = "sources" | "agents" | "draft";

const ITEMS: readonly WorkspaceTabsItem<TabId>[] = [
  { id: "sources", label: "Sources" },
  { id: "agents", label: "Agents", badge: "2 live" },
  { id: "draft", label: "Draft", disabled: true },
];

function renderTabs(active: TabId, onSelect = vi.fn()) {
  render(
    <WorkspaceTabs
      items={ITEMS}
      active={active}
      onSelect={onSelect}
      ariaLabel="Test tabs"
    />,
  );
  return { onSelect };
}

describe("WorkspaceTabs", () => {
  it("renders one tab per item with the expected ARIA shape", () => {
    renderTabs("sources");
    const tablist = screen.getByRole("tablist", { name: "Test tabs" });
    expect(tablist).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("tabindex", "0");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
    expect(tabs[2]).toHaveAttribute("disabled");
  });

  it("renders badges inline with the label", () => {
    renderTabs("sources");
    expect(screen.getByText("2 live")).toBeInTheDocument();
  });

  it("ArrowRight cycles past disabled tabs", () => {
    const { onSelect } = renderTabs("sources");
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith("agents");
    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    // next focusable is sources (draft is disabled)
    expect(onSelect).toHaveBeenLastCalledWith("sources");
  });

  it("ArrowLeft wraps to the last enabled tab", () => {
    const { onSelect } = renderTabs("sources");
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenLastCalledWith("agents");
  });

  it("Home / End jump to first / last enabled tab", () => {
    const { onSelect } = renderTabs("agents");
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[1], { key: "Home" });
    expect(onSelect).toHaveBeenLastCalledWith("sources");
    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith("agents");
  });

  it("click selects", () => {
    const { onSelect } = renderTabs("sources");
    fireEvent.click(screen.getByRole("tab", { name: /Agents/ }));
    expect(onSelect).toHaveBeenCalledWith("agents");
  });
});
