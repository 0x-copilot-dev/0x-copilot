import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";
import { TeamDestination, type Member } from "./TeamDestination";

const CAPS: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

const SESSION: Session = { bearer: null };

function makeTransport(
  handler: (req: TypedRequest) => Promise<unknown>,
): Transport {
  return {
    request: <TRes,>(req: TypedRequest) => handler(req).then((v) => v as TRes),
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => undefined,
    }),
    getSession: () => SESSION,
    capabilities: () => CAPS,
  };
}

function makeRouter(): Router<ArtifactRoute> & {
  readonly calls: ArtifactRoute[];
} {
  const calls: ArtifactRoute[] = [];
  let current: ArtifactRoute = { kind: "workspace", workspaceId: "w0" };
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  return {
    current: () => current,
    navigate: (route: ArtifactRoute) => {
      calls.push(route);
      current = route;
      subscribers.forEach((s) => s(route));
    },
    subscribe: (handler) => {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
    calls,
  };
}

function memberFixture(over: Partial<Member> = {}): Member {
  return {
    id: "m1",
    name: "Sarah Acme",
    email: "sarah@acme.test",
    role: "owner",
    lastActiveIso: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
    workspaceId: "ws_1",
    ...over,
  };
}

function renderWithProviders(
  transport: Transport,
  router: Router<ArtifactRoute>,
) {
  return render(
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <TeamDestination />
      </RouterProvider>
    </TransportProvider>,
  );
}

describe("TeamDestination", () => {
  it("renders a loading skeleton before the transport resolves", () => {
    const transport = makeTransport(() => new Promise(() => undefined));
    const router = makeRouter();
    renderWithProviders(transport, router);
    expect(screen.getByTestId("team-loading")).toBeInTheDocument();
  });

  it("renders a populated table once the transport resolves", async () => {
    const transport = makeTransport(async () => ({
      members: [
        memberFixture({
          id: "m1",
          name: "Sarah Acme",
          email: "sarah@acme.test",
          role: "owner",
        }),
        memberFixture({
          id: "m2",
          name: "Marcus Admin",
          email: "marcus@acme.test",
          role: "admin",
          workspaceId: "ws_1",
        }),
      ],
    }));
    const router = makeRouter();
    renderWithProviders(transport, router);

    const table = await screen.findByTestId("team-table");
    expect(within(table).getByText("Sarah Acme")).toBeInTheDocument();
    expect(within(table).getByText("sarah@acme.test")).toBeInTheDocument();
    expect(within(table).getByText("Owner")).toBeInTheDocument();
    expect(within(table).getByText("Marcus Admin")).toBeInTheDocument();
    expect(within(table).getByText("Admin")).toBeInTheDocument();
  });

  it("renders the empty-state card when the transport returns no members", async () => {
    const transport = makeTransport(async () => ({ members: [] }));
    const router = makeRouter();
    renderWithProviders(transport, router);
    expect(await screen.findByTestId("team-empty")).toBeInTheDocument();
  });

  it("renders an error sentinel when the transport rejects", async () => {
    const transport = makeTransport(async () => {
      throw new Error("network down");
    });
    const router = makeRouter();
    renderWithProviders(transport, router);
    const errorCard = await screen.findByTestId("team-error");
    expect(errorCard).toHaveTextContent("network down");
  });

  it("navigates to the workspace route when a row is clicked", async () => {
    const user = userEvent.setup();
    const transport = makeTransport(async () => ({
      members: [memberFixture({ workspaceId: "ws_42" })],
    }));
    const router = makeRouter();
    renderWithProviders(transport, router);
    const row = await screen.findByRole("button", {
      name: /open workspace for sarah acme/i,
    });
    await user.click(row);
    expect(router.calls).toEqual([{ kind: "workspace", workspaceId: "ws_42" }]);
  });

  it("calls onInvite when the Invite button is clicked", async () => {
    const user = userEvent.setup();
    const transport = makeTransport(async () => ({
      members: [memberFixture()],
    }));
    const router = makeRouter();
    const onInvite = vi.fn();
    render(
      <TransportProvider transport={transport}>
        <RouterProvider router={router}>
          <TeamDestination onInvite={onInvite} />
        </RouterProvider>
      </TransportProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("team-table")).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Invite" }));
    expect(onInvite).toHaveBeenCalledTimes(1);
  });
});
