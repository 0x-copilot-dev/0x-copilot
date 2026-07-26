import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolCallEntry } from "./eventProjector";
import { ToolCallCard } from "./ToolCallCard";

const detailedToolCall: ToolCallEntry = {
  args: { issue: "ENG-142" },
  createdAtMs: 0,
  id: "call-linear-1",
  result: { identifier: "ENG-142", state: "In progress" },
  sequenceNo: 1,
  status: "complete",
  title: "Get Linear issue",
  toolName: "get_issue",
};

describe("ToolCallCard", () => {
  it("uses the native disclosure as the compact visual tool header", async () => {
    const { container } = render(<ToolCallCard toolCall={detailedToolCall} />);

    const summary = container.querySelector("summary");
    expect(summary).not.toBeNull();
    if (summary === null) {
      throw new Error("Expected a native details summary");
    }
    expect(summary).toHaveStyle({
      display: "flex",
      gap: "9px",
      padding: "9px 11px",
    });
    expect(summary.firstElementChild).not.toBeNull();
    expect(summary.firstElementChild).toHaveStyle({ display: "contents" });
    expect(summary).toHaveAttribute(
      "aria-label",
      "Show details for Get Linear issue",
    );

    fireEvent.click(summary);
    expect(summary.parentElement).toHaveProperty("open", true);
    await waitFor(() =>
      expect(summary).toHaveAttribute(
        "aria-label",
        "Hide details for Get Linear issue",
      ),
    );
  });
});
