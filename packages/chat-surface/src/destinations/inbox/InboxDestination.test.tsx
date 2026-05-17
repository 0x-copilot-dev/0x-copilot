import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import {
  InboxDestination,
  type InboxFilter,
  type InboxItemId,
  type InboxPayload,
} from "./InboxDestination";

interface DeferredController {
  readonly transport: Transport;
  readonly calls: Array<TypedRequest>;
  resolveGet(payload: InboxPayload): void;
  rejectGet(error: unknown): void;
  resolveLastPost(value?: unknown): void;
  rejectLastPost(error: unknown): void;
}

function makeDeferredTransport(): DeferredController {
  const calls: Array<TypedRequest> = [];
  let resolveGet: (value: InboxPayload) => void = () => undefined;
  let rejectGet: (error: unknown) => void = () => undefined;
  const postResolvers: Array<{
    resolve: (value: unknown) => void;
    reject: (error: unknown) => void;
  }> = [];

  const transport: Transport = {
    request<TRes>(req: TypedRequest): Promise<TRes> {
      calls.push(req);
      if (req.method === "GET") {
        return new Promise<TRes>((res, rej) => {
          resolveGet = res as unknown as (value: InboxPayload) => void;
          rejectGet = rej as (error: unknown) => void;
        });
      }
      return new Promise<TRes>((res, rej) => {
        postResolvers.push({
          resolve: res as (value: unknown) => void,
          reject: rej as (error: unknown) => void,
        });
      });
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

  return {
    transport,
    calls,
    resolveGet(payload) {
      resolveGet(payload);
    },
    rejectGet(error) {
      rejectGet(error);
    },
    resolveLastPost(value) {
      const entry = postResolvers.shift();
      if (entry === undefined) throw new Error("no pending POST");
      entry.resolve(value);
    },
    rejectLastPost(error) {
      const entry = postResolvers.shift();
      if (entry === undefined) throw new Error("no pending POST");
      entry.reject(error);
    },
  };
}

function makeRouter(): Router<ArtifactRoute> & {
  navigate: ReturnType<typeof vi.fn>;
} {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  const navigate = vi.fn((r: ArtifactRoute) => {
    current = r;
    for (const s of subscribers) s(r);
  });
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate,
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderInbox(
  transport: Transport,
  router: Router<ArtifactRoute> = makeRouter(),
): void {
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <InboxDestination />
      </RouterProvider>
    </TransportProvider>,
  );
}

const COUNTS: Record<InboxFilter, number> = {
  all: 2,
  mentions: 1,
  approvals: 1,
  errors: 0,
};

const PAYLOAD: InboxPayload = {
  items: [
    {
      id: "inbox_001" as InboxItemId,
      kind: "mention",
      title: "Sarah mentioned you in #revops",
      source: "Slack · #revops",
      receivedAt: "2026-05-17T10:00:00.000Z",
    },
    {
      id: "inbox_002" as InboxItemId,
      kind: "approval",
      title: "Approve Salesforce stage change",
      source: "Run rn_42",
      receivedAt: "2026-05-17T09:30:00.000Z",
      route: { kind: "run", runId: "rn_42" },
    },
  ],
  counts: COUNTS,
};

describe("InboxDestination", () => {
  it("renders skeleton rows while the inbox request is in flight", () => {
    const controller = makeDeferredTransport();
    renderInbox(controller.transport);

    const section = screen.getByRole("region", { name: /inbox destination/i });
    expect(section).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("inbox-skeleton-row")).toHaveLength(5);
  });

  it("renders populated rows, shows tab counts, and navigates on title click", async () => {
    const controller = makeDeferredTransport();
    const router = makeRouter();
    renderInbox(controller.transport, router);

    controller.resolveGet(PAYLOAD);

    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /inbox destination/i }),
      ).toHaveAttribute("data-state", "ready"),
    );

    expect(screen.getAllByTestId("inbox-item")).toHaveLength(2);
    expect(screen.getByTestId("inbox-count-all")).toHaveTextContent("2");
    expect(screen.getByTestId("inbox-count-mentions")).toHaveTextContent("1");

    const [firstOpen, secondOpen] = screen.getAllByTestId("inbox-item-open");
    fireEvent.click(firstOpen!);
    expect(router.navigate).toHaveBeenLastCalledWith({
      kind: "workspace",
      workspaceId: "inbox_001",
    });

    fireEvent.click(secondOpen!);
    expect(router.navigate).toHaveBeenLastCalledWith({
      kind: "run",
      runId: "rn_42",
    });

    fireEvent.click(screen.getAllByTestId("inbox-item-mark-read")[0]!);
    controller.resolveLastPost(undefined);
    await waitFor(() =>
      expect(screen.getAllByTestId("inbox-item")).toHaveLength(1),
    );
  });

  it("renders the empty panel when the active filter has no items", async () => {
    const controller = makeDeferredTransport();
    renderInbox(controller.transport);

    controller.resolveGet({
      items: [],
      counts: { all: 0, mentions: 0, approvals: 0, errors: 0 },
    });

    await waitFor(() => {
      expect(screen.getByTestId("inbox-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("inbox-empty")).toHaveTextContent(/inbox zero/i);
  });

  it("renders an error panel and re-fetches when Retry is clicked", async () => {
    let call = 0;
    let rejectFn: (error: unknown) => void = () => undefined;
    let resolveFn: (value: InboxPayload) => void = () => undefined;
    const transport: Transport = {
      request<TRes>(_req: TypedRequest): Promise<TRes> {
        call += 1;
        return new Promise<TRes>((res, rej) => {
          if (call === 1) {
            rejectFn = rej as (error: unknown) => void;
          } else {
            resolveFn = res as unknown as (value: InboxPayload) => void;
          }
        });
      },
      subscribeServerSentEvents(): SseSubscription {
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

    renderInbox(transport);
    rejectFn(new Error("upstream timeout"));

    const alert = await screen.findByTestId("inbox-error");
    expect(alert).toHaveTextContent(/upstream timeout/i);

    fireEvent.click(screen.getByTestId("inbox-retry"));
    expect(call).toBe(2);
    resolveFn(PAYLOAD);

    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /inbox destination/i }),
      ).toHaveAttribute("data-state", "ready"),
    );
  });
});
