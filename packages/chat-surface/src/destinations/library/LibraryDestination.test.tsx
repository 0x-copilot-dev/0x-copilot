import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type {
  ArtifactRoute,
  NavigateOptions,
  Router,
} from "../../routing/router";

import {
  LibraryDestination,
  type LibraryItem,
  type LibraryItemId,
} from "./LibraryDestination";

interface DeferredTransport {
  readonly transport: Transport;
  resolve(response: unknown): void;
  reject(err: Error): void;
  reset(): void;
  readonly calls: ReadonlyArray<TypedRequest>;
}

function makeDeferredTransport(): DeferredTransport {
  let resolver: ((value: unknown) => void) | null = null;
  let rejecter: ((err: Error) => void) | null = null;
  const calls: TypedRequest[] = [];

  function newPromise(): Promise<unknown> {
    return new Promise<unknown>((resolve, reject) => {
      resolver = resolve;
      rejecter = reject;
    });
  }

  let pending: Promise<unknown> = newPromise();

  const transport: Transport = {
    request<TRes>(req: TypedRequest): Promise<TRes> {
      calls.push(req);
      return pending as Promise<TRes>;
    },
    subscribeServerSentEvents(_opts: SseSubscribeOptions): SseSubscription {
      return { close: () => {} };
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

  return {
    transport,
    resolve(response) {
      resolver?.(response);
    },
    reject(err) {
      rejecter?.(err);
    },
    reset() {
      pending = newPromise();
    },
    get calls(): ReadonlyArray<TypedRequest> {
      return calls;
    },
  };
}

function makeRouter(): Router<ArtifactRoute> & {
  readonly navigate: ReturnType<typeof vi.fn>;
} {
  const navigate =
    vi.fn<(route: ArtifactRoute, opts?: NavigateOptions) => void>();
  return {
    current(): ArtifactRoute {
      return { kind: "workspace", workspaceId: "current" };
    },
    navigate,
    subscribe: () => () => {},
  };
}

const ADAPTERS: ReadonlyArray<LibraryItem> = [
  {
    id: "adapter-1" as LibraryItemId,
    kind: "adapter",
    title: "Salesforce Opportunity v3",
    modifiedAt: new Date(Date.now() - 2 * 60_000).toISOString(),
    subtitle: "sf-opp://",
  },
  {
    id: "adapter-2" as LibraryItemId,
    kind: "adapter",
    title: "Linear Issue v1",
    modifiedAt: new Date(Date.now() - 24 * 3_600_000).toISOString(),
    subtitle: "linear-issue://",
  },
];

const RESULTS: ReadonlyArray<LibraryItem> = [
  {
    id: "result-1" as LibraryItemId,
    kind: "result",
    title: "ARR by segment Q3",
    modifiedAt: new Date(Date.now() - 4 * 3_600_000).toISOString(),
  },
];

function renderLibrary(deferred: DeferredTransport, router = makeRouter()) {
  return {
    router,
    ...render(
      <TransportProvider transport={deferred.transport}>
        <RouterProvider router={router}>
          <LibraryDestination />
        </RouterProvider>
      </TransportProvider>,
    ),
  };
}

describe("LibraryDestination", () => {
  it("renders skeleton rows while the initial request is pending", () => {
    const deferred = makeDeferredTransport();
    renderLibrary(deferred);
    const list = screen.getByTestId("library-list");
    expect(list).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("library-skeleton-row")).toHaveLength(4);
    expect(deferred.calls).toHaveLength(1);
    expect(deferred.calls[0]).toEqual({
      method: "GET",
      path: "/v1/library",
      query: { kind: "adapter" },
    });
  });

  it("renders item rows when the request resolves with data", async () => {
    const deferred = makeDeferredTransport();
    renderLibrary(deferred);
    await act(async () => {
      deferred.resolve({ items: ADAPTERS });
      await Promise.resolve();
    });
    const items = screen.getAllByTestId("library-item");
    expect(items).toHaveLength(2);
    expect(screen.getByText("Salesforce Opportunity v3")).toBeInTheDocument();
    expect(screen.getByText("Linear Issue v1")).toBeInTheDocument();
  });

  it("renders an empty state with per-tab copy when results are empty", async () => {
    const deferred = makeDeferredTransport();
    renderLibrary(deferred);
    await act(async () => {
      deferred.resolve({ items: [] });
      await Promise.resolve();
    });
    const empty = screen.getByTestId("library-empty");
    expect(empty).toBeInTheDocument();
    expect(screen.getByText("No saved adapters")).toBeInTheDocument();
  });

  it("renders the error panel and retries on click", async () => {
    const deferred = makeDeferredTransport();
    renderLibrary(deferred);
    await act(async () => {
      deferred.reject(new Error("library unavailable"));
      await Promise.resolve();
    });
    expect(screen.getByTestId("library-error")).toBeInTheDocument();
    expect(screen.getByText("library unavailable")).toBeInTheDocument();

    deferred.reset();
    fireEvent.click(screen.getByTestId("library-retry"));
    expect(screen.getByTestId("library-list")).toHaveAttribute(
      "data-state",
      "loading",
    );
    await act(async () => {
      deferred.resolve({ items: ADAPTERS });
      await Promise.resolve();
    });
    expect(screen.getAllByTestId("library-item")).toHaveLength(2);
  });

  it("switching tabs re-issues the request with the new kind", async () => {
    const deferred = makeDeferredTransport();
    renderLibrary(deferred);
    await act(async () => {
      deferred.resolve({ items: ADAPTERS });
      await Promise.resolve();
    });
    deferred.reset();
    fireEvent.click(screen.getByTestId("library-tab-result"));
    expect(screen.getByTestId("library-list")).toHaveAttribute(
      "data-state",
      "loading",
    );
    const lastCall = deferred.calls[deferred.calls.length - 1]!;
    expect(lastCall).toEqual({
      method: "GET",
      path: "/v1/library",
      query: { kind: "result" },
    });
    await act(async () => {
      deferred.resolve({ items: RESULTS });
      await Promise.resolve();
    });
    expect(screen.getAllByTestId("library-item")).toHaveLength(1);
    expect(screen.getByText("ARR by segment Q3")).toBeInTheDocument();
  });

  it("clicking Open routes via the router with a workspace placeholder", async () => {
    const deferred = makeDeferredTransport();
    const { router } = renderLibrary(deferred);
    await act(async () => {
      deferred.resolve({ items: ADAPTERS });
      await Promise.resolve();
    });
    fireEvent.click(
      screen.getByRole("button", { name: /open salesforce opportunity v3/i }),
    );
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "workspace",
      workspaceId: "adapter-1",
    });
  });
});
