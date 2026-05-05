import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  // PR 3.3 — non-blocking discovery variant.
  describe("discovery variant (PR 3.3)", () => {
    it("renders Connect/Skip buttons when discovery_reason is set", () => {
      renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims about ticket progress",
      });
      // Discovery variant uses "Skip" instead of "Not now".
      expect(
        screen.getByRole("button", { name: /^skip$/i }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /not now/i }),
      ).not.toBeInTheDocument();
      // Status pill reads "Suggested" — not the blocking
      // "Waiting for permission" copy.
      expect(screen.getByText(/suggested/i)).toBeInTheDocument();
    });

    it("uses the discovery title and expected_value description", () => {
      renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims about ticket progress",
      });
      expect(screen.getByText(/connect linear\?/i)).toBeInTheDocument();
      expect(
        screen.getByText(/ground claims about ticket progress/i),
      ).toBeInTheDocument();
    });

    it("Skip records the discovery reason in the resume payload", async () => {
      const { resume, onSkip } = renderConnectorAuth({
        server_id: "linear",
        approval_id: "mcp_discovery:run_1:linear",
        display_name: "Linear",
        discovery_reason: "fetch ticket statuses",
        expected_value: "ground claims",
      });
      fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
      // ``submit("skip")`` awaits ``onSkip`` before calling ``resume``.
      await waitFor(() => {
        expect(onSkip).toHaveBeenCalledWith({
          approvalId: "mcp_discovery:run_1:linear",
          serverId: "linear",
        });
      });
      await waitFor(() => {
        expect(resume).toHaveBeenCalledWith(
          expect.objectContaining({
            approval_id: "mcp_discovery:run_1:linear",
            decision: "rejected",
            reason: "mcp_discovery_skipped",
          }),
        );
      });
    });

    it("blocking variant does not include the discovery reason on Skip", async () => {
      const { resume } = renderConnectorAuth({
        server_id: "salesforce",
        approval_id: "salesforce-1",
        display_name: "Salesforce",
      });
      fireEvent.click(screen.getByRole("button", { name: /not now/i }));
      await waitFor(() => {
        expect(resume).toHaveBeenCalledWith(
          expect.not.objectContaining({ reason: "mcp_discovery_skipped" }),
        );
      });
    });
  });
});
