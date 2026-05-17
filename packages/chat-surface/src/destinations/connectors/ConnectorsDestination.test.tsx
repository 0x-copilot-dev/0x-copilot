import type { McpAuthState, McpServer } from "@enterprise-search/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  ConnectorsDestination,
  type McpServerRow,
} from "./ConnectorsDestination";

type RequestHandler = (req: TypedRequest) => Promise<unknown>;

function makeTransport(handler: RequestHandler): Transport {
  return {
    async request<TRes>(req: TypedRequest): Promise<TRes> {
      return (await handler(req)) as TRes;
    },
    subscribeServerSentEvents(_opts: SseSubscribeOptions): SseSubscription {
      return { close: () => undefined };
    },
    getSession(): Session {
      return { bearer: null };
    },
    capabilities(): TransportCapabilities {
      return {
        substrate: "web",
        nativeSecretStorage: false,
        fileSystemAccess: false,
        clipboardWrite: false,
        openExternal: false,
      };
    },
  };
}

function makeRouter(): Router<ArtifactRoute> {
  let current: ArtifactRoute | null = null;
  const subs = new Set<(r: ArtifactRoute) => void>();
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate: vi.fn((r: ArtifactRoute) => {
      current = r;
      for (const s of subs) s(r);
    }),
    subscribe(handler) {
      subs.add(handler);
      return () => subs.delete(handler);
    },
  };
}

function makeServer(
  overrides: Partial<McpServerRow> & {
    readonly server_id: string;
    readonly auth_state: McpAuthState;
  },
): McpServerRow {
  const base: McpServer = {
    server_id: overrides.server_id,
    name: overrides.name ?? overrides.server_id,
    display_name: overrides.display_name ?? overrides.server_id,
    url: overrides.url ?? `https://example.com/${overrides.server_id}`,
    transport: overrides.transport ?? "http",
    auth_mode: overrides.auth_mode ?? "oauth2",
    auth_state: overrides.auth_state,
    health: overrides.health ?? "healthy",
    enabled: overrides.enabled ?? true,
    oauth_client_configured: overrides.oauth_client_configured ?? true,
    created_at: overrides.created_at ?? "2026-05-01T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-15T00:00:00Z",
  };
  return {
    ...base,
    tool_count: overrides.tool_count ?? 0,
    last_used_at: overrides.last_used_at ?? null,
  };
}

const SAMPLE_SERVERS: readonly McpServerRow[] = [
  makeServer({
    server_id: "srv-notion",
    display_name: "Notion",
    auth_state: "authenticated",
    tool_count: 12,
    last_used_at: "2026-05-15T10:00:00Z",
  }),
  makeServer({
    server_id: "srv-slack",
    display_name: "Slack",
    auth_state: "auth_failed",
    tool_count: 7,
  }),
  makeServer({
    server_id: "srv-github",
    display_name: "GitHub",
    auth_state: "unauthenticated",
    tool_count: 5,
  }),
];

function renderWith(handler: RequestHandler): {
  router: Router<ArtifactRoute>;
} {
  const router = makeRouter();
  render(
    <TransportProvider transport={makeTransport(handler)}>
      <RouterProvider router={router}>
        <ConnectorsDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { router };
}

describe("ConnectorsDestination", () => {
  it("renders the skeleton while the initial request is in flight", async () => {
    let resolve!: (v: { servers: readonly McpServerRow[] }) => void;
    const pending = new Promise<{ servers: readonly McpServerRow[] }>((r) => {
      resolve = r;
    });
    renderWith(() => pending);
    expect(
      screen.getAllByTestId("connectors-skeleton-card").length,
    ).toBeGreaterThan(0);
    await act(async () => {
      resolve({ servers: [] });
      await pending;
    });
  });

  it("renders connector cards once the request resolves", async () => {
    renderWith(async () => ({ servers: SAMPLE_SERVERS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("connectors-card")).toHaveLength(3);
    });
    expect(screen.getByText("Notion")).toBeInTheDocument();
    expect(screen.getByText("Slack")).toBeInTheDocument();
  });

  it("renders affordances based on auth_state", async () => {
    renderWith(async () => ({ servers: SAMPLE_SERVERS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("connectors-card")).toHaveLength(3);
    });
    expect(screen.getByTestId("connectors-reauthorize")).toBeInTheDocument();
    expect(screen.getByTestId("connectors-disconnect")).toBeInTheDocument();
    expect(screen.getAllByTestId("connectors-connect")).toHaveLength(2);
  });

  it("renders the empty state when the servers list is empty", async () => {
    renderWith(async () => ({ servers: [] }));
    await waitFor(() => {
      expect(screen.getByTestId("connectors-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("connectors-card")).toBeNull();
  });

  it("renders the error state and recovers on retry", async () => {
    let calls = 0;
    renderWith(async () => {
      calls += 1;
      if (calls === 1) throw new Error("network failure");
      return { servers: SAMPLE_SERVERS };
    });
    await waitFor(() => {
      expect(screen.getByTestId("connectors-error")).toBeInTheDocument();
    });
    expect(screen.getByText("network failure")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("connectors-retry"));
    await waitFor(() => {
      expect(screen.getAllByTestId("connectors-card")).toHaveLength(3);
    });
    expect(calls).toBe(2);
  });

  it("clicking a card navigates with {kind:'mcp', serverId}", async () => {
    const { router } = renderWith(async () => ({ servers: SAMPLE_SERVERS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("connectors-card")).toHaveLength(3);
    });
    fireEvent.click(screen.getAllByTestId("connectors-card")[0]);
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "mcp",
      serverId: "srv-notion",
    });
  });

  it("clicking the Reauthorize button does not also navigate", async () => {
    const { router } = renderWith(async () => ({ servers: SAMPLE_SERVERS }));
    await waitFor(() => {
      expect(screen.getAllByTestId("connectors-card")).toHaveLength(3);
    });
    fireEvent.click(screen.getByTestId("connectors-reauthorize"));
    expect(router.navigate).not.toHaveBeenCalled();
  });
});
