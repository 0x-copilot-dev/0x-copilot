// projectFirstRunConnectors — projection table (PRD-P4). Mirrors the web app's
// `projectChatConnectors` classification (installed + authenticated =
// connected) and the catalog seed-id cross-reference.

import { describe, expect, it } from "vitest";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import {
  firstRunActiveToolCount,
  isFirstRunConnectorActive,
  projectFirstRunConnectors,
  type FirstRunConnectedConnector,
} from "./projectFirstRunConnectors";

function server(overrides: Partial<McpServer>): McpServer {
  return {
    server_id: "srv-1",
    name: "srv",
    display_name: "Server",
    url: "https://example.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function catalogEntry(overrides: Partial<McpCatalogEntry>): McpCatalogEntry {
  return {
    slug: "safe",
    display_name: "Safe{Wallet}",
    url: "https://safe.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    description: "propose & sign transactions",
    requires_pre_registered_client: false,
    verified: true,
    ...overrides,
  };
}

describe("projectFirstRunConnectors — connected classification", () => {
  it("includes only installed + authenticated servers", () => {
    const servers = [
      server({ server_id: "a", enabled: true, auth_state: "authenticated" }),
      server({ server_id: "b", enabled: false, auth_state: "authenticated" }), // not installed
      server({ server_id: "c", enabled: true, auth_state: "unauthenticated" }), // not authed
      server({ server_id: "d", enabled: true, auth_state: "auth_skipped" }), // counts
      server({ server_id: "e", enabled: true, auth_state: "auth_unsupported" }), // counts
    ];
    const { connected } = projectFirstRunConnectors(servers, []);
    expect(connected.map((c) => c.serverId)).toEqual(["a", "d", "e"]);
  });

  it("projects display metadata with fallbacks", () => {
    const { connected } = projectFirstRunConnectors(
      [
        server({
          server_id: "seed:sheets",
          display_name: "",
          name: "Google Sheets",
          scopes_summary: "read & write workbooks",
          logo_url: "https://logo.test/s.png",
          brand_color: "#0f0",
        }),
      ],
      [],
    );
    expect(connected[0]).toEqual({
      serverId: "seed:sheets",
      displayName: "Google Sheets", // falls back to name when display_name empty
      scopesSummary: "read & write workbooks",
      logoUrl: "https://logo.test/s.png",
      brandColor: "#0f0",
      accessMode: "read",
    });
  });

  it("carries read_act through and defaults a missing access_mode to read", () => {
    const { connected } = projectFirstRunConnectors(
      [
        server({ server_id: "a", access_mode: "read_act" }),
        server({ server_id: "b" }), // field absent — older backend
      ],
      [],
    );
    expect(connected.map((c) => c.accessMode)).toEqual(["read_act", "read"]);
  });

  it("drops an `off` server — the runtime never offers it to the model", () => {
    // `list_internal_cards` skips `off` servers, so a per-run toggle here would
    // be a control that cannot grant anything. Settings → Tools owns that switch.
    const { connected } = projectFirstRunConnectors(
      [
        server({ server_id: "a", access_mode: "off" }),
        server({ server_id: "b", access_mode: "read" }),
      ],
      [],
    );
    expect(connected.map((c) => c.serverId)).toEqual(["b"]);
  });

  it("keeps an `off` seed out of the installable list too", () => {
    // It IS installed; re-offering "Connect" would install what already exists.
    const { connected, installable } = projectFirstRunConnectors(
      [server({ server_id: "seed:safe", access_mode: "off" })],
      [catalogEntry({ slug: "safe" })],
    );
    expect(connected).toHaveLength(0);
    expect(installable).toHaveLength(0);
  });
});

describe("projectFirstRunConnectors — installable cross-reference", () => {
  it("drops catalog entries already connected via seed id", () => {
    const servers = [
      server({
        server_id: "seed:safe",
        enabled: true,
        auth_state: "authenticated",
      }),
    ];
    const catalog = [
      catalogEntry({ slug: "safe" }),
      catalogEntry({ slug: "sheets", display_name: "Google Sheets" }),
      catalogEntry({ slug: "github", display_name: "GitHub" }),
    ];
    const { installable } = projectFirstRunConnectors(servers, catalog);
    expect(installable.map((e) => e.slug)).toEqual(["sheets", "github"]);
  });

  it("keeps a catalog entry when the matching server is installed but not authed", () => {
    const servers = [
      server({
        server_id: "seed:safe",
        enabled: true,
        auth_state: "unauthenticated",
      }),
    ];
    const { connected, installable } = projectFirstRunConnectors(servers, [
      catalogEntry({ slug: "safe" }),
    ]);
    expect(connected).toHaveLength(0);
    expect(installable.map((e) => e.slug)).toEqual(["safe"]);
  });

  it("carries requiresPreRegisteredClient through", () => {
    const { installable } = projectFirstRunConnectors(
      [],
      [catalogEntry({ slug: "github", requires_pre_registered_client: true })],
    );
    expect(installable[0].requiresPreRegisteredClient).toBe(true);
  });
});

function connectedRow(serverId: string): FirstRunConnectedConnector {
  return {
    serverId,
    displayName: serverId.toUpperCase(),
    scopesSummary: null,
    logoUrl: null,
    brandColor: null,
    accessMode: "read",
  };
}

describe("isFirstRunConnectorActive", () => {
  it("is active by default — connected means the runtime can call it", () => {
    expect(isFirstRunConnectorActive(connectedRow("a"), [])).toBe(true);
  });

  it("is inactive only when explicitly paused for this run", () => {
    expect(isFirstRunConnectorActive(connectedRow("a"), ["a"])).toBe(false);
    expect(isFirstRunConnectorActive(connectedRow("a"), ["b"])).toBe(true);
  });
});

describe("firstRunActiveToolCount", () => {
  const connected = [connectedRow("a"), connectedRow("b")];

  it("counts every connected connector when nothing is paused", () => {
    // The regression this pins: an empty selection used to mean "1 on" (web
    // search alone) while the panel listed two connected connectors.
    expect(firstRunActiveToolCount(true, connected, [])).toBe(3);
    expect(firstRunActiveToolCount(false, connected, [])).toBe(2);
  });

  it("subtracts paused connectors", () => {
    expect(firstRunActiveToolCount(true, connected, ["a"])).toBe(2);
    expect(firstRunActiveToolCount(true, connected, ["a", "b"])).toBe(1);
    expect(firstRunActiveToolCount(false, connected, ["a", "b"])).toBe(0);
  });

  it("ignores paused ids not present in the connected set", () => {
    expect(firstRunActiveToolCount(false, connected, ["ghost"])).toBe(2);
  });
});
