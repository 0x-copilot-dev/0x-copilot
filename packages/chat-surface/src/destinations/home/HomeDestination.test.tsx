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

import type {
  ConversationId,
  RunId,
  SkillId,
} from "@enterprise-search/api-types";

import { HomeDestination, type HomePayload } from "./HomeDestination";

interface DeferredController<T> {
  readonly transport: Transport;
  resolve(value: T): void;
  reject(error: unknown): void;
}

function makeDeferredTransport<T>(): DeferredController<T> {
  let resolveFn: (value: T) => void = () => undefined;
  let rejectFn: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolveFn = res;
    rejectFn = rej;
  });
  const transport: Transport = {
    request<TRes>(_req: TypedRequest): Promise<TRes> {
      return promise as unknown as Promise<TRes>;
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
    resolve: (value: T) => resolveFn(value),
    reject: (error: unknown) => rejectFn(error),
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

function renderHome(
  transport: Transport,
  router: Router<ArtifactRoute> = makeRouter(),
): void {
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <HomeDestination />
      </RouterProvider>
    </TransportProvider>,
  );
}

const PAYLOAD: HomePayload = {
  pinned: [
    {
      conversationId: "conv_001" as ConversationId,
      title: "Renewal-uplift exploration",
      lastMessageAt: "2026-05-17T10:00:00.000Z",
      subtitle: "Acme",
    },
  ],
  recent_runs: [
    {
      runId: "run_001" as RunId,
      title: "Q3 forecast refresh",
      status: "succeeded",
      startedAt: "2026-05-17T09:30:00.000Z",
    },
  ],
  favorites: [
    {
      skillId: "skill_001" as SkillId,
      name: "salesforce.opportunity",
      subtitle: "Pinned tool",
    },
  ],
};

describe("HomeDestination", () => {
  it("renders skeleton placeholders while the home request is in flight", () => {
    const controller = makeDeferredTransport<HomePayload>();
    renderHome(controller.transport);

    const section = screen.getByRole("region", { name: /home destination/i });
    expect(section).toHaveAttribute("data-state", "loading");
    const placeholders = screen.getAllByTestId("home-skeleton-card");
    expect(placeholders).toHaveLength(3);
  });

  it("renders the three populated sections and navigates on card click", async () => {
    const controller = makeDeferredTransport<HomePayload>();
    const router = makeRouter();
    renderHome(controller.transport, router);

    controller.resolve(PAYLOAD);

    const section = await screen.findByRole("region", {
      name: /home destination/i,
    });
    await waitFor(() => expect(section).toHaveAttribute("data-state", "ready"));

    expect(screen.getByTestId("home-section-pinned")).toBeInTheDocument();
    expect(screen.getByTestId("home-section-recent-runs")).toBeInTheDocument();
    expect(screen.getByTestId("home-section-favorites")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("home-pinned-card"));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "chat",
      conversationId: "conv_001",
    });

    fireEvent.click(screen.getByTestId("home-recent-run-card"));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "run",
      runId: "run_001",
    });

    fireEvent.click(screen.getByTestId("home-favorite-card"));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "skill",
      skillId: "skill_001",
    });
  });

  it("renders per-section empty hints when each list is empty", async () => {
    const controller = makeDeferredTransport<HomePayload>();
    renderHome(controller.transport);

    controller.resolve({ pinned: [], recent_runs: [], favorites: [] });

    await waitFor(() => {
      expect(screen.getAllByTestId("home-section-empty")).toHaveLength(3);
    });
  });

  it("renders an error panel and re-fetches when Retry is clicked", async () => {
    let call = 0;
    let resolveFn: (value: HomePayload) => void = () => undefined;
    let rejectFn: (error: unknown) => void = () => undefined;
    const transport: Transport = {
      request<TRes>(_req: TypedRequest): Promise<TRes> {
        call += 1;
        return new Promise<TRes>((res, rej) => {
          if (call === 1) {
            rejectFn = rej as (error: unknown) => void;
          } else {
            resolveFn = res as unknown as (value: HomePayload) => void;
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
    renderHome(transport);

    rejectFn(new Error("offline"));

    const alert = await screen.findByTestId("home-error");
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent(/offline/i);

    fireEvent.click(screen.getByTestId("home-retry"));
    expect(call).toBe(2);

    resolveFn(PAYLOAD);

    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /home destination/i }),
      ).toHaveAttribute("data-state", "ready"),
    );
  });
});
