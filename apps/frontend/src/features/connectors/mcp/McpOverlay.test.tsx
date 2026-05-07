/**
 * PR 4.4.6 — McpOverlay tabs + install behaviour.
 *
 * Replaces PR 4.4's 5-step wizard tests. Pins:
 *   - Catalog tab renders one card per `useMcpCatalog` entry.
 *   - Install on a 1-click vendor calls installFromCatalog → authenticate.
 *   - Install on a pre-registered vendor opens the credentials form first.
 *   - Connected tab lists only `isAuthenticated` servers.
 *   - Search filters the catalog grid.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  McpCatalogEntry,
  McpCatalogResponse,
  McpServer,
} from "@enterprise-search/api-types";

import { McpOverlay } from "./McpOverlay";
import type { ConnectorState } from "../useConnectors";

// `useMcpCatalog` calls `listMcpCatalog` from `mcpApi`. Stub the module
// so we don't need a real fetch mock and the test stays isolated to the
// component's behaviour.
vi.mock("../../../api/mcpApi", () => ({
  listMcpCatalog: vi.fn(),
}));

import { listMcpCatalog } from "../../../api/mcpApi";

function makeCatalogEntry(
  overrides: Partial<McpCatalogEntry> = {},
): McpCatalogEntry {
  return {
    slug: "linear",
    display_name: "Linear",
    url: "https://mcp.linear.app/mcp",
    transport: "http",
    auth_mode: "oauth2",
    description: "Issues, projects, and cycles.",
    logo_url: null,
    brand_color: "#5E6AD2",
    scopes_summary: "Read issues, projects, cycles",
    default_scopes: ["read"],
    requires_pre_registered_client: false,
    verified: true,
    ...overrides,
  };
}

function makeServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    server_id: "seed:linear",
    name: "linear",
    display_name: "Linear",
    url: "https://mcp.linear.app/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: false,
    created_at: "2026-05-07T00:00:00Z",
    updated_at: "2026-05-07T00:00:00Z",
    ...overrides,
  };
}

function makeConnectors(
  overrides: Partial<ConnectorState> = {},
): ConnectorState {
  return {
    servers: [],
    loading: false,
    error: null,
    refresh: vi.fn().mockResolvedValue(undefined),
    addServer: vi.fn().mockResolvedValue(undefined),
    installFromCatalog: vi.fn().mockResolvedValue(makeServer()),
    removeServer: vi.fn().mockResolvedValue(undefined),
    setEnabled: vi.fn().mockResolvedValue(undefined),
    authenticate: vi.fn().mockResolvedValue(undefined),
    skipAuth: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function mockCatalog(entries: McpCatalogEntry[]): void {
  vi.mocked(listMcpCatalog).mockResolvedValue({
    entries,
  } satisfies McpCatalogResponse);
}

beforeEach(() => {
  vi.mocked(listMcpCatalog).mockReset();
});

describe("McpOverlay", () => {
  it("renders one Catalog card per entry from useMcpCatalog", async () => {
    mockCatalog([
      makeCatalogEntry({ slug: "linear", display_name: "Linear" }),
      makeCatalogEntry({ slug: "notion", display_name: "Notion" }),
    ]);
    render(<McpOverlay open onClose={vi.fn()} connectors={makeConnectors()} />);

    expect(
      await screen.findByLabelText("Linear catalog card"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Notion catalog card")).toBeInTheDocument();
  });

  it("Install button on a 1-click vendor calls installFromCatalog then authenticate", async () => {
    mockCatalog([makeCatalogEntry({ slug: "linear" })]);
    const installFromCatalog = vi
      .fn()
      .mockResolvedValue(makeServer({ server_id: "seed:linear" }));
    const authenticate = vi.fn().mockResolvedValue(undefined);
    const connectors = makeConnectors({ installFromCatalog, authenticate });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(await screen.findByLabelText("Install Linear"));

    await waitFor(() => {
      expect(installFromCatalog).toHaveBeenCalledWith("linear");
    });
    expect(authenticate).toHaveBeenCalledWith("seed:linear");
  });

  it("Install on a pre-registered vendor opens the credentials form first", async () => {
    mockCatalog([
      makeCatalogEntry({
        slug: "atlassian",
        display_name: "Atlassian",
        requires_pre_registered_client: true,
      }),
    ]);
    const installFromCatalog = vi.fn();
    const connectors = makeConnectors({ installFromCatalog });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(await screen.findByLabelText("Install Atlassian"));

    // Credentials form expanded; install was NOT called yet.
    expect(
      await screen.findByLabelText("OAuth credentials for Atlassian"),
    ).toBeInTheDocument();
    expect(installFromCatalog).not.toHaveBeenCalled();
  });

  it("submitting credentials installs with the OAuth client", async () => {
    mockCatalog([
      makeCatalogEntry({
        slug: "atlassian",
        display_name: "Atlassian",
        requires_pre_registered_client: true,
      }),
    ]);
    const installFromCatalog = vi
      .fn()
      .mockResolvedValue(makeServer({ server_id: "seed:atlassian" }));
    const authenticate = vi.fn().mockResolvedValue(undefined);
    const connectors = makeConnectors({ installFromCatalog, authenticate });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(await screen.findByLabelText("Install Atlassian"));
    fireEvent.change(screen.getByLabelText("Client ID"), {
      target: { value: "atl-client-123" },
    });
    fireEvent.change(screen.getByLabelText("Client secret"), {
      target: { value: "atl-secret" },
    });
    await userEvent.click(
      screen.getByRole("button", { name: /Install with credentials/ }),
    );

    await waitFor(() => {
      expect(installFromCatalog).toHaveBeenCalledWith("atlassian", {
        client_id: "atl-client-123",
        client_secret: "atl-secret",
        token_endpoint_auth_method: "client_secret_post",
      });
    });
    expect(authenticate).toHaveBeenCalledWith("seed:atlassian");
  });

  it("Connected tab shows only authenticated servers, hides unauthenticated", async () => {
    mockCatalog([]);
    const connectors = makeConnectors({
      servers: [
        makeServer({
          server_id: "seed:linear",
          display_name: "Linear",
          auth_state: "authenticated",
        }),
        makeServer({
          server_id: "seed:notion",
          display_name: "Notion",
          auth_state: "unauthenticated",
        }),
      ],
    });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(screen.getByRole("tab", { name: "Connected" }));

    expect(screen.getByText("Linear")).toBeInTheDocument();
    expect(screen.queryByText("Notion")).toBeNull();
  });

  it("filters the Catalog grid by search input", async () => {
    mockCatalog([
      makeCatalogEntry({ slug: "linear", display_name: "Linear" }),
      makeCatalogEntry({ slug: "sentry", display_name: "Sentry" }),
      makeCatalogEntry({ slug: "notion", display_name: "Notion" }),
    ]);

    render(<McpOverlay open onClose={vi.fn()} connectors={makeConnectors()} />);

    await screen.findByLabelText("Linear catalog card");
    fireEvent.change(screen.getByLabelText("Search catalog"), {
      target: { value: "sentry" },
    });

    await waitFor(() => {
      expect(screen.queryByLabelText("Linear catalog card")).toBeNull();
    });
    expect(screen.getByLabelText("Sentry catalog card")).toBeInTheDocument();
    expect(screen.queryByLabelText("Notion catalog card")).toBeNull();
  });
});
