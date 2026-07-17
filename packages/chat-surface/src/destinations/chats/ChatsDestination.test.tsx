import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RouterProvider } from "../../providers/RouterProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { ArtifactRoute, Router } from "../../routing/router";

import { ChatsDestination } from "./ChatsDestination";

function makeTransport(): Transport {
  return {
    async request<TRes>(_req: TypedRequest): Promise<TRes> {
      return { projects: [] } as unknown as TRes;
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
}

function makeRouter(): Router<ArtifactRoute> {
  let current: ArtifactRoute | null = null;
  const subscribers = new Set<(r: ArtifactRoute) => void>();
  return {
    current(): ArtifactRoute {
      if (current === null) throw new Error("no route");
      return current;
    },
    navigate: vi.fn((r: ArtifactRoute) => {
      current = r;
      for (const s of subscribers) s(r);
    }),
    subscribe(handler) {
      subscribers.add(handler);
      return () => subscribers.delete(handler);
    },
  };
}

function renderDestination(): void {
  render(
    <TransportProvider transport={makeTransport()}>
      <RouterProvider router={makeRouter()}>
        <ChatsDestination />
      </RouterProvider>
    </TransportProvider>,
  );
}

describe("ChatsDestination", () => {
  it("renders the chats sidebar alongside a thread-canvas placeholder", () => {
    renderDestination();
    expect(
      screen.getByRole("complementary", { name: /chats sidebar/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("thread-canvas-placeholder")).toBeInTheDocument();
  });

  it("toggling fullscreen from the sidebar updates the destination's grid", () => {
    renderDestination();
    const root = screen
      .getByTestId("thread-canvas-placeholder")
      .closest("[data-component='chats-destination']");
    expect(root).not.toBeNull();
    expect(root).toHaveAttribute("data-fullscreen", "off");

    fireEvent.click(screen.getByTestId("chats-fullscreen-toggle"));
    expect(root).toHaveAttribute("data-fullscreen", "on");
  });
});
