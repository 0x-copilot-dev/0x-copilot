import { render, screen } from "@testing-library/react";
import type React from "react";
import { describe, expect, it, vi } from "vitest";
import { McpTool } from "./McpTool";

function renderMcp(
  args: Record<string, unknown>,
  extra: Partial<React.ComponentProps<typeof McpTool>> = {},
) {
  const props = {
    toolName: "call_mcp_tool",
    args,
    argsText: "",
    result: undefined,
    status: { type: "running" },
    isError: false,
    toolCallId: "mcp-1",
    addResult: vi.fn(),
    resume: vi.fn(),
    ...extra,
  } as unknown as React.ComponentProps<typeof McpTool>;
  return render(<McpTool {...props} />);
}

describe("McpTool", () => {
  it("renders the backend-projected display_title verbatim", () => {
    // The backend projector unwraps the MCP dispatcher and supplies the
    // user-meaningful title (e.g. "Calling list_issues"). The component
    // must render it without recomputing from args — verifying the title
    // is present asserts the trust-the-projection invariant.
    renderMcp({
      server_name: "linear",
      display_name: "Linear",
      tool_name: "list_issues",
      display_title: "Calling list_issues",
    });
    expect(screen.getByText("Calling list_issues")).toBeInTheDocument();
  });

  it("renders the backend-projected summary verbatim", () => {
    renderMcp({
      server_name: "linear",
      display_name: "Linear",
      tool_name: "list_issues",
      display_title: "Calling list_issues",
      summary: "Fetching open Linear issues",
    });
    expect(screen.getByText("Fetching open Linear issues")).toBeInTheDocument();
  });

  it("does NOT render the legacy 'Action connector' fallback", () => {
    // The pre-fix derivation produced "Action connector" at
    // tool_call_started time because the dispatcher's nested args
    // hadn't streamed yet. Trusting the projection means the only
    // copy on the row is whatever the backend emitted — never the
    // local fallback.
    renderMcp(
      {
        server_name: "linear",
        // No display_name, no tool_name — what the dispatcher payload
        // looked like before deltas arrived. The backend projection
        // already fell back to "Calling call_mcp_tool" here; the row
        // must NOT show the old "Action connector" literal.
        display_title: "Calling call_mcp_tool",
      },
      { toolName: "call_mcp_tool" },
    );
    expect(screen.queryByText(/action connector/i)).not.toBeInTheDocument();
    expect(screen.getByText("Calling call_mcp_tool")).toBeInTheDocument();
  });

  it("renders a generic fallback when no display_title is provided", () => {
    // Defence-in-depth: a malformed event with no projection at all
    // still renders without crashing. The exact fallback copy is an
    // implementation detail; the test only pins that the component
    // doesn't fall back to the deleted client-side derivation.
    renderMcp({
      server_name: "linear",
    });
    expect(screen.queryByText(/action connector/i)).not.toBeInTheDocument();
  });
});
