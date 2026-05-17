import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RightRail } from "./RightRail";

describe("RightRail", () => {
  it("renders the Atlas conversation header and placeholder list when open", () => {
    render(<RightRail open={true} onToggle={() => {}} />);
    expect(
      screen.getByRole("complementary", { name: "Atlas conversation" }),
    ).toBeInTheDocument();
    const list = screen.getByTestId("right-rail-placeholder-list");
    expect(list.children).toHaveLength(3);
  });

  it("hides the placeholder list when closed", () => {
    render(<RightRail open={false} onToggle={() => {}} />);
    expect(
      screen.queryByTestId("right-rail-placeholder-list"),
    ).not.toBeInTheDocument();
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
});
