import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

  describe("minimum visible duration", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("keeps the indicator visible for at least one full pulse cycle after a brief flash", () => {
      const { container, rerender } = render(
        <PlanningIndicator label="Planning next step..." visible={false} />,
      );
      const indicator = () =>
        container.querySelector(".aui-planning-indicator");

      // Run becomes active for one frame (visible=true) before fast model_delta
      // flips it back. Without the floor, the indicator would be unperceivable.
      rerender(
        <PlanningIndicator label="Planning next step..." visible={true} />,
      );
      expect(indicator()?.getAttribute("data-visible")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(50);
      });
      rerender(
        <PlanningIndicator label="Planning next step..." visible={false} />,
      );
      // Still visible — only 50ms elapsed, floor is 1500ms.
      expect(indicator()?.getAttribute("data-visible")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(1440);
      });
      // Still inside the 1500ms window.
      expect(indicator()?.getAttribute("data-visible")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(20);
      });
      // Past the floor; now hidden.
      expect(indicator()?.getAttribute("data-visible")).toBe("false");
    });

    it("hides immediately when the visible window has already exceeded the floor", () => {
      const { container, rerender } = render(
        <PlanningIndicator label="Planning next step..." visible={true} />,
      );
      const indicator = () =>
        container.querySelector(".aui-planning-indicator");
      expect(indicator()?.getAttribute("data-visible")).toBe("true");

      act(() => {
        vi.advanceTimersByTime(1700);
      });
      rerender(
        <PlanningIndicator label="Planning next step..." visible={false} />,
      );
      expect(indicator()?.getAttribute("data-visible")).toBe("false");
    });
  });
});
