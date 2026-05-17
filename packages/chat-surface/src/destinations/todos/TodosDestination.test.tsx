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

import type { TodoId } from "@enterprise-search/api-types";

import { TodosDestination, type TodosPayload } from "./TodosDestination";

interface DeferredController {
  readonly transport: Transport;
  readonly calls: Array<TypedRequest>;
  resolveGet(payload: TodosPayload): void;
  rejectGet(error: unknown): void;
  resolveLastPatch(value?: unknown): void;
  rejectLastPatch(error: unknown): void;
}

function makeDeferredTransport(): DeferredController {
  const calls: Array<TypedRequest> = [];
  let resolveGet: (value: TodosPayload) => void = () => undefined;
  let rejectGet: (error: unknown) => void = () => undefined;
  const patchResolvers: Array<{
    resolve: (value: unknown) => void;
    reject: (error: unknown) => void;
  }> = [];

  const transport: Transport = {
    request<TRes>(req: TypedRequest): Promise<TRes> {
      calls.push(req);
      if (req.method === "GET") {
        return new Promise<TRes>((res, rej) => {
          resolveGet = res as unknown as (value: TodosPayload) => void;
          rejectGet = rej as (error: unknown) => void;
        });
      }
      return new Promise<TRes>((res, rej) => {
        patchResolvers.push({
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
    resolveLastPatch(value) {
      const entry = patchResolvers.shift();
      if (entry === undefined) throw new Error("no pending PATCH");
      entry.resolve(value);
    },
    rejectLastPatch(error) {
      const entry = patchResolvers.shift();
      if (entry === undefined) throw new Error("no pending PATCH");
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

function renderTodos(
  transport: Transport,
  router: Router<ArtifactRoute> = makeRouter(),
): void {
  render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <TodosDestination />
      </RouterProvider>
    </TransportProvider>,
  );
}

const PAYLOAD: TodosPayload = {
  todos: [
    {
      id: "todo_001" as TodoId,
      title: "Draft renewal narrative for Globex",
      completed: false,
      dueAt: "2099-12-31T00:00:00.000Z",
      source: "from run · rn_88",
    },
    {
      id: "todo_002" as TodoId,
      title: "Schedule Atlas onboarding",
      completed: false,
      route: { kind: "workspace", workspaceId: "wsp_acme" },
    },
  ],
};

describe("TodosDestination", () => {
  it("renders skeleton rows while the todos request is in flight", () => {
    const controller = makeDeferredTransport();
    renderTodos(controller.transport);

    const section = screen.getByRole("region", { name: /todos destination/i });
    expect(section).toHaveAttribute("data-state", "loading");
    expect(screen.getAllByTestId("todos-skeleton-row")).toHaveLength(4);
  });

  it("renders populated rows and toggles a todo optimistically", async () => {
    const controller = makeDeferredTransport();
    const router = makeRouter();
    renderTodos(controller.transport, router);

    controller.resolveGet(PAYLOAD);

    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /todos destination/i }),
      ).toHaveAttribute("data-state", "ready"),
    );

    expect(screen.getAllByTestId("todo-row")).toHaveLength(2);

    const [firstOpen, secondOpen] = screen.getAllByTestId("todo-row-open");
    fireEvent.click(firstOpen!);
    expect(router.navigate).toHaveBeenLastCalledWith({
      kind: "workspace",
      workspaceId: "todo_001",
    });
    fireEvent.click(secondOpen!);
    expect(router.navigate).toHaveBeenLastCalledWith({
      kind: "workspace",
      workspaceId: "wsp_acme",
    });

    const firstToggle = screen.getAllByTestId("todo-row-toggle")[0]!;
    fireEvent.click(firstToggle);
    const firstRow = screen.getAllByTestId("todo-row")[0]!;
    expect(firstRow).toHaveAttribute("data-completed", "true");

    controller.resolveLastPatch(undefined);
    await waitFor(() => {
      expect(firstRow).toHaveAttribute("data-completed", "true");
    });

    expect(
      controller.calls.some(
        (c) =>
          c.method === "PATCH" &&
          c.path === "/v1/todos/todo_001" &&
          (c.body as { completed: boolean }).completed === true,
      ),
    ).toBe(true);
  });

  it("renders the empty panel when no todos match the active filter", async () => {
    const controller = makeDeferredTransport();
    renderTodos(controller.transport);

    controller.resolveGet({ todos: [] });

    await waitFor(() => {
      expect(screen.getByTestId("todos-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("todos-empty")).toHaveTextContent(
      /nothing open/i,
    );
  });

  it("renders an error panel and re-fetches when Retry is clicked", async () => {
    let call = 0;
    let rejectFn: (error: unknown) => void = () => undefined;
    let resolveFn: (value: TodosPayload) => void = () => undefined;
    const transport: Transport = {
      request<TRes>(_req: TypedRequest): Promise<TRes> {
        call += 1;
        return new Promise<TRes>((res, rej) => {
          if (call === 1) {
            rejectFn = rej as (error: unknown) => void;
          } else {
            resolveFn = res as unknown as (value: TodosPayload) => void;
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

    renderTodos(transport);
    rejectFn(new Error("conflict"));

    const alert = await screen.findByTestId("todos-error");
    expect(alert).toHaveTextContent(/conflict/i);

    fireEvent.click(screen.getByTestId("todos-retry"));
    expect(call).toBe(2);
    resolveFn(PAYLOAD);

    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: /todos destination/i }),
      ).toHaveAttribute("data-state", "ready"),
    );
  });
});
