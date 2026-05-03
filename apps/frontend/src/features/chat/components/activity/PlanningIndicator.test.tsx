import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlanningIndicator } from "./PlanningIndicator";

describe("PlanningIndicator", () => {
  it("exposes the label via aria-label", () => {
    render(<PlanningIndicator label="Planning the next steps" visible />);
    expect(
      screen.getByLabelText("Planning the next steps"),
    ).toBeInTheDocument();
  });
  it("sets data-visible to false when not visible", () => {
    const { container } = render(
      <PlanningIndicator label="Idle" visible={false} />,
    );
    const indicator = container.querySelector(".aui-planning-indicator");
    expect(indicator?.getAttribute("data-visible")).toBe("false");
    expect(indicator?.getAttribute("aria-hidden")).toBe("true");
  });
});
