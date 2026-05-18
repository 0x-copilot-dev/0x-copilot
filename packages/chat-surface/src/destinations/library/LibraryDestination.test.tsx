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

import { LibraryDestination } from "./LibraryDestination";

// Wave-0 LibraryDestination is the dignified placeholder. It must
// not hit the backend — the previous fetch to /v1/library?kind=adapter
// 404'd (Phase 7 work). These tests pin the no-fetch contract and
// the placeholder copy.

type AnyRoute = unknown;

function makeRouter(): Router<AnyRoute> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate = vi.fn<(route: AnyRoute, opts?: NavigateOptions) => void>();
  return {
    current(): AnyRoute {
      return { screen: "chat", destination: "library" } as AnyRoute;
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
        new Error("LibraryDestination must not fetch"),
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

function renderLibrary() {
  const { transport, requests } = makeTransport();
  const router = makeRouter();
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <LibraryDestination />
      </RouterProvider>
    </TransportProvider>,
  );
  return { requests, router };
}

describe("LibraryDestination (Wave-0 placeholder)", () => {
  it("renders the placeholder and never calls the transport", () => {
    const { requests } = renderLibrary();
    expect(screen.getByTestId("destination-placeholder")).toBeInTheDocument();
    expect(
      screen.getByTestId("destination-placeholder-title"),
    ).toHaveTextContent("Your knowledge library");
    expect(
      screen.getByTestId("destination-placeholder-phase"),
    ).toHaveTextContent("Coming in Phase 7");
    expect(requests).toHaveLength(0);
  });

  it("does not render the old Adapters/Results/Knowledge tab bar", () => {
    renderLibrary();
    expect(screen.queryByTestId("library-tab-adapter")).not.toBeInTheDocument();
    expect(screen.queryByTestId("library-tab-result")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("library-tab-knowledge"),
    ).not.toBeInTheDocument();
  });

  it("navigates via the host's chat destination route when a bridge is clicked", () => {
    const { router } = renderLibrary();
    fireEvent.click(
      screen.getByTestId("destination-placeholder-bridge-connectors"),
    );
    expect(router.navigate).toHaveBeenCalledWith({
      screen: "chat",
      destination: "connectors",
    });
  });
});
