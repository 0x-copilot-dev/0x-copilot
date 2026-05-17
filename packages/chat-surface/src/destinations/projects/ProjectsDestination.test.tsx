import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type {
  ArtifactRoute,
  NavigateOptions,
  Router,
} from "../../routing/router";

import type { ProjectId } from "@enterprise-search/api-types";

import { ProjectsDestination, type Project } from "./ProjectsDestination";

interface DeferredTransport {
  readonly transport: Transport;
  resolve(response: unknown): void;
  reject(err: Error): void;
  reset(): void;
  readonly calls: ReadonlyArray<TypedRequest>;
}

function makeDeferredTransport(initial?: unknown): DeferredTransport {
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

  if (initial !== undefined) {
    Promise.resolve().then(() => resolver?.(initial));
  }

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

const SAMPLE: ReadonlyArray<Project> = [
  {
    id: "proj-1" as ProjectId,
    name: "Q4 sales push",
    lastActivityAt: new Date(Date.now() - 5 * 60_000).toISOString(),
    chatCount: 12,
    ownerName: "Sarah Chen",
  },
  {
    id: "proj-2" as ProjectId,
    name: "Onboarding redesign",
    lastActivityAt: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    chatCount: 1,
    ownerName: "Marcus Wells",
    ownerAvatarUrl: "https://example.com/marcus.png",
  },
];

function renderProjects(deferred: DeferredTransport, router = makeRouter()) {
  return {
    router,
    ...render(
      <TransportProvider transport={deferred.transport}>
        <RouterProvider router={router}>
          <ProjectsDestination />
        </RouterProvider>
      </TransportProvider>,
    ),
  };
}

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("ProjectsDestination", () => {
  it("renders a skeleton grid while the projects request is pending", () => {
    const deferred = makeDeferredTransport();
    renderProjects(deferred);
    const grid = screen.getByTestId("projects-grid");
    expect(grid).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("projects-skeleton-card")).toHaveLength(6);
    expect(deferred.calls).toHaveLength(1);
    expect(deferred.calls[0]).toEqual({
      method: "GET",
      path: "/v1/projects",
    });
  });

  it("renders project cards when the request resolves with data", async () => {
    const deferred = makeDeferredTransport();
    renderProjects(deferred);
    await act(async () => {
      deferred.resolve({ projects: SAMPLE });
      await Promise.resolve();
    });
    const cards = screen.getAllByTestId("project-card");
    expect(cards).toHaveLength(2);
    expect(screen.getByText("Q4 sales push")).toBeInTheDocument();
    expect(screen.getByText("Onboarding redesign")).toBeInTheDocument();
    expect(screen.getByText("12 chats")).toBeInTheDocument();
    expect(screen.getByText("1 chat")).toBeInTheDocument();
    expect(screen.getByText("SC")).toBeInTheDocument();
  });

  it("clicking a card navigates via the router with a workspace route", async () => {
    const deferred = makeDeferredTransport();
    const { router } = renderProjects(deferred);
    await act(async () => {
      deferred.resolve({ projects: SAMPLE });
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: /q4 sales push/i }));
    expect(router.navigate).toHaveBeenCalledWith({
      kind: "workspace",
      workspaceId: "proj-1",
    });
  });

  it("renders the empty state when the request resolves with no projects", async () => {
    const deferred = makeDeferredTransport();
    renderProjects(deferred);
    await act(async () => {
      deferred.resolve({ projects: [] });
      await Promise.resolve();
    });
    expect(screen.getByTestId("projects-empty")).toBeInTheDocument();
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByTestId("projects-new-trigger")).toBeInTheDocument();
  });

  it("renders the error panel when the request rejects and retries on click", async () => {
    const deferred = makeDeferredTransport();
    renderProjects(deferred);
    await act(async () => {
      deferred.reject(new Error("network down"));
      await Promise.resolve();
    });
    const errorPanel = screen.getByTestId("projects-error");
    expect(errorPanel).toBeInTheDocument();
    expect(screen.getByText("network down")).toBeInTheDocument();

    deferred.reset();
    fireEvent.click(screen.getByTestId("projects-retry"));
    expect(screen.getByTestId("projects-grid")).toHaveAttribute(
      "data-state",
      "loading",
    );
    await act(async () => {
      deferred.resolve({ projects: SAMPLE });
      await Promise.resolve();
    });
    expect(screen.getAllByTestId("project-card")).toHaveLength(2);
  });

  it("creates a new project via Transport and prepends it to the list", async () => {
    const deferred = makeDeferredTransport();
    renderProjects(deferred);
    await act(async () => {
      deferred.resolve({ projects: SAMPLE });
      await Promise.resolve();
    });
    deferred.reset();

    const user = userEvent.setup();
    await user.click(screen.getByTestId("projects-new-trigger"));
    const input = screen.getByTestId("projects-new-input");
    await user.type(input, "New initiative");
    await user.click(screen.getByTestId("projects-new-submit"));

    const created: Project = {
      id: "proj-new" as ProjectId,
      name: "New initiative",
      lastActivityAt: new Date().toISOString(),
      chatCount: 0,
      ownerName: "Sarah Chen",
    };
    await act(async () => {
      deferred.resolve(created);
      await Promise.resolve();
    });
    await flushMicrotasks();

    const cards = screen.getAllByTestId("project-card");
    expect(cards).toHaveLength(3);
    expect(cards[0]!.getAttribute("data-project-id")).toBe("proj-new");

    const lastCall = deferred.calls[deferred.calls.length - 1]!;
    expect(lastCall).toEqual({
      method: "POST",
      path: "/v1/projects",
      body: { name: "New initiative" },
    });
  });
});
