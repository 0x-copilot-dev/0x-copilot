// ToolsPopover — section rendering, toggle/connect/custom callbacks,
// loading/empty/error (PRD-P4).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import { ToolsPopover, type ToolsPopoverProps } from "./ToolsPopover";

function server(overrides: Partial<McpServer>): McpServer {
  return {
    server_id: "seed:sheets",
    name: "Google Sheets",
    display_name: "Google Sheets",
    url: "https://sheets.test/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "authenticated",
    health: "healthy",
    enabled: true,
    oauth_client_configured: true,
    scopes_summary: "read & write workbooks",
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

function makePort(
  over: Partial<FirstRunConnectorsPort> = {},
): FirstRunConnectorsPort {
  return {
    listServers: vi.fn().mockResolvedValue([]),
    listCatalog: vi.fn().mockResolvedValue([]),
    installFromCatalog: vi.fn().mockResolvedValue(server({})),
    addCustomServer: vi.fn().mockResolvedValue(server({})),
    beginAuth: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

function renderPopover(
  over: Partial<ToolsPopoverProps> = {},
): ToolsPopoverProps {
  const props: ToolsPopoverProps = {
    open: true,
    onClose: vi.fn(),
    port: makePort(),
    webSearchEnabled: true,
    onToggleWebSearch: vi.fn(),
    activeConnectorIds: [],
    onToggleConnector: vi.fn(),
    onConnectCatalog: vi.fn(),
    onAddCustom: vi.fn(),
    ...over,
  };
  render(<ToolsPopover {...props} />);
  return props;
}

describe("<ToolsPopover> — open/closed", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <ToolsPopover
        open={false}
        onClose={vi.fn()}
        port={makePort()}
        webSearchEnabled
        onToggleWebSearch={vi.fn()}
        activeConnectorIds={[]}
        onToggleConnector={vi.fn()}
        onConnectCatalog={vi.fn()}
        onAddCustom={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the web-search toggle and custom row immediately (before fetch resolves)", () => {
    renderPopover({
      port: makePort({
        listServers: vi.fn(() => new Promise<never>(() => {})),
      }),
    });
    expect(screen.getByTestId("first-run-tools-websearch")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-custom")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-loading")).toBeTruthy();
  });
});

describe("<ToolsPopover> — load states", () => {
  it("renders connected + installable sections once loaded", async () => {
    renderPopover({
      port: makePort({
        listServers: vi.fn().mockResolvedValue([server({})]),
        listCatalog: vi
          .fn()
          .mockResolvedValue([
            catalogEntry({ slug: "safe", display_name: "Safe{Wallet}" }),
            catalogEntry({ slug: "github", display_name: "GitHub" }),
          ]),
      }),
    });
    await screen.findByTestId("first-run-tools-connected");
    expect(
      screen.getByTestId("first-run-tools-connected-seed:sheets"),
    ).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-installable")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-connect-safe")).toBeTruthy();
    expect(screen.getByTestId("first-run-tools-connect-github")).toBeTruthy();
    // Group note copy is byte-verbatim vs SPEC.
    expect(
      screen.getByTestId("first-run-tools-installable-note").textContent,
    ).toBe("1-click connect · you approve first use");
  });

  it("shows the empty state when no connectors and no catalog", async () => {
    renderPopover({ port: makePort() });
    await screen.findByTestId("first-run-tools-empty");
    expect(screen.queryByTestId("first-run-tools-connected")).toBeNull();
    expect(screen.queryByTestId("first-run-tools-installable")).toBeNull();
  });

  it("shows the error state when a fetch rejects", async () => {
    renderPopover({
      port: makePort({
        listCatalog: vi.fn().mockRejectedValue(new Error("boom")),
      }),
    });
    await screen.findByTestId("first-run-tools-error");
  });
});

describe("<ToolsPopover> — header meta count", () => {
  it("counts web search + active connectors (`{n} on · none required`)", async () => {
    renderPopover({
      webSearchEnabled: true,
      activeConnectorIds: ["seed:sheets"],
      port: makePort({ listServers: vi.fn().mockResolvedValue([server({})]) }),
    });
    await screen.findByTestId("first-run-tools-connected");
    expect(screen.getByTestId("first-run-tools-meta").textContent).toBe(
      "2 on · none required",
    );
  });

  it("web search off with no active connectors reads `0 on`", () => {
    renderPopover({
      webSearchEnabled: false,
      port: makePort({
        listServers: vi.fn(() => new Promise<never>(() => {})),
      }),
    });
    expect(screen.getByTestId("first-run-tools-meta").textContent).toBe(
      "0 on · none required",
    );
  });
});

describe("<ToolsPopover> — callbacks", () => {
  it("toggles web search", () => {
    const props = renderPopover({
      webSearchEnabled: true,
      port: makePort({
        listServers: vi.fn(() => new Promise<never>(() => {})),
      }),
    });
    fireEvent.click(screen.getByTestId("first-run-tools-websearch"));
    expect(props.onToggleWebSearch).toHaveBeenCalledWith(false);
  });

  it("toggles a connected connector to active when currently paused", async () => {
    const props = renderPopover({
      activeConnectorIds: [],
      port: makePort({ listServers: vi.fn().mockResolvedValue([server({})]) }),
    });
    const row = await screen.findByTestId(
      "first-run-tools-connected-seed:sheets",
    );
    expect(row.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(row);
    expect(props.onToggleConnector).toHaveBeenCalledWith("seed:sheets", true);
  });

  it("toggles a connected connector to paused when currently active", async () => {
    const props = renderPopover({
      activeConnectorIds: ["seed:sheets"],
      port: makePort({ listServers: vi.fn().mockResolvedValue([server({})]) }),
    });
    const row = await screen.findByTestId(
      "first-run-tools-connected-seed:sheets",
    );
    expect(row.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(row);
    expect(props.onToggleConnector).toHaveBeenCalledWith("seed:sheets", false);
  });

  it("connects a 1-click catalog entry, preserving requiresPreRegisteredClient", async () => {
    const props = renderPopover({
      port: makePort({
        listCatalog: vi.fn().mockResolvedValue([
          catalogEntry({ slug: "safe", requires_pre_registered_client: false }),
          catalogEntry({
            slug: "github",
            display_name: "GitHub",
            requires_pre_registered_client: true,
          }),
        ]),
      }),
    });
    await screen.findByTestId("first-run-tools-connect-safe");

    fireEvent.click(screen.getByTestId("first-run-tools-connect-safe"));
    expect(props.onConnectCatalog).toHaveBeenLastCalledWith(
      expect.objectContaining({
        slug: "safe",
        requiresPreRegisteredClient: false,
      }),
    );

    // Pre-registered vendors show "Set up" (host routes to the config form).
    const gh = screen.getByTestId("first-run-tools-connect-github");
    expect(gh.textContent).toContain("Set up");
    fireEvent.click(gh);
    expect(props.onConnectCatalog).toHaveBeenLastCalledWith(
      expect.objectContaining({
        slug: "github",
        requiresPreRegisteredClient: true,
      }),
    );
  });

  it("fires onAddCustom and onClose", () => {
    const props = renderPopover({
      port: makePort({
        listServers: vi.fn(() => new Promise<never>(() => {})),
      }),
    });
    fireEvent.click(screen.getByTestId("first-run-tools-custom"));
    expect(props.onAddCustom).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("first-run-tools-close"));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });
});

// Punch-list row 46 — the design's dismissal semantics: a transparent
// click-out scrim behind the panel (mousedown closes) plus Escape. Both are
// RENDERED/React-local, never a `window`/`document` listener (banned here).
describe("<ToolsPopover> — click-out scrim + Escape (row 46)", () => {
  function renderWithContainer(over: Partial<ToolsPopoverProps> = {}): {
    props: ToolsPopoverProps;
    container: HTMLElement;
  } {
    const props: ToolsPopoverProps = {
      open: true,
      onClose: vi.fn(),
      port: makePort({
        listServers: vi.fn(() => new Promise<never>(() => {})),
      }),
      webSearchEnabled: true,
      onToggleWebSearch: vi.fn(),
      activeConnectorIds: [],
      onToggleConnector: vi.fn(),
      onConnectCatalog: vi.fn(),
      onAddCustom: vi.fn(),
      ...over,
    };
    const { container } = render(<ToolsPopover {...props} />);
    return { props, container };
  }

  it("renders the shared `.ui-pop` panel with a `.ui-pop-scrim` sibling", () => {
    const { container } = renderWithContainer();
    expect(container.querySelector(".ui-pop-scrim")).not.toBeNull();
    expect(
      screen
        .getByTestId("first-run-tools-popover")
        .classList.contains("ui-pop"),
    ).toBe(true);
  });

  it("closes on mousedown on the scrim", () => {
    const { props, container } = renderWithContainer();
    const scrim = container.querySelector(".ui-pop-scrim");
    expect(scrim).not.toBeNull();
    fireEvent.mouseDown(scrim as Element);
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape inside the panel", () => {
    const { props } = renderWithContainer();
    fireEvent.keyDown(screen.getByTestId("first-run-tools-popover"), {
      key: "Escape",
    });
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores other keys", () => {
    const { props } = renderWithContainer();
    fireEvent.keyDown(screen.getByTestId("first-run-tools-popover"), {
      key: "a",
    });
    expect(props.onClose).not.toHaveBeenCalled();
  });
});
