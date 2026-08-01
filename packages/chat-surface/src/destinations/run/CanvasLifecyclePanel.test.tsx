import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CanvasLifecyclePanel,
  CHAT_ONLY_CANVAS_COPY,
} from "./CanvasLifecyclePanel";

describe("CanvasLifecyclePanel (PRD-B3)", () => {
  it("pins the honest chat-only copy", () => {
    render(<CanvasLifecyclePanel lifecycle="chat_only" />);

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

  // The canvas reports on the canvas. It offers no run-level action: the old
  // "Retry run" button called an SSE reconnect, so it could not retry anything,
  // and its "This run needs attention" framing contradicted a chat pane that
  // had already answered. A terminal run failure is reported in the stream.
  it("offers no action in any state", () => {
    const states = [
      "assembling",
      "chat_only",
      "parked",
      "complete_empty",
    ] as const;

    for (const lifecycle of states) {
      const { unmount } = render(
        <CanvasLifecyclePanel lifecycle={lifecycle} />,
      );
      expect(screen.queryByRole("button")).toBeNull();
      unmount();
    }
  });

  it("never announces assertively — it is not an alarm", () => {
    render(<CanvasLifecyclePanel lifecycle="complete_empty" />);

    expect(screen.getByTestId("canvas-lifecycle-panel")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(screen.getByText("Nothing to open")).toBeInTheDocument();
  });
});
