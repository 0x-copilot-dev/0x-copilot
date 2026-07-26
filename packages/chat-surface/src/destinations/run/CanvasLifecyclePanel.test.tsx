import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  CanvasLifecyclePanel,
  CHAT_ONLY_CANVAS_COPY,
} from "./CanvasLifecyclePanel";

describe("CanvasLifecyclePanel (PRD-B3)", () => {
  it("pins the honest chat-only copy", () => {
    render(<CanvasLifecyclePanel lifecycle="chat_only" failure={null} />);

    const panel = screen.getByTestId("canvas-lifecycle-panel");
    expect(panel).toHaveAttribute("data-lifecycle", "chat_only");
    // The empty state is a canvas state, not nested card chrome.
    expect(panel).toHaveStyle({
      background: "transparent",
      borderRadius: "0px",
      padding: "26px",
    });
    expect(panel.style.borderWidth).toBe("0px");
    expect(screen.getByText("Answered in chat")).toBeInTheDocument();
    expect(screen.getByText(CHAT_ONLY_CANVAS_COPY)).toBeInTheDocument();
  });

  it("exposes a retry only for a failed run", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <CanvasLifecyclePanel
        lifecycle="failed"
        failure="The connector timed out."
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry run" }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByText("The connector timed out.")).toBeInTheDocument();

    rerender(
      <CanvasLifecyclePanel lifecycle="complete_empty" failure={null} />,
    );
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
  });
});
