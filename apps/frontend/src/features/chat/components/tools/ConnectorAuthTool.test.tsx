import { fireEvent, render, screen } from "@testing-library/react";
import type React from "react";
import { describe, expect, it, vi } from "vitest";
import { ConnectorAuthTool } from "./ConnectorAuthTool";

function renderConnectorAuth(args: Record<string, unknown>, result?: unknown) {
  const onConnect = vi.fn().mockResolvedValue(undefined);
  const onSkip = vi.fn().mockResolvedValue(undefined);
  const resume = vi.fn();
  const props = {
    args,
    argsText: "",
    result,
    status: { type: "requires-action", reason: "interrupt" },
    isError: false,
    toolCallId: "connector-1",
    toolName: "mcp_auth_required",
    resume,
    onConnect,
    onSkip,
  } as unknown as React.ComponentProps<typeof ConnectorAuthTool>;
  const utils = render(<ConnectorAuthTool {...props} />);
  return { ...utils, onConnect, onSkip, resume };
}

describe("ConnectorAuthTool", () => {
  it("renders Connect and Not now actions while pending", () => {
    renderConnectorAuth({
      server_id: "slack",
      approval_id: "approval-1",
      display_name: "Slack",
    });
    expect(
      screen.getByRole("button", { name: /^connect$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /not now/i }),
    ).toBeInTheDocument();
  });
  it("calls onConnect when Connect is clicked", () => {
    const { onConnect } = renderConnectorAuth({
      server_id: "slack",
      approval_id: "approval-9",
      display_name: "Slack",
    });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
    expect(onConnect).toHaveBeenCalledWith({
      approvalId: "approval-9",
      serverId: "slack",
    });
  });
});
