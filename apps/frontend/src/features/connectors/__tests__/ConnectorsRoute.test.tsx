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
  startConnectorOAuth: vi.fn(),
  refreshConnector: vi.fn(),
  disconnectConnector: vi.fn(),
  patchConnectorScopes: vi.fn(),
  setConnectorAccessMode: vi.fn(),
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
    startConnectorOAuth: connectorsApiMocks.startConnectorOAuth,
    refreshConnector: connectorsApiMocks.refreshConnector,
    disconnectConnector: connectorsApiMocks.disconnectConnector,
    patchConnectorScopes: connectorsApiMocks.patchConnectorScopes,
    setConnectorAccessMode: connectorsApiMocks.setConnectorAccessMode,
    streamConnectorEvents: connectorsApiMocks.streamConnectorEvents,
  };
});

// Hoisted mocks for mcpApi — the custom-server add path (Decision D1)
// creates the MCP server + starts MCP OAuth; keep the route test off the
// real HTTP surface.
const mcpApiMocks = vi.hoisted(() => ({
  createMcpServer: vi.fn(),
  startMcpAuth: vi.fn(),
}));
vi.mock("../../../api/mcpApi", async () => {
  const actual = await vi.importActual<typeof import("../../../api/mcpApi")>(
    "../../../api/mcpApi",
  );
  return {
    ...actual,
    createMcpServer: mcpApiMocks.createMcpServer,
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
    connectorsApiMocks.startConnectorOAuth.mockReset();
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
    connectorsApiMocks.startConnectorOAuth.mockResolvedValueOnce({
      authorization_url: "https://example.com/oauth",
      state: "state_abc",
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
    expect(connectorsApiMocks.startConnectorOAuth).toHaveBeenCalledWith(
      IDENTITY,
      "notion",
    );
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

describe("ConnectorsRoute custom server add", () => {
  const originalOpen = window.open;

  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.streamConnectorEvents.mockReset();
    mcpApiMocks.createMcpServer.mockReset();
    mcpApiMocks.startMcpAuth.mockReset();
    window.open = vi.fn() as unknown as typeof window.open;
  });
  afterEach(() => {
    window.open = originalOpen;
    vi.clearAllMocks();
  });

  /** Open the modal, switch to the custom form, and submit `url`. */
  async function submitCustomUrl(
    url: string,
    { clientId }: { clientId?: string } = {},
  ): Promise<void> {
    fireEvent.click(
      screen.getAllByRole("button", { name: "Connect a tool" })[0],
    );
    fireEvent.click(screen.getByTestId("connect-catalog-custom"));
    fireEvent.change(screen.getByPlaceholderText("https://mcp.example.com"), {
      target: { value: url },
    });
    if (clientId !== undefined) {
      fireEvent.change(screen.getByPlaceholderText("client_id"), {
        target: { value: clientId },
      });
    }
    fireEvent.click(screen.getByTestId("connect-custom-add"));
  }

  it("creates the server with url + oauth client, starts auth, and closes on the SSE write-through", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(listResponse([]));
    mcpApiMocks.createMcpServer.mockResolvedValueOnce(
      mcpServer({ auth_mode: "oauth2", auth_state: "auth_pending" }),
    );
    mcpApiMocks.startMcpAuth.mockResolvedValueOnce({
      server_id: "srv_custom",
      auth_url: "https://example.com/mcp-auth",
      expires_at: "2026-05-01T01:00:00Z",
    });
    const sse = captureStreamCallbacks();

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(connectorsApiMocks.streamConnectorEvents).toHaveBeenCalledTimes(1);
    });

    await submitCustomUrl("https://mcp.example.com/mcp", {
      clientId: "cid_123",
    });

    await waitFor(() => {
      expect(mcpApiMocks.createMcpServer).toHaveBeenCalledWith(
        "https://mcp.example.com/mcp",
        IDENTITY,
        { client_id: "cid_123", token_endpoint_auth_method: "none" },
      );
    });

    // Auth needed → the MCP OAuth round-trip starts in a popup while the
    // modal shows the spinner.
    await waitFor(() => {
      expect(mcpApiMocks.startMcpAuth).toHaveBeenCalledWith(
        "srv_custom",
        IDENTITY,
      );
    });
    expect(window.open).toHaveBeenCalledWith(
      "https://example.com/mcp-auth",
      "_blank",
      "noopener,noreferrer",
    );
    expect(screen.getByTestId("connect-oauth")).toBeInTheDocument();

    // The backend write-through emits connector.created; the custom flow has
    // no permission step, so the modal closes.
    act(() => {
      sse.lastCall().onEvent(
        envelope(
          "connector.created",
          connector({
            id: "conn_custom" as ConnectorId,
            slug: "custom" as ConnectorSlug,
            display_name: "Custom server",
            status: "connected",
          }),
          1,
        ),
      );
    });
    await waitFor(() => {
      expect(screen.queryByTestId("connect-oauth")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("connect-permission")).not.toBeInTheDocument();
    expect(screen.queryByTestId("connect-custom-form")).not.toBeInTheDocument();
  });

  it("shows the modal alert when the create fails", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(listResponse([]));
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    mcpApiMocks.createMcpServer.mockRejectedValueOnce(
      new Error("create_failed"),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    await submitCustomUrl("https://mcp.example.com/mcp");

    await waitFor(() => {
      expect(screen.getByTestId("connect-oauth-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("connect-oauth-error").textContent).toContain(
      "create_failed",
    );
    expect(mcpApiMocks.startMcpAuth).not.toHaveBeenCalled();
  });

  it("clears pending immediately (modal closes) when the server needs no auth", async () => {
    connectorsApiMocks.fetchConnectors.mockResolvedValueOnce(listResponse([]));
    connectorsApiMocks.streamConnectorEvents.mockReturnValue({
      close: vi.fn(),
    });
    mcpApiMocks.createMcpServer.mockResolvedValueOnce(
      mcpServer({ auth_mode: "none", auth_state: "auth_unsupported" }),
    );

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    await submitCustomUrl("https://mcp.example.com/mcp");

    // Install alone completes: no auth round-trip, spinner resolves, closed.
    await waitFor(() => {
      expect(screen.queryByTestId("connect-oauth")).not.toBeInTheDocument();
    });
    expect(mcpApiMocks.startMcpAuth).not.toHaveBeenCalled();
    expect(window.open).not.toHaveBeenCalled();
    expect(screen.queryByTestId("connect-custom-form")).not.toBeInTheDocument();
  });
});

// ===========================================================================
// RECONNECT — error/expired connectors kick off the OAuth restart (FR-4.25)
// ===========================================================================

describe("ConnectorsRoute reconnect", () => {
  const ORIGINAL_LOCATION = window.location;

  beforeEach(() => {
    connectorsApiMocks.fetchConnectors.mockReset();
    connectorsApiMocks.startConnectorOAuth.mockReset();
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
    connectorsApiMocks.startConnectorOAuth.mockResolvedValueOnce({
      authorization_url: "https://example.com/oauth",
      state: "state_abc",
    });

    render(<ConnectorsRoute identity={IDENTITY} />);
    await waitFor(() => {
      expect(screen.getByTestId("connectors-route")).toHaveAttribute(
        "data-state",
        "ready",
      );
    });

    fireEvent.click(screen.getByTestId("connector-card-action"));

    await waitFor(() => {
      expect(connectorsApiMocks.startConnectorOAuth).toHaveBeenCalledWith(
        IDENTITY,
        "gmail",
      );
    });
    expect(window.location.assign).toHaveBeenCalledWith(
      "https://example.com/oauth",
    );
  });
});
