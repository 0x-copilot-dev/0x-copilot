import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmptyState } from "./EmptyState";

describe("<EmptyState>", () => {
  it("renders the title with role=status (announces to AT)", () => {
    render(<EmptyState title="Inbox zero" />);
    const root = screen.getByRole("status");
    expect(root).toBeInTheDocument();
    expect(screen.getByTestId("empty-state-title")).toHaveTextContent(
      "Inbox zero",
    );
  });

  it("renders the body when supplied and omits it when empty", () => {
    const { rerender } = render(
      <EmptyState title="Empty" body="Nothing here yet." />,
    );
    expect(screen.getByTestId("empty-state-body")).toHaveTextContent(
      "Nothing here yet.",
    );
    rerender(<EmptyState title="Empty" body="" />);
    expect(screen.queryByTestId("empty-state-body")).toBeNull();
  });

  it("fires the action onClick and respects disabled", () => {
    const onClick = vi.fn();
    const { rerender } = render(
      <EmptyState title="Empty" action={{ label: "Create", onClick }} />,
    );
    fireEvent.click(screen.getByTestId("empty-state-action"));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(
      <EmptyState
        title="Empty"
        action={{ label: "Create", onClick, disabled: true }}
      />,
    );
    const btn = screen.getByTestId("empty-state-action");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
