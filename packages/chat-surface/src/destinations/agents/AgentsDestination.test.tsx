import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { NavigateOptions, Router } from "../../routing/router";

import { AgentsDestination } from "./AgentsDestination";

// Wave-0 AgentsDestination is the dignified placeholder. It must not
// hit the backend — the previous "loading → 405 → Retry" loop was the
// regression we're fixing. These tests pin both contracts:
//   1. zero transport.request calls
//   2. the placeholder copy + bridges are correct and clickable.

type AnyRoute = unknown;

function makeRouter(): Router<AnyRoute> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn<(route: AnyRoute, opts?: NavigateOptions) => void>();
  return {
    current(): AnyRoute {
      return { screen: "chat", destination: "agents" } as AnyRoute;
    },
    navigate,
    subscribe: () => () => {},
  };
}

function makeTransport(): {
  readonly transport: Transport;
  readonly requests: ReadonlyArray<TypedRequest>;
} {
  const requests: TypedRequest[] = [];
  const transport: Transport = {
    request<TRes>(req: TypedRequest): Promise<TRes> {
      requests.push(req);
      return Promise.reject(
        new Error("AgentsDestination must not fetch"),
      ) as Promise<TRes>;
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
  return { transport, requests };
}

function renderAgents() {
  const { transport, requests } = makeTransport();
  const router = makeRouter();
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <AgentsDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { requests, router };
}

describe("AgentsDestination (Wave-0 placeholder)", () => {
  it("renders the placeholder and never calls the transport", () => {
    const { requests } = renderAgents();
    expect(screen.getByTestId("destination-placeholder")).toBeInTheDocument();
    expect(
      screen.getByTestId("destination-placeholder-title"),
    ).toHaveTextContent("Manage your agents");
    expect(
      screen.getByTestId("destination-placeholder-phase"),
    ).toHaveTextContent("Coming in Phase 8");
    expect(requests).toHaveLength(0);
  });

  it("exposes bridges to home and chats", () => {
    renderAgents();
    expect(
      screen.getByTestId("destination-placeholder-bridge-home"),
    ).toHaveTextContent(/recent agent activity in Home/i);
    expect(
      screen.getByTestId("destination-placeholder-bridge-chats"),
    ).toHaveTextContent(/past runs in your Chats/i);
  });

  it("navigates via the host's chat destination route when a bridge is clicked", () => {
    const { router } = renderAgents();
    fireEvent.click(screen.getByTestId("destination-placeholder-bridge-home"));
    expect(router.navigate).toHaveBeenCalledWith({
      screen: "chat",
      destination: "home",
    });
  });
});
