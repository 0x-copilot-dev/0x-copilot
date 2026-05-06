import type { McpServer } from "@enterprise-search/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConnectorsPill, activeConnectorsFromScopes } from "./ConnectorsPill";

const baseServer: McpServer = {
  server_id: "s",
  name: "slack",
  display_name: "Slack",
  url: "https://example/mcp",
  transport: "http",
  auth_mode: "oauth2",
  auth_state: "authenticated",
  health: "healthy",
  enabled: true,
  oauth_client_configured: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const make = (overrides: Partial<McpServer>): McpServer => ({
  ...baseServer,
  ...overrides,
});

describe("activeConnectorsFromScopes", () => {
  it("includes authenticated, enabled servers with no override", () => {
    const servers = [make({ server_id: "a", display_name: "Notion" })];
    expect(activeConnectorsFromScopes(servers, {})).toEqual([
      { id: "a", name: "Notion" },
    ]);
  });

  it("excludes servers paused for this chat", () => {
    const servers = [make({ server_id: "a", display_name: "Notion" })];
    expect(activeConnectorsFromScopes(servers, { a: null })).toEqual([]);
  });

  it("excludes disabled servers", () => {
    const servers = [
      make({ server_id: "a", display_name: "Notion", enabled: false }),
    ];
    expect(activeConnectorsFromScopes(servers, {})).toEqual([]);
  });

  it("excludes unauthenticated servers", () => {
    const servers = [
      make({
        server_id: "a",
        display_name: "Notion",
        auth_state: "unauthenticated",
      }),
    ];
    expect(activeConnectorsFromScopes(servers, {})).toEqual([]);
  });

  it("includes servers explicitly scoped (array override)", () => {
    const servers = [make({ server_id: "a", display_name: "Notion" })];
    expect(activeConnectorsFromScopes(servers, { a: ["read"] })).toEqual([
      { id: "a", name: "Notion" },
    ]);
  });
});

describe("ConnectorsPill", () => {
  it("renders up to 4 glyphs", () => {
    const active = ["a", "b", "c", "d", "e"].map((id) => ({
      id,
      name: id.toUpperCase(),
    }));
    render(<ConnectorsPill active={active} onOpen={() => undefined} />);
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("does not show overflow when ≤4 active", () => {
    const active = [
      { id: "a", name: "A" },
      { id: "b", name: "B" },
    ];
    render(<ConnectorsPill active={active} onOpen={() => undefined} />);
    expect(screen.queryByText(/^\+\d+$/)).toBeNull();
  });

  it("shows 'Connect a tool' CTA when none active (PR 8.0.2)", () => {
    render(<ConnectorsPill active={[]} onOpen={() => undefined} />);
    expect(screen.getByText("Connect a tool")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect a tool/i })).toHaveClass(
      "atlas-connectors-pill--empty",
    );
  });

  it("calls onOpen on click", () => {
    const onOpen = vi.fn();
    render(<ConnectorsPill active={[]} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalled();
  });

  it("reflects open state on aria-expanded", () => {
    render(<ConnectorsPill active={[]} onOpen={() => undefined} open />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });
});
