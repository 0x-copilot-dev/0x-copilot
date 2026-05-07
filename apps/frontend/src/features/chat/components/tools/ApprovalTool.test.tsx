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
  // PR 4.4.6.1 — approve/skip copy; ApprovalCard replaces ActivityCard.
  it("renders approve and skip actions while waiting", () => {
    renderApproval({
      approval_id: "approval-1",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "send_message",
    });
    expect(
      screen.getByRole("button", { name: /approve & continue/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /skip this step/i }),
    ).toBeInTheDocument();
  });
  it("dispatches resume(approved) when the approve button is clicked", () => {
    const { resume } = renderApproval({
      approval_id: "approval-7",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "send_message",
    });
    fireEvent.click(
      screen.getByRole("button", { name: /approve & continue/i }),
    );
    expect(resume).toHaveBeenCalledWith({
      decision: "approved",
      approval_id: "approval-7",
    });
  });
  it("dispatches resume(rejected) when skip is clicked", () => {
    const { resume } = renderApproval({
      approval_id: "approval-8",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "send_message",
    });
    fireEvent.click(screen.getByRole("button", { name: /skip this step/i }));
    expect(resume).toHaveBeenCalledWith({
      decision: "rejected",
      approval_id: "approval-8",
    });
  });
  it("renders the action title and vendor pill", () => {
    renderApproval({
      approval_id: "approval-9",
      approval_kind: "mcp_tool",
      display_name: "Linear",
      tool_name: "list_issues",
      read_only: true,
    });
    // "Search your Linear issues?" via mcpApprovalActionTitle
    expect(screen.getByText(/search your linear issues/i)).toBeInTheDocument();
    expect(screen.getByText(/^LINEAR$/)).toBeInTheDocument();
    expect(screen.getByText(/^READ$/)).toBeInTheDocument();
  });
  it("collapses to a one-line receipt when approved", () => {
    renderApproval(
      {
        approval_id: "approval-10",
        approval_kind: "mcp_tool",
        display_name: "Linear",
        tool_name: "list_issues",
        read_only: true,
      },
      { decision: "approved" },
    );
    expect(
      screen.queryByRole("button", { name: /approve & continue/i }),
    ).toBeNull();
    expect(screen.getByRole("note")).toHaveTextContent(/approved/i);
  });

  // PR 4.4.6.2 — structured payload reads.
  it("renders server-supplied params verbatim when provided", () => {
    renderApproval({
      approval_id: "approval-11",
      approval_kind: "mcp_tool",
      display_name: "Slack",
      tool_name: "post_message",
      read_only: false,
      vendor: "SLACK",
      category: "write",
      reason_code: "writes_out_of_workspace",
      params: [
        { label: "Channel", value: "#launch-aurora" },
        { label: "Visibility", value: "Channel members" },
      ],
    });
    // Server-supplied params win over the synthesised Risk + Access pair.
    expect(screen.getByText("Channel")).toBeInTheDocument();
    expect(screen.getByText("#launch-aurora")).toBeInTheDocument();
    expect(screen.getByText("Visibility")).toBeInTheDocument();
    expect(screen.queryByText("Risk")).toBeNull();
  });

  it("falls back to Risk + Access when server params are absent", () => {
    renderApproval({
      approval_id: "approval-12",
      approval_kind: "mcp_tool",
      display_name: "Linear",
      tool_name: "list_issues",
      read_only: true,
      risk_level: "low",
    });
    expect(screen.getByText("Risk")).toBeInTheDocument();
    expect(screen.getByText("Access")).toBeInTheDocument();
    expect(screen.getByText("Read-only")).toBeInTheDocument();
  });

  it("renders the high-risk reason when reason_code === risk_high", () => {
    renderApproval({
      approval_id: "approval-13",
      approval_kind: "mcp_tool",
      display_name: "GitHub",
      tool_name: "delete_repository",
      read_only: false,
      reason_code: "risk_high",
    });
    expect(
      screen.getByText(/writes to a high-risk connector/i),
    ).toBeInTheDocument();
  });

  it("uses server-supplied vendor + category for the pill", () => {
    renderApproval({
      approval_id: "approval-14",
      approval_kind: "mcp_tool",
      display_name: "Linear",
      tool_name: "list_issues",
      read_only: true,
      vendor: "ACME",
      category: "action",
    });
    expect(screen.getByText(/^ACME$/)).toBeInTheDocument();
    expect(screen.getByText(/^ACTION$/)).toBeInTheDocument();
  });

  // PR 4.4.6.4 — undo window UX on the resolved-approved receipt.
  it("renders an undo button when the receipt has a future undoUntil", () => {
    const future = new Date(Date.now() + 30_000).toISOString();
    renderApproval(
      {
        approval_id: "approval-15",
        approval_kind: "mcp_tool",
        display_name: "Slack",
        tool_name: "post_message",
        read_only: false,
      },
      { decision: "approved", undo_expires_at: future },
    );
    expect(screen.getByRole("button", { name: /undo/i })).toBeInTheDocument();
  });

  it("does not render an undo button when undo_expires_at is absent", () => {
    renderApproval(
      {
        approval_id: "approval-16",
        approval_kind: "mcp_tool",
        display_name: "Linear",
        tool_name: "list_issues",
        read_only: true,
      },
      { decision: "approved" },
    );
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });

  it("does not render an undo button when the window has expired", () => {
    const past = new Date(Date.now() - 5_000).toISOString();
    renderApproval(
      {
        approval_id: "approval-17",
        approval_kind: "mcp_tool",
        display_name: "Slack",
        tool_name: "post_message",
        read_only: false,
      },
      { decision: "approved", undo_expires_at: past },
    );
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });
});
