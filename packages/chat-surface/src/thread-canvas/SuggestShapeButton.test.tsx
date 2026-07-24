// SuggestShapeButton (PRD-B4) — the user-invited "Suggest a shape" affordance.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SHAPE_NO_FIT_LINE, SuggestShapeButton } from "./SuggestShapeButton";

describe("SuggestShapeButton", () => {
  it("idle: renders the invite label, enabled, no no-fit line", () => {
    const onShapeRequest = vi.fn();
    render(
      <SuggestShapeButton
        surfaceId="s1"
        shapeRequest="idle"
        onShapeRequest={onShapeRequest}
      />,
    );
    const button = screen.getByTestId("tc-suggest-shape-button");
    expect(button).toHaveTextContent("Suggest a shape for this tool");
    expect(button).not.toBeDisabled();
    expect(screen.queryByTestId("tc-suggest-shape-no-fit")).toBeNull();
  });

  it("fires onShapeRequest with the surface id exactly once on click", () => {
    const onShapeRequest = vi.fn();
    render(
      <SuggestShapeButton
        surfaceId="surf_42"
        shapeRequest="idle"
        onShapeRequest={onShapeRequest}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-suggest-shape-button"));
    expect(onShapeRequest).toHaveBeenCalledTimes(1);
    expect(onShapeRequest).toHaveBeenCalledWith("surf_42");
  });

  it("requested: disabled + assembling label, no no-fit line", () => {
    render(
      <SuggestShapeButton
        surfaceId="s1"
        shapeRequest="requested"
        onShapeRequest={vi.fn()}
      />,
    );
    const button = screen.getByTestId("tc-suggest-shape-button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveTextContent("Attempting a shape");
    expect(screen.queryByTestId("tc-suggest-shape-no-fit")).toBeNull();
  });

  it("no_fit: shows the honest line (requirement-grade) + re-enables the button", () => {
    render(
      <SuggestShapeButton
        surfaceId="s1"
        shapeRequest="no_fit"
        onShapeRequest={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-suggest-shape-no-fit")).toHaveTextContent(
      SHAPE_NO_FIT_LINE,
    );
    expect(SHAPE_NO_FIT_LINE).toBe(
      "No confident fit — keeping the raw/generic view. Nothing is hidden.",
    );
    expect(screen.getByTestId("tc-suggest-shape-button")).not.toBeDisabled();
  });
});
