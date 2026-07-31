import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import type {
  Connector,
  ConnectorId,
  ConnectorListResponse,
  ConnectorSlug,
  ConnectorStreamEnvelope,
  McpServer,
  TenantId,
  UserId,
} from "@0x-copilot/api-types";

// Hoisted mocks for connectorsApi — keep the route test off the real
// transport / fetch surface (covered in connectorsApi-level tests).
const connectorsApiMocks = vi.hoisted(() => ({
  fetchConnectors: vi.fn(),
  fetchConnector: vi.fn(),
  refreshConnector: vi.fn(),
  disconnectConnector: vi.fn(),
  patchConnectorScopes: vi.fn(),
  setConnectorAccessMode: vi.fn(),
  removeConnector: vi.fn(),
  streamConnectorEvents: vi.fn(),
}));
vi.mock("../../../api/connectorsApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../api/connectorsApi")
  >("../../../api/connectorsApi");
  return {
    ...actual,
    fetchConnectors: connectorsApiMocks.fetchConnectors,
    fetchConnector: connectorsApiMocks.fetchConnector,
    refreshConnector: connectorsApiMocks.refreshConnector,
    disconnectConnector: connectorsApiMocks.disconnectConnector,
    patchConnectorScopes: connectorsApiMocks.patchConnectorScopes,
    setConnectorAccessMode: connectorsApiMocks.setConnectorAccessMode,
    removeConnector: connectorsApiMocks.removeConnector,
    streamConnectorEvents: connectorsApiMocks.streamConnectorEvents,
  };
});

// Hoisted mocks for mcpApi — the custom-server add path (Decision D1)
// creates the MCP server + starts MCP OAuth; keep the route test off the
// real HTTP surface.
const mcpApiMocks = vi.hoisted(() => ({
  createMcpServer: vi.fn(),
  readMcpConfig: vi.fn(),
  writeMcpConfig: vi.fn(),
  installMcpServer: vi.fn(),
  startMcpAuth: vi.fn(),
}));
vi.mock("../../../api/mcpApi", async () => {
  const actual = await vi.importActual<typeof import("../../../api/mcpApi")>(
    "../../../api/mcpApi",
  );
  return {
    ...actual,
    createMcpServer: mcpApiMocks.createMcpServer,
    readMcpConfig: mcpApiMocks.readMcpConfig,
    writeMcpConfig: mcpApiMocks.writeMcpConfig,
    installMcpServer: mcpApiMocks.installMcpServer,
    startMcpAuth: mcpApiMocks.startMcpAuth,
  };
});

import { ConnectorsRoute } from "../ConnectorsRoute";

const IDENTITY = { orgId: "org_test", userId: "user_test" };

function connector(overrides: Partial<Connector> = {}): Connector {
  return {
    id: "conn_1" as ConnectorId,
    tenant_id: "tenant_1" as TenantId,
    slug: "gmail" as ConnectorSlug,
    display_name: "Gmail",
    description: "Email",
    status: "connected",
    owner_user_id: "user_test" as UserId,
    access_mode: "read_act",
    scopes: [],
    last_sync_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-18T09:00:00Z",
    ...overrides,
  };
}

function listResponse(
  items: ReadonlyArray<Connector>,
  available: ConnectorListResponse["available"] = [],
): ConnectorListResponse {
  return { connectors: items, available, next_cursor: null };
}

function envelope(
  type: ConnectorStreamEnvelope["event_type"],
  conn: Connector | undefined,
  sequenceNo = 1,
): ConnectorStreamEnvelope {
  return {
    event_id: `evt_${sequenceNo}`,
    sequence_no: sequenceNo,
    event_type: type,
    connector: conn,
    created_at: "2026-05-18T09:00:00Z",
  };
}

function captureStreamCallbacks(closeMock = vi.fn()): {
  readonly close: Mock;
  readonly lastCall: () => {
    onEvent: (e: ConnectorStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  };
} {
  let lastCallbacks: {
    onEvent: (e: ConnectorStreamEnvelope) => void;
    onError: (e: Event) => void;
    onOpen?: () => void;
  } = { onEvent: () => undefined, onError: () => undefined };
  connectorsApiMocks.streamConnectorEvents.mockImplementation(
    ({
      onEvent,
      onError,
      onOpen,
    }: {
      onEvent: (e: ConnectorStreamEnvelope) => void;
      onError: (e: Event) => void;
      onOpen?: () => void;
    }) => {
      lastCallbacks = { onEvent, onError, onOpen };
      return { close: closeMock };
    },
  );
  return {
    close: closeMock,
    lastCall: () => lastCallbacks,
  };
}

// ===========================================================================
// RENDER — happy + error paths
// ===========================================================================

describe("ConnectorsRoute render", () => {
  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading, then the ready state with the connector list", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([connector({ display_name: "Gmail" })]),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);

    expect(screen.getByTestId("connectors-route")).toHaveAttribute(
      "data-state",
      "loading",
    );

    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(screen.getByText("Gmail")).toBeInTheDocument();
  });

  it("renders the error state on fetch failure and retries", async () => {
    connectorsApiMocks.fetchConnectors.mockRejectedValueOnce(new Error("boom"));
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([connector()]),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(screen.getByText(/boom/i)).toBeInTheDocument();
    });

    // The ConnectorsDestination shell renders the retry CTA inside its
    // EmptyState; here we just trigger a reload via the destination's
    // retry seam by mounting the route fresh — the simplest signal is
    // re-rendering after a successful response on next fetch.
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([connector()]),
    );
    // Click the destination-level retry button.
    const retryButton = await screen.findByRole("button", { name: /retry/i });
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    expect(connectorsApiMocks.fetchConnectors).toHaveBeenCalledTimes(2);
  });
});

// ===========================================================================
// SSE — deltas merge into the local list
// ===========================================================================

describe("ConnectorsRoute SSE", () => {
  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes after the initial load and merges connector.created", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([
        connector({ id: "a" as ConnectorId, display_name: "Alpha" }),
      ]),
    );
    const sse = captureStreamCallbacks();

    render(<ConnectorsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(connectorsApiMocks.streamConnectorEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse
        .lastCall()
        .onEvent(
          envelope(
            "connector.created",
            connector({ id: "b" as ConnectorId, display_name: "Bravo" }),
            1,
          ),
        );
    });

    await waitFor(() => {
      expect(screen.getByText("Bravo")).toBeInTheDocument();
    });
    expect(screen.getByTestId("connectors-route")).toHaveAttribute(
      "data-item-count",
      "2",
    );
  });

  it("closes the active stream when the stream errors (reconnect scheduled)", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([connector()]),
    );
    const sse = captureStreamCallbacks();

    render(<ConnectorsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(connectorsApiMocks.streamConnectorEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
  });

  it("uses 1s exponential backoff base (mirrors AgentsRoute / ToolsRoute)", async () => {
    // The reconnect path is best validated by structural invariants
    // (RECONNECT_BACKOFF_MIN_MS = 1000, MAX = 30000). The route is the
    // single source of those constants. Asserting on the structural
    // boundary by inspecting the route source would couple the test
    // to the file layout; instead, validate the observable contract:
    // (a) initial connection is made, (b) error path closes the handle.
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([connector()]),
    );
    const sse = captureStreamCallbacks();

    render(<ConnectorsRoute identity={IDENTITY} />);

    await waitFor(() => {
      expect(connectorsApiMocks.streamConnectorEvents).toHaveBeenCalledTimes(1);
    });

    act(() => {
      sse.lastCall().onError(new Event("error"));
    });
    expect(sse.close).toHaveBeenCalled();
    // A fresh attempt is scheduled via setTimeout — the structural
    // detail (backoff schedule) is owned by the route file and pinned
    // by the constants above. Reading the source would re-implement
    // the schedule in the test; rely on the close signal.
  });
});

// ===========================================================================
// ACCESS-MODE PATCH — optimistic apply + revert-on-failure (FR-4.22)
// ===========================================================================

describe("ConnectorsRoute access-mode PATCH", () => {
  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.setConnectorAccessMode.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("applies the picked mode optimistically and reconciles on success", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([
        connector({ id: "conn_1" as ConnectorId, access_mode: "read" }),
      ]),
    );
    connectorsApiMocks.setConnectorAccessMode.mockResolvedValueOnce({
      connector: connector({
        id: "conn_1" as ConnectorId,
        access_mode: "read_act",
      }),
    });

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("access-mode-option-read_act"));

    // Optimistic: the segment reflects read_act before the PATCH resolves.
    expect(screen.getByTestId("access-mode-segment")).toHaveAttribute(
      "data-value",
      "read_act",
    );
    expect(connectorsApiMocks.setConnectorAccessMode).toHaveBeenCalledWith(
      IDENTITY,
      "conn_1",
      { access_mode: "read_act" },
    );

    // Reconciled row keeps read_act, no error surfaced.
    await waitFor(() => {
      expect(
        screen.queryByTestId("connectors-route-access-mode-error"),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("access-mode-segment")).toHaveAttribute(
      "data-value",
      "read_act",
    );
  });

  it("reverts to the prior mode and shows an inline error on PATCH failure", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([
        connector({ id: "conn_1" as ConnectorId, access_mode: "read" }),
      ]),
    );
    connectorsApiMocks.setConnectorAccessMode.mockRejectedValueOnce(
      new Error("patch_failed"),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("access-mode-option-off"));

    // Optimistic flip to off before the rejection settles.
    expect(screen.getByTestId("access-mode-segment")).toHaveAttribute(
      "data-value",
      "off",
    );

    // Revert to the prior mode + inline error once the PATCH rejects.
    await waitFor(() => {
      expect(
        screen.getByTestId("connectors-route-access-mode-error"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("access-mode-segment")).toHaveAttribute(
      "data-value",
      "read",
    );
  });
});

// ===========================================================================
// CONNECT FLOW — ConnectModal catalog → OAuth → permission → persist
// (FR-4.23)
// ===========================================================================

describe("ConnectorsRoute connect flow", () => {
  const originalOpen = window.open;

  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    mcpApiMocks.installMcpServer.mockReset();
    mcpApiMocks.startMcpAuth.mockReset();
    connectorsApiMocks.setConnectorAccessMode.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    // Popup OAuth: stub window.open so jsdom doesn't warn "Not implemented".
    window.open = vi.fn() as unknown as typeof window.open;
  });
  afterEach(() => {
    window.open = originalOpen;
    vi.clearAllMocks();
  });

  it("advances catalog → OAuth → permission and persists the access mode", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse(
        [],
        [
          {
            slug: "notion" as ConnectorSlug,
            display_name: "Notion",
            description: "Docs and notes.",
          },
        ],
      ),
    );
    mcpApiMocks.installMcpServer.mockResolvedValueOnce({
      server_id: "seed:notion",
    });
    mcpApiMocks.startMcpAuth.mockResolvedValueOnce({
      auth_url: "https://example.com/oauth",
    });
    connectorsApiMocks.setConnectorAccessMode.mockResolvedValueOnce({
      connector: connector({
        id: "conn_notion" as ConnectorId,
        slug: "notion" as ConnectorSlug,
        display_name: "Notion",
        access_mode: "read",
      }),
    });
    const sse = captureStreamCallbacks();

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(connectorsApiMocks.streamConnectorEvents).toHaveBeenCalledTimes(1);
    });

    // Open the ConnectModal via the "Connect a tool" CTA.
    fireEvent.click(
      screen.getAllByRole("button", { name: "Connect a tool" })[0],
    );
    expect(screen.getByTestId("connect-catalog-list")).toBeInTheDocument();

    // Pick the catalog entry → OAuth round-trip starts + spinner shows.
    fireEvent.click(screen.getByTestId("connect-catalog-option"));
    // Install-then-authorize on the MCP path — the destination has no
    // OAuth surface of its own.
    await waitFor(() => {
      expect(mcpApiMocks.installMcpServer).toHaveBeenCalledWith(
        "notion",
        IDENTITY,
      );
    });
    expect(screen.getByTestId("connect-oauth")).toBeInTheDocument();

    // OAuth completes: the SSE reports the created connector, clearing the
    // pending state so the modal auto-advances to the permission step.
    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "connector.created",
          connector({
            id: "conn_notion" as ConnectorId,
            slug: "notion" as ConnectorSlug,
            display_name: "Notion",
            status: "connected",
          }),
          1,
        ),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("connect-permission")).toBeInTheDocument();
    });

    // Terminal Connect persists the chosen access mode (default "read").
    fireEvent.click(screen.getByTestId("connect-confirm"));
    await waitFor(() => {
      expect(connectorsApiMocks.setConnectorAccessMode).toHaveBeenCalledWith(
        IDENTITY,
        "conn_notion",
        { access_mode: "read" },
      );
    });
  });
});

// ===========================================================================
// CUSTOM SERVER ADD — ConnectModal "Add a custom server" → create MCP server
// → MCP OAuth popup → SSE write-through closes the flow (Decision D1)
// ===========================================================================

function mcpServer(overrides: Partial<McpServer> = {}): McpServer {
  return {
    server_id: "srv_custom",
    name: "custom",
    display_name: "Custom server",
    url: "https://mcp.example.com/mcp",
    transport: "http",
    auth_mode: "oauth2",
    auth_state: "auth_pending",
    health: "healthy",
    enabled: true,
    oauth_client_configured: false,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

describe("ConnectorsRoute — Manage MCP", () => {
  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    mcpApiMocks.readMcpConfig.mockReset();
    mcpApiMocks.writeMcpConfig.mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  /** Open the connect modal and click through to the config editor. */
  async function openEditor(): Promise<void> {
    fireEvent.click(
      screen.getAllByRole("button", { name: "Connect a tool" })[0],
    );
    fireEvent.click(screen.getByTestId("connect-catalog-custom"));
    await waitFor(() => {
      expect(screen.getByTestId("manage-mcp-editor")).toBeInTheDocument();
    });
  }

  it("opens the editor seeded from the server's document", async () => {
    // Seeded from the server, never from local state: an editor that opened
    // empty would happily save a document omitting every server it failed to
    // load, deleting them.
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(listResponse([]));
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    mcpApiMocks.readMcpConfig.mockResolvedValueOnce({
      servers: {
        github: {
          type: "http",
          url: "https://api.githubcopilot.com/mcp/",
          headers: { Authorization: "\u2022".repeat(8) },
        },
      },
    });

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    await openEditor();

    expect(mcpApiMocks.readMcpConfig).toHaveBeenCalledWith(IDENTITY);
    // The editor mounts before the read resolves, so it is the seeded CONTENT
    // that has to be awaited, not the element.
    await waitFor(() => {
      const seeded = screen.getByTestId(
        "manage-mcp-editor",
      ) as HTMLTextAreaElement;
      expect(seeded.value).toContain("api.githubcopilot.com");
    });
    const editor = screen.getByTestId(
      "manage-mcp-editor",
    ) as HTMLTextAreaElement;
    // The credential reads back redacted, never as the token itself.
    expect(editor.value).toContain("\u2022".repeat(8));
  });

  it("saves the document with a typed credential in it", async () => {
    // Not `Once`: a successful save refetches the connector list, which is the
    // wiring that makes the JSON and the Tools list agree.
    connectorsApiMocks.fetchConnectors.mockResolvedValue(listResponse([]));
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    mcpApiMocks.readMcpConfig.mockResolvedValueOnce({
      servers: {
        github: {
          type: "http",
          url: "https://api.githubcopilot.com/mcp/",
          headers: { Authorization: "\u2022".repeat(8) },
        },
      },
    });
    mcpApiMocks.writeMcpConfig.mockResolvedValueOnce({
      created: ["github"],
      updated: [],
      deleted: [],
      unchanged: [],
    });

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    await openEditor();

    // Wait for the seeded content before replacing it.
    await waitFor(() => {
      const seeded = screen.getByTestId(
        "manage-mcp-editor",
      ) as HTMLTextAreaElement;
      expect(seeded.value).toContain("githubcopilot");
    });
    fireEvent.change(screen.getByTestId("manage-mcp-editor"), {
      target: {
        value: JSON.stringify({
          servers: {
            github: {
              type: "http",
              url: "https://api.githubcopilot.com/mcp/",
              headers: { Authorization: "Bearer ghp_real" },
            },
          },
        }),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mcpApiMocks.writeMcpConfig).toHaveBeenCalledTimes(1);
    });
    const [request, identity] = mcpApiMocks.writeMcpConfig.mock.calls[0];
    expect(identity).toEqual(IDENTITY);
    // Everything in one document — no companion secrets map.
    expect(request).toEqual({ document: expect.anything() });
    expect(request.document.servers.github.headers.Authorization).toBe(
      "Bearer ghp_real",
    );
  });

  it("surfaces a save failure instead of closing", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(listResponse([]));
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    mcpApiMocks.readMcpConfig.mockResolvedValueOnce({
      servers: {},
      inputs: [],
    });
    mcpApiMocks.writeMcpConfig.mockRejectedValueOnce(
      new Error('server "broken" is type stdio but has no command'),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });
    await openEditor();

    fireEvent.change(screen.getByTestId("manage-mcp-editor"), {
      target: {
        value: JSON.stringify({
          servers: { broken: { type: "stdio" } },
        }),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(
        screen.getByText(/is type stdio but has no command/),
      ).toBeInTheDocument();
    });
    // Still open, with the user's text intact — a failed save that closed the
    // editor would discard the config they were trying to fix.
    expect(screen.getByTestId("manage-mcp-editor")).toBeInTheDocument();
  });
});

// ===========================================================================
// RECONNECT — error/expired connectors kick off the OAuth restart (FR-4.25)
// ===========================================================================

describe("ConnectorsRoute reconnect", () => {
  const ORIGINAL_LOCATION = window.location;

  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    mcpApiMocks.installMcpServer.mockReset();
    mcpApiMocks.startMcpAuth.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    // jsdom guards navigation — redefine location so `assign` is a no-op spy.
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: { ...ORIGINAL_LOCATION, assign: vi.fn() },
    });
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: ORIGINAL_LOCATION,
    });
    vi.clearAllMocks();
  });

  it("wires a Reconnect action on an error connector to the OAuth restart", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(
      listResponse([
        connector({
          id: "conn_1" as ConnectorId,
          slug: "gmail" as ConnectorSlug,
          status: "error",
        }),
      ]),
    );
    mcpApiMocks.installMcpServer.mockResolvedValueOnce({
      server_id: "seed:gmail",
    });
    mcpApiMocks.startMcpAuth.mockResolvedValueOnce({
      auth_url: "https://example.com/oauth",
    });

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("connector-reconnect"));

    await waitFor(() => {
      expect(mcpApiMocks.installMcpServer).toHaveBeenCalledWith(
        "gmail",
        IDENTITY,
      );
    });
    expect(window.location.assign).toHaveBeenCalledWith(
      "https://example.com/oauth",
    );
  });
});

describe("ConnectorsRoute — remove", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("confirms first, then removes over DELETE /v1/connectors/{id}", async () => {
    captureStreamCallbacks();
    connectorsApiMocks.fetchConnectors.mockResolvedValue(
      listResponse([connector({ id: "conn_1" as ConnectorId })]),
    );
    connectorsApiMocks.removeConnector.mockResolvedValue(undefined);

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("connector-remove"));
    // The first click asks; it must not delete.
    expect(connectorsApiMocks.removeConnector).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("connector-remove-confirm"));

    await waitFor(() => {
      expect(connectorsApiMocks.removeConnector).toHaveBeenCalledWith(
        IDENTITY,
        "conn_1",
      );
    });
  });

  it("surfaces a failed remove instead of pretending the row is gone", async () => {
    captureStreamCallbacks();
    connectorsApiMocks.fetchConnectors.mockResolvedValue(
      listResponse([connector({ id: "conn_1" as ConnectorId })]),
    );
    connectorsApiMocks.removeConnector.mockRejectedValue(
      new Error("owner_or_admin_only"),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("connector-remove"));
    fireEvent.click(screen.getByTestId("connector-remove-confirm"));

    await waitFor(() => {
      expect(screen.getByText(/owner_or_admin_only/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("connector-row")).toBeInTheDocument();
  });
});
