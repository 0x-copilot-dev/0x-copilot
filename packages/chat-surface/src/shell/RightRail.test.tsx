import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RightRail } from "./RightRail";

describe("RightRail", () => {
  it("renders the Atlas conversation header and a neutral empty state when open with no children", () => {
    render(<RightRail open={true} onToggle={() => {}} />);
    expect(
      screen.getByRole("complementary", { name: "Atlas conversation" }),
    ).toBeInTheDocument();
    // No more hardcoded "Placeholder message 1/2/3" — the rail shows a
    // neutral empty state until the host pipes in real content.
    expect(screen.queryByText(/Placeholder message/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("right-rail-empty")).toBeInTheDocument();
  });

  it("renders host-supplied children inside the body", () => {
    render(
      <RightRail open={true} onToggle={() => {}}>
        <div data-testid="rail-child">live thread</div>
      </RightRail>,
    );
    expect(screen.getByTestId("rail-child")).toBeInTheDocument();
    expect(screen.queryByTestId("right-rail-empty")).not.toBeInTheDocument();
  });

  it("hides the body when closed", () => {
    render(<RightRail open={false} onToggle={() => {}} />);
    expect(screen.queryByTestId("right-rail-body")).not.toBeInTheDocument();
    expect(screen.queryByTestId("right-rail-empty")).not.toBeInTheDocument();
  });

  it("renders the toggle button in both states", () => {
    const { rerender } = render(<RightRail open={true} onToggle={() => {}} />);
    expect(screen.getByTestId("right-rail-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("right-rail-toggle")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    rerender(<RightRail open={false} onToggle={() => {}} />);
    expect(screen.getByTestId("right-rail-toggle")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("calls onToggle when the toggle button is clicked", () => {
    const onToggle = vi.fn();
    render(<RightRail open={true} onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("right-rail-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("re-labels the rail when the host passes a title", () => {
    render(
      <RightRail open={true} onToggle={() => {}} title="Approvals queue" />,
    );
    expect(
      screen.getByRole("complementary", { name: "Approvals queue" }),
    ).toBeInTheDocument();
  });
});
