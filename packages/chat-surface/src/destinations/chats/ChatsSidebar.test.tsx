import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { ChatsSidebar } from "./ChatsSidebar";

interface ProjectsResponse {
  readonly projects: ReadonlyArray<{
    readonly id: string;
    readonly name: string;
    readonly threads: ReadonlyArray<{
      readonly id: string;
      readonly title: string;
      readonly updated_at: string;
    }>;
  }>;
}

const SAMPLE: ProjectsResponse = {
  projects: [
    {
      id: "p-rev",
      name: "Revenue Ops",
      threads: [
        {
          id: "t-pipeline",
          title: "Pipeline review",
          updated_at: "2026-05-17T10:00:00Z",
        },
        {
          id: "t-renewal",
          title: "Renewal playbook",
          updated_at: "2026-05-17T09:00:00Z",
        },
      ],
    },
    {
      id: "p-prod",
      name: "Product",
      threads: [
        {
          id: "t-roadmap",
          title: "Q3 roadmap",
          updated_at: "2026-05-17T08:00:00Z",
        },
      ],
    },
  ],
};

function makeTransport(options: {
  readonly response?: ProjectsResponse;
  readonly error?: Error;
  readonly defer?: boolean;
}): {
  readonly transport: Transport;
  readonly request: ReturnType<typeof vi.fn>;
  resolve(): void;
} {
  let resolveDeferred: (() => void) | null = null;
  const request = vi.fn(async (req: TypedRequest): Promise<unknown> => {
    if (req.path !== "/v1/chats/projects") return {};
    if (options.error !== undefined) throw options.error;
    if (options.defer === true) {
      await new Promise<void>((res) => {
        resolveDeferred = res;
      });
    }
    return options.response ?? SAMPLE;
  });
  const transport: Transport = {
    request: request as <TRes>(r: TypedRequest) => Promise<TRes>,
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
    request,
    resolve(): void {
      resolveDeferred?.();
    },
  };
}

function makeRouter(initial: ArtifactRoute | null): Router<ArtifactRoute> & {
  __set(r: ArtifactRoute): void;
} {
  let current: ArtifactRoute | null = initial;
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
    __set(r: ArtifactRoute) {
      current = r;
      for (const s of subscribers) s(r);
    },
  };
}

function renderSidebar(
  transport: Transport,
  router: Router<ArtifactRoute>,
  props: Partial<{
    fullscreen: boolean;
    onFullscreenChange: (next: boolean) => void;
  }> = {},
): void {
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <ChatsSidebar {...props} />
      </RouterProvider>
    </TransportProvider>,
  );
}

describe("ChatsSidebar", () => {
  it("renders loading then the project list after the transport resolves", async () => {
    const { transport, resolve } = makeTransport({ defer: true });
    const router = makeRouter(null);
    renderSidebar(transport, router);

    expect(screen.getByTestId("chats-sidebar-loading")).toBeInTheDocument();
    await act(async () => {
      resolve();
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(
        screen.queryByTestId("chats-sidebar-loading"),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText("Revenue Ops")).toBeInTheDocument();
    expect(screen.getByText("Product")).toBeInTheDocument();
  });

  it("renders an error sentinel when the transport rejects", async () => {
    const { transport } = makeTransport({ error: new Error("boom") });
    const router = makeRouter(null);
    renderSidebar(transport, router);

    const alert = await screen.findByTestId("chats-sidebar-error");
    expect(alert).toHaveTextContent("boom");
  });

  it("projects collapsed by default; clicking the caret expands threads", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    await screen.findByText("Revenue Ops");
    expect(screen.queryByText("Pipeline review")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /toggle revenue ops/i }),
    );
    expect(screen.getByText("Pipeline review")).toBeInTheDocument();
    expect(screen.getByText("Renewal playbook")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /toggle revenue ops/i }),
    );
    expect(screen.queryByText("Pipeline review")).not.toBeInTheDocument();
  });

  it("auto-expands the project that contains the active thread", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter({
      kind: "chat",
      conversationId: "t-roadmap",
    });
    renderSidebar(transport, router);

    await screen.findByText("Q3 roadmap");
    expect(screen.queryByText("Pipeline review")).not.toBeInTheDocument();
  });

  it("search filters by thread title and auto-expands the matching project", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    await screen.findByText("Revenue Ops");
    const input = screen.getByRole("searchbox", { name: /search chats/i });
    fireEvent.change(input, { target: { value: "roadmap" } });

    expect(screen.queryByText("Revenue Ops")).not.toBeInTheDocument();
    expect(screen.getByText("Product")).toBeInTheDocument();
    expect(screen.getByText("Q3 roadmap")).toBeInTheDocument();
  });

  it("search by project name keeps that project, hides others", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    await screen.findByText("Revenue Ops");
    const input = screen.getByRole("searchbox", { name: /search chats/i });
    fireEvent.change(input, { target: { value: "product" } });

    expect(screen.queryByText("Revenue Ops")).not.toBeInTheDocument();
    expect(screen.getByText("Product")).toBeInTheDocument();
  });

  it("clicking a thread navigates with kind: chat", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    await screen.findByText("Revenue Ops");
    fireEvent.click(
      screen.getByRole("button", { name: /toggle revenue ops/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /pipeline review/i }));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "chat",
      conversationId: "t-pipeline",
    });
  });

  it("the active thread row has aria-current=page", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter({
      kind: "chat",
      conversationId: "t-pipeline",
    });
    renderSidebar(transport, router);

    const row = await screen.findByRole("button", { name: /pipeline review/i });
    expect(row).toHaveAttribute("aria-current", "page");
    expect(row).toHaveAttribute("data-state", "active");
  });

  it("active highlight updates when the router publishes a new route within the same project", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter({
      kind: "chat",
      conversationId: "t-pipeline",
    });
    renderSidebar(transport, router);

    const initialActive = await screen.findByRole("button", {
      name: /pipeline review/i,
    });
    expect(initialActive).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: /renewal playbook/i }),
    ).not.toHaveAttribute("aria-current");

    act(() => {
      router.__set({ kind: "chat", conversationId: "t-renewal" });
    });

    const nextActive = await screen.findByRole("button", {
      name: /renewal playbook/i,
    });
    expect(nextActive).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("button", { name: /pipeline review/i }),
    ).not.toHaveAttribute("aria-current");
  });

  it("fullscreen toggle calls onFullscreenChange with the next value", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    const onFullscreenChange = vi.fn();
    renderSidebar(transport, router, {
      fullscreen: false,
      onFullscreenChange,
    });

    const btn = await screen.findByTestId("chats-fullscreen-toggle");
    expect(btn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(btn);
    expect(onFullscreenChange).toHaveBeenCalledWith(true);
  });

  it("fullscreen=true renders the toggle in a pressed state", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router, { fullscreen: true });

    const btn = await screen.findByTestId("chats-fullscreen-toggle");
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });

  it("calls the projects endpoint exactly once on mount", async () => {
    const { transport, request } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);
    await screen.findByText("Revenue Ops");
    expect(request).toHaveBeenCalledTimes(1);
    const arg = request.mock.calls[0]?.[0] as TypedRequest;
    expect(arg.method).toBe("GET");
    expect(arg.path).toBe("/v1/chats/projects");
  });

  it("renders an empty sentinel when search matches nothing", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    await screen.findByText("Revenue Ops");
    fireEvent.change(screen.getByRole("searchbox", { name: /search chats/i }), {
      target: { value: "zzzzzz-no-match" },
    });
    expect(screen.getByTestId("chats-sidebar-empty")).toBeInTheDocument();
  });

  it("conversation-kind routes also drive the active highlight", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter({
      kind: "conversation",
      conversationId: "t-roadmap",
    });
    renderSidebar(transport, router);

    const row = await screen.findByRole("button", { name: /q3 roadmap/i });
    expect(row).toHaveAttribute("aria-current", "page");
  });

  it("collapsed project's threads list is not rendered", async () => {
    const { transport } = makeTransport({});
    const router = makeRouter(null);
    renderSidebar(transport, router);

    const list = await screen.findByTestId("chats-sidebar-projects");
    expect(within(list).queryByText("Pipeline review")).not.toBeInTheDocument();
    expect(within(list).queryByText("Q3 roadmap")).not.toBeInTheDocument();
  });
});
