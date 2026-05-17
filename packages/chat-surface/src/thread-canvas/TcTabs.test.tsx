import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TcTabs, type TcTab } from "./TcTabs";

const baseTabs: readonly TcTab[] = [
  { uri: "email://draft-1", title: "Renewal email" },
  { uri: "sf-opp://acme/op-1", title: "Acme — Closed Won", pinned: true },
  { uri: "sheet-row://q/2", title: "Pricing row" },
];

describe("TcTabs", () => {
  it("renders one tab per entry with the title text", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByText("Renewal email")).toBeInTheDocument();
    expect(screen.getByText("Acme — Closed Won")).toBeInTheDocument();
    expect(screen.getByText("Pricing row")).toBeInTheDocument();
  });

  it("marks the active tab with aria-current and aria-selected", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="sheet-row://q/2"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    const active = screen.getByText("Pricing row").closest('[role="tab"]');
    expect(active).not.toBeNull();
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active).toHaveAttribute("aria-selected", "true");

    const inactive = screen.getByText("Renewal email").closest('[role="tab"]');
    expect(inactive).toHaveAttribute("aria-selected", "false");
    expect(inactive).not.toHaveAttribute("aria-current");
  });

  it("calls onActivate with the tab uri on click", () => {
    const onActivate = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("Pricing row"));
    expect(onActivate).toHaveBeenCalledWith("sheet-row://q/2");
  });

  it("activates a tab on keyboard Enter/Space", () => {
    const onActivate = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={() => {}}
      />,
    );
    const target = screen.getByText("Pricing row").closest('[role="tab"]');
    if (!target) throw new Error("tab not found");
    fireEvent.keyDown(target, { key: "Enter" });
    fireEvent.keyDown(target, { key: " " });
    expect(onActivate).toHaveBeenCalledTimes(2);
    expect(onActivate).toHaveBeenLastCalledWith("sheet-row://q/2");
  });

  it("renders a close button only on non-pinned tabs", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByTestId("tc-tabs-close-email://draft-1"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("tc-tabs-close-sheet-row://q/2"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-tabs-close-sf-opp://acme/op-1"),
    ).not.toBeInTheDocument();
  });

  it("calls onClose without activating when the close button is clicked", () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={onActivate}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-tabs-close-sheet-row://q/2"));
    expect(onClose).toHaveBeenCalledWith("sheet-row://q/2");
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("scrolls horizontally when tab list overflows", () => {
    render(
      <TcTabs
        tabs={baseTabs}
        activeUri="email://draft-1"
        onActivate={() => {}}
        onClose={() => {}}
      />,
    );
    const tablist = screen.getByTestId("tc-tabs");
    expect(tablist.style.overflowX).toBe("auto");
    expect(tablist.style.flexDirection).toBe("row");
  });
});
