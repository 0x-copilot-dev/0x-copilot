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

import { MemoryDestination } from "./MemoryDestination";

// Wave-0 MemoryDestination is the dignified placeholder. It must
// not hit the backend — the previous fetch to /v1/memory 404'd
// (Phase 11 work).

type AnyRoute = unknown;

function makeRouter(): Router<AnyRoute> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn<(route: AnyRoute, opts?: NavigateOptions) => void>();
  return {
    current(): AnyRoute {
      return { screen: "chat", destination: "memory" } as AnyRoute;
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
        new Error("MemoryDestination must not fetch"),
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

function renderMemory() {
  const { transport, requests } = makeTransport();
  const router = makeRouter();
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <MemoryDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { requests, router };
}

describe("MemoryDestination (Wave-0 placeholder)", () => {
  it("renders the placeholder and never calls the transport", () => {
    const { requests } = renderMemory();
    expect(screen.getByTestId("destination-placeholder")).toBeInTheDocument();
    expect(
      screen.getByTestId("destination-placeholder-title"),
    ).toHaveTextContent("What the agent remembers");
    expect(
      screen.getByTestId("destination-placeholder-phase"),
    ).toHaveTextContent("Coming in Phase 11");
    expect(requests).toHaveLength(0);
  });

  it("navigates via the host's chat destination route when a bridge is clicked", () => {
    const { router } = renderMemory();
    fireEvent.click(screen.getByTestId("destination-placeholder-bridge-team"));
    expect(router.navigate).toHaveBeenCalledWith({
      screen: "chat",
      destination: "team",
    });
  });
});
