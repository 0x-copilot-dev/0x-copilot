import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ToolCallEntry } from "./eventProjector";
import { ToolCallCard } from "./ToolCallCard";

const detailedToolCall: ToolCallEntry = {
  args: { issue: "ENG-142" },
  createdAtMs: 0,
  id: "call-linear-1",
  result: { identifier: "ENG-142", state: "In progress" },
  sequenceNo: 1,
  runId: null,
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
    expect(within(summary).getByText("Get Linear issue")).toBeInTheDocument();
    expect(within(summary).queryByText("get_issue")).not.toBeInTheDocument();
    expect(
      within(summary).getByTestId("tc-chat-tool-call-linear-1-status"),
    ).toHaveTextContent("✓Done");
    expect(summary.parentElement).toHaveProperty("open", false);

    fireEvent.click(summary);
    expect(summary.parentElement).toHaveProperty("open", true);
    await waitFor(() =>
      expect(summary).toHaveAttribute(
        "aria-label",
        "Hide details for Get Linear issue",
      ),
    );
  });

  it("shows a failed plain-language status and safe error while collapsed", () => {
    const { container } = render(
      <ToolCallCard
        toolCall={{
          createdAtMs: 0,
          errorMessage: "The Linear server could not be reached.",
          id: "call-linear-failed",
          result: {
            error: {
              code: "connection_failed",
              safe_message: "The Linear server could not be reached.",
            },
          },
          sequenceNo: 1,
          runId: null,
          status: "error",
          title: "Load Linear tools",
          toolName: "load_mcp_server",
        }}
      />,
    );

    const details = container.querySelector("details");
    const summary = container.querySelector("summary");
    if (details === null || summary === null) {
      throw new Error("Expected a failed tool disclosure");
    }
    expect(details).toHaveProperty("open", false);
    expect(details).toHaveAttribute("data-tool-status", "error");
    expect(
      screen.getByTestId("tc-chat-tool-call-linear-failed-status"),
    ).toHaveTextContent("!Failed");
    expect(
      within(summary).getByText("The Linear server could not be reached."),
    ).toBeInTheDocument();
    expect(within(summary).queryByText("✓")).toBeNull();
  });
});

describe("ToolCallCard — parked on an approval", () => {
  const running: ToolCallEntry = {
    createdAtMs: 0,
    id: "call-ls-1",
    sequenceNo: 1,
    runId: null,
    status: "running",
    title: "Calling ls",
    toolName: "ls",
  };

  it("reads Waiting with a still glyph instead of Running with a spinner", () => {
    // The lie this fixes: a parked run has no motion, but the card asserted it.
    const { container } = render(<ToolCallCard toolCall={running} parked />);

    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();
    expect(container.querySelector(".tc-tool-card__spinner")).toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-ls-1-status"),
    ).toHaveTextContent("Waiting");
  });

  it("spins again once the approval resolves", () => {
    const { container, rerender } = render(
      <ToolCallCard toolCall={running} parked />,
    );
    expect(screen.getByTestId("tc-tool-card-waiting")).toBeInTheDocument();

    rerender(<ToolCallCard toolCall={running} />);
    expect(screen.queryByTestId("tc-tool-card-waiting")).toBeNull();
    expect(container.querySelector(".tc-tool-card__spinner")).not.toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-ls-1-status"),
    ).toHaveTextContent("Running");
  });

  it("leaves a settled call alone — only a running one can be parked", () => {
    // A completed call is history; the run being parked says nothing about it.
    render(<ToolCallCard toolCall={detailedToolCall} parked />);

    expect(screen.queryByTestId("tc-tool-card-waiting")).toBeNull();
    expect(
      screen.getByTestId("tc-chat-tool-call-linear-1-status"),
    ).toHaveTextContent("Done");
  });
});
