import { fireEvent, render, screen } from "@testing-library/react";
import type React from "react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalTool } from "./ApprovalTool";

function renderApproval(args: Record<string, unknown>, result?: unknown) {
  const resume = vi.fn();
  const props = {
    args,
    argsText: "",
    result,
    status: { type: "requires-action", reason: "interrupt" },
    isError: false,
    toolCallId: "approval-1",
    toolName: "approval_request",
    resume,
  } as unknown as React.ComponentProps<typeof ApprovalTool>;
  const utils = render(<ApprovalTool {...props} />);
  return { ...utils, resume };
}

describe("ApprovalTool", () => {
  it("renders approve and reject actions while waiting", () => {
    renderApproval({
      approval_id: "approval-1",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "send_message",
    });
    expect(
      screen.getByRole("button", { name: /allow once/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });
  it("dispatches resume(approved) when the approve button is clicked", () => {
    const { resume } = renderApproval({
      approval_id: "approval-7",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "send_message",
    });
    fireEvent.click(screen.getByRole("button", { name: /allow once/i }));
    expect(resume).toHaveBeenCalledWith({
      decision: "approved",
      approval_id: "approval-7",
    });
  });
});
