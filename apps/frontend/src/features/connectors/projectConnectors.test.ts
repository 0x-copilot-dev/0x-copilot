import type { McpServer } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { activeCount, projectConnectors } from "./projectConnectors";

function server(overrides: Partial<McpServer> = {}): McpServer {
  return {
    server_id: "srv_notion",
    name: "notion",
    display_name: "Notion",
    url: "https://notion.example/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-05T00:00:00Z",
    ...overrides,
  };
}

describe("projectConnectors", () => {
  it("classifies an installed + authenticated server with no override as active", () => {
    const [row] = projectConnectors([server()], {});
    expect(row).toMatchObject({
      server_id: "srv_notion",
      display_name: "Notion",
      state: "active",
      current_scopes: [],
    });
  });

  it("classifies an active server with an explicit scope array", () => {
    const [row] = projectConnectors([server()], {
      srv_notion: ["read", "write_drafts"],
    });
    expect(row.state).toBe("active");
    expect(row.current_scopes).toEqual(["read", "write_drafts"]);
  });

  it("classifies an installed + authenticated + null override as paused", () => {
    const [row] = projectConnectors([server()], { srv_notion: null });
    expect(row.state).toBe("paused");
    expect(row.current_scopes).toBeNull();
  });

  it("classifies an installed + unauthenticated server as disconnected", () => {
    const [row] = projectConnectors(
      [server({ auth_state: "unauthenticated" })],
      {},
    );
    expect(row.state).toBe("disconnected");
    expect(row.current_scopes).toBeNull();
  });

  it("classifies a workspace-disabled server as workspace_off, ignoring scope", () => {
    const [row] = projectConnectors([server({ enabled: false })], {
      srv_notion: ["read"], // server-side enforcement; UI must not say active
    });
    expect(row.state).toBe("workspace_off");
    expect(row.current_scopes).toBeNull();
  });

  it("falls back to display_name → name → url when display_name is empty", () => {
    const [row] = projectConnectors(
      [
        server({
          server_id: "s1",
          display_name: "",
          name: "raw-name",
        }),
      ],
      {},
    );
    expect(row.display_name).toBe("raw-name");

    const [row2] = projectConnectors(
      [
        server({
          server_id: "s2",
          display_name: "",
          name: "",
          url: "https://example.com/mcp",
        }),
      ],
      {},
    );
    expect(row2.display_name).toBe("https://example.com/mcp");
  });

  it("preserves server order", () => {
    const rows = projectConnectors(
      [
        server({ server_id: "a", display_name: "A" }),
        server({ server_id: "b", display_name: "B" }),
        server({ server_id: "c", display_name: "C" }),
      ],
      {},
    );
    expect(rows.map((r) => r.server_id)).toEqual(["a", "b", "c"]);
  });
});

describe("activeCount", () => {
  it("counts only active rows", () => {
    const rows = projectConnectors(
      [
        server({ server_id: "a" }),
        server({ server_id: "b" }),
        server({ server_id: "c", auth_state: "unauthenticated" }),
        server({ server_id: "d", enabled: false }),
      ],
      { b: null },
    );
    // a: active, b: paused, c: disconnected, d: workspace_off
    expect(activeCount(rows)).toBe(1);
  });
});
