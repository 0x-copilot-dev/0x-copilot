// InboxPanel shell tests (P4-B1).
//
// Covers: filter chips (All / Unread / Mentions / Errors), unread badge,
// count chips, change callback, optional footer.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  InboxPanel,
  type InboxPanelCounts,
  type InboxPanelProps,
} from "./InboxPanel";

function renderPanel(props: InboxPanelProps = {}): void {
  render(<InboxPanel {...props} />);
}

describe("InboxPanel", () => {
  it("renders the four filter chips with All selected by default", () => {
    renderPanel();
    expect(screen.getByTestId("filter-tab-all")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("filter-tab-unread")).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByTestId("filter-tab-mentions")).toBeInTheDocument();
    expect(screen.getByTestId("filter-tab-errors")).toBeInTheDocument();
  });

  it("highlights the active filter when supplied", () => {
    renderPanel({ filter: "mentions" });
    expect(screen.getByTestId("filter-tab-mentions")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("calls onFilterChange with the chip slug when clicked", () => {
    const onFilterChange = vi.fn();
    renderPanel({ onFilterChange });
    fireEvent.click(screen.getByTestId("filter-tab-unread"));
    expect(onFilterChange).toHaveBeenCalledWith("unread");
    fireEvent.click(screen.getByTestId("filter-tab-errors"));
    expect(onFilterChange).toHaveBeenCalledWith("errors");
  });

  it("renders per-filter count chips when counts is supplied", () => {
    const counts: InboxPanelCounts = {
      all: 12,
      unread: 4,
      mentions: 3,
      errors: 1,
    };
    renderPanel({ counts });
    expect(screen.getByTestId("filter-tab-count-all")).toHaveTextContent("12");
    expect(screen.getByTestId("filter-tab-count-unread")).toHaveTextContent(
      "4",
    );
    expect(screen.getByTestId("filter-tab-count-mentions")).toHaveTextContent(
      "3",
    );
    expect(screen.getByTestId("filter-tab-count-errors")).toHaveTextContent(
      "1",
    );
  });

  it("renders the unread badge section only when unreadCount > 0", () => {
    const { rerender } = render(<InboxPanel unreadCount={0} />);
    expect(screen.queryByTestId("inbox-panel-section-unread-badge")).toBeNull();

    rerender(<InboxPanel unreadCount={3} />);
    expect(
      screen.getByTestId("inbox-panel-section-unread-badge"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("inbox-panel-unread-badge")).toHaveTextContent(
      /3 unread/i,
    );
  });

  it("falls back to unreadCount for the Unread chip count when counts.unread is absent", () => {
    renderPanel({ unreadCount: 9 });
    expect(screen.getByTestId("filter-tab-count-unread")).toHaveTextContent(
      "9",
    );
  });

  it("renders the optional footer slot when supplied", () => {
    renderPanel({
      footer: <a href="#">Edit inbox rules</a>,
    });
    const footer = screen.getByTestId("inbox-panel-footer");
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveTextContent(/edit inbox rules/i);
  });

  it("does NOT render the footer section when footer is omitted", () => {
    renderPanel();
    expect(screen.queryByTestId("inbox-panel-section-footer")).toBeNull();
  });
});
