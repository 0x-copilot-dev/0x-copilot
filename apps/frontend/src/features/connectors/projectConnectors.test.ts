import type { McpServer } from "@0x-copilot/api-types";
import { describe, expect, it } from "vitest";

import {
  activeCount,
  projectChatConnectors,
  projectConnectors,
} from "./projectConnectors";

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
    logo_url: null,
    brand_color: null,
    scopes_summary: null,
    default_scopes: [],
    admin_managed: false,
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

  // PR 3.4.1 — server-supplied default_scopes power the resume-from-paused
  // payload so a Resume-from-Paused row no longer flips the connector on
  // with `[]` (the PR 3.4 behaviour). Active rows with no explicit override
  // pick up the same defaults (mirrors runtime_connector_scopes()).
  it("uses server default_scopes as the resume target", () => {
    const [row] = projectConnectors(
      [server({ default_scopes: ["read", "write_drafts"] })],
      { srv_notion: null },
    );
    expect(row.state).toBe("paused");
    expect(row.default_scopes).toEqual(["read", "write_drafts"]);
  });

  it("active with no override falls back to server defaults", () => {
    const [row] = projectConnectors([server({ default_scopes: ["read"] })], {});
    expect(row.state).toBe("active");
    expect(row.current_scopes).toEqual(["read"]);
  });

  it("carries brand metadata onto the row", () => {
    const [row] = projectConnectors(
      [
        server({
          logo_url: "https://cdn.example/notion.svg",
          brand_color: "#000000",
          scopes_summary: "Read all pages, write to /Drafts",
          admin_managed: true,
        }),
      ],
      {},
    );
    expect(row.logo_url).toBe("https://cdn.example/notion.svg");
    expect(row.brand_color).toBe("#000000");
    expect(row.scopes_summary).toBe("Read all pages, write to /Drafts");
    expect(row.admin_managed).toBe(true);
  });

  it("normalises missing brand metadata to nulls / defaults", () => {
    const [row] = projectConnectors([server()], {});
    expect(row.logo_url).toBeNull();
    expect(row.brand_color).toBeNull();
    expect(row.scopes_summary).toBeNull();
    expect(row.admin_managed).toBe(false);
    expect(row.default_scopes).toEqual([]);
  });
});

describe("projectChatConnectors (PR 4.4.6)", () => {
  // The chat popover shows only **Connected** rows: installed AND
  // authorized. Catalog availability lives in Settings → Manage MCP
  // servers; the chat surface never renders disconnected / workspace_off
  // rows. The base ``projectConnectors`` is preserved for admin views
  // that *do* need the full four-state vocabulary.
  it("includes authenticated + enabled servers as active", () => {
    const rows = projectChatConnectors([server()], {});
    expect(rows.map((r) => r.state)).toEqual(["active"]);
  });

  it("drops unauthenticated servers from the chat popover", () => {
    const rows = projectChatConnectors(
      [server({ auth_state: "unauthenticated" })],
      {},
    );
    expect(rows).toEqual([]);
  });

  it("drops workspace-disabled servers from the chat popover", () => {
    const rows = projectChatConnectors([server({ enabled: false })], {});
    expect(rows).toEqual([]);
  });

  it("keeps paused authenticated servers visible (user's per-chat choice)", () => {
    const [row] = projectChatConnectors([server()], { srv_notion: null });
    expect(row.state).toBe("paused");
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
