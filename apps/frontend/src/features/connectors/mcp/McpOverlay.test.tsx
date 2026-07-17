/**
 * PR 4.4.6 — McpOverlay tabs + install behaviour.
 *
 * Replaces PR 4.4's 5-step wizard tests. Pins:
 *   - Catalog tab renders one card per `useMcpCatalog` entry.
 *   - Install on a 1-click vendor calls installFromCatalog → authenticate.
 *   - Install on a pre-registered vendor opens the credentials form first.
 *   - Connected tab lists every added server (including ones still in
 *     ``auth_pending``) so a manual Add custom URL doesn't disappear.
 *   - Add custom URL kicks off OAuth on the freshly-created server so
 *     the user lands connected, not stranded in ``auth_pending``.
 *   - Search filters the catalog grid.
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  McpCatalogEntry,
  McpCatalogResponse,
  McpServer,
} from "@0x-copilot/api-types";

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
    addServer: vi.fn().mockResolvedValue(makeServer()),
    installFromCatalog: vi.fn().mockResolvedValue(makeServer()),
    removeServer: vi.fn().mockResolvedValue(undefined),
    setEnabled: vi.fn().mockResolvedValue(undefined),
    setDisplayName: vi.fn().mockResolvedValue(makeServer()),
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

  it("Connected tab lists every added server, including ones still pending OAuth", async () => {
    // Regression: a manually-added URL lands in ``auth_pending`` and
    // used to be hidden from both tabs (Catalog cross-references seeds
    // only; Connected used to filter on ``isAuthenticated``). The user
    // should always be able to find a server they explicitly added.
    mockCatalog([]);
    const connectors = makeConnectors({
      servers: [
        makeServer({
          server_id: "seed:linear",
          display_name: "Linear",
          auth_state: "authenticated",
        }),
        makeServer({
          server_id: "manual:clickup",
          display_name: "ClickUp",
          auth_state: "auth_pending",
        }),
      ],
    });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    await userEvent.click(screen.getByRole("tab", { name: "Connected" }));

    expect(screen.getByText("Linear")).toBeInTheDocument();
    expect(screen.getByText("ClickUp")).toBeInTheDocument();
    // The pending row exposes "Sign in" instead of "Re-auth" so the
    // user can resume an interrupted OAuth flow.
    expect(
      screen.getByRole("button", { name: /^Sign in$/ }),
    ).toBeInTheDocument();
  });

  it("Add custom URL submits and kicks off OAuth on the new server", async () => {
    mockCatalog([]);
    const newServer = makeServer({
      server_id: "manual:clickup",
      display_name: "ClickUp",
      auth_state: "auth_pending",
    });
    const addServer = vi.fn().mockResolvedValue(newServer);
    const authenticate = vi.fn().mockResolvedValue(undefined);
    const connectors = makeConnectors({ addServer, authenticate });

    render(<McpOverlay open onClose={vi.fn()} connectors={connectors} />);

    // The "Add custom URL" card has an "Add" CTA that opens the form
    // dialog. Once the dialog is open both the card and the dialog
    // expose an "Add" button — scope queries via ``within(dialog)``.
    await userEvent.click(await screen.findByRole("button", { name: /^Add$/ }));
    const dialog = await screen.findByRole("dialog", {
      name: /Add custom MCP server/i,
    });
    fireEvent.change(within(dialog).getByLabelText("Server URL"), {
      target: { value: "https://mcp.clickup.com/mcp" },
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: /^Add$/ }),
    );

    await waitFor(() => {
      expect(addServer).toHaveBeenCalledWith("https://mcp.clickup.com/mcp");
    });
    expect(authenticate).toHaveBeenCalledWith("manual:clickup");
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

  // PR 4.4.7 Phase 2 (Slice C) — chat-driven deep-link.
  describe("installSlug deep-link", () => {
    it("scrolls the matching catalog card into view and pulses it", async () => {
      mockCatalog([
        makeCatalogEntry({ slug: "linear", display_name: "Linear" }),
        makeCatalogEntry({ slug: "notion", display_name: "Notion" }),
      ]);

      // Capture which element ``scrollIntoView`` was called on so the
      // assertion is independent of jsdom's lack of layout. The
      // setup.ts shim is a no-op; we replace it for this test only.
      const calls: HTMLElement[] = [];
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function scrollIntoViewSpy(
        this: HTMLElement,
      ) {
        calls.push(this);
      } as typeof Element.prototype.scrollIntoView;

      try {
        render(
          <McpOverlay
            open
            onClose={vi.fn()}
            connectors={makeConnectors()}
            installSlug="linear"
          />,
        );

        const card = await screen.findByLabelText("Linear catalog card");
        await waitFor(() => {
          expect(calls).toContain(card);
        });
        // Pulse class lives on the same article so CSS animation
        // fires alongside the scroll.
        expect(card.className).toMatch(/mcp-card--highlight/);
      } finally {
        Element.prototype.scrollIntoView = original;
      }
    });

    it("does not pulse other catalog cards when installSlug picks one", async () => {
      mockCatalog([
        makeCatalogEntry({ slug: "linear", display_name: "Linear" }),
        makeCatalogEntry({ slug: "notion", display_name: "Notion" }),
      ]);
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView =
        function () {} as typeof Element.prototype.scrollIntoView;
      try {
        render(
          <McpOverlay
            open
            onClose={vi.fn()}
            connectors={makeConnectors()}
            installSlug="linear"
          />,
        );

        const linear = await screen.findByLabelText("Linear catalog card");
        const notion = await screen.findByLabelText("Notion catalog card");
        expect(linear.className).toMatch(/mcp-card--highlight/);
        expect(notion.className).not.toMatch(/mcp-card--highlight/);
      } finally {
        Element.prototype.scrollIntoView = original;
      }
    });

    it("no-op when installSlug is null (regular catalog open)", async () => {
      mockCatalog([
        makeCatalogEntry({ slug: "linear", display_name: "Linear" }),
      ]);
      const calls: HTMLElement[] = [];
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function scrollIntoViewSpy(
        this: HTMLElement,
      ) {
        calls.push(this);
      } as typeof Element.prototype.scrollIntoView;
      try {
        render(
          <McpOverlay
            open
            onClose={vi.fn()}
            connectors={makeConnectors()}
            installSlug={null}
          />,
        );
        const card = await screen.findByLabelText("Linear catalog card");
        // No scroll, no highlight class.
        expect(calls).not.toContain(card);
        expect(card.className).not.toMatch(/mcp-card--highlight/);
      } finally {
        Element.prototype.scrollIntoView = original;
      }
    });
  });
});
