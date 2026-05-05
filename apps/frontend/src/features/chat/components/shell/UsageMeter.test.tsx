import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UsageMeter } from "./UsageMeter";

describe("UsageMeter", () => {
  it("renders a dash when pct is null", () => {
    render(<UsageMeter pct={null} onOpen={() => undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders the rounded percentage", () => {
    render(<UsageMeter pct={64.4} onOpen={() => undefined} />);
    expect(screen.getByText("64%")).toBeInTheDocument();
  });

  it("clamps below zero and above 100", () => {
    const { rerender } = render(
      <UsageMeter pct={-12} onOpen={() => undefined} />,
    );
    expect(screen.getByText("0%")).toBeInTheDocument();
    rerender(<UsageMeter pct={150} onOpen={() => undefined} />);
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("flips tone past warning + danger thresholds", () => {
    const { rerender, container } = render(
      <UsageMeter pct={10} onOpen={() => undefined} />,
    );
    expect(container.querySelector("[data-tone='ok']")).not.toBeNull();
    rerender(<UsageMeter pct={75} onOpen={() => undefined} />);
    expect(container.querySelector("[data-tone='warn']")).not.toBeNull();
    rerender(<UsageMeter pct={92} onOpen={() => undefined} />);
    expect(container.querySelector("[data-tone='danger']")).not.toBeNull();
  });

  it("calls onOpen when clicked", () => {
    const onOpen = vi.fn();
    render(<UsageMeter pct={50} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
