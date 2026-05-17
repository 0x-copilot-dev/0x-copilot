import type { Transport } from "@enterprise-search/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PresenceSignal } from "../presence/presence-signal";
import type { ArtifactRoute, Router } from "../routing/router";
import type { KeyValueStore } from "../storage/key-value-store";

import { ChatShell } from "./ChatShell";

function staticRouter(route: ArtifactRoute | null): Router<ArtifactRoute> {
  return {
    current(): ArtifactRoute {
      if (route === null) throw new Error("no route");
      return route;
    },
    navigate(): void {
      /* unused */
    },
    subscribe(): () => void {
      return () => {
        /* unused */
      };
    },
  };
}

const stubTransport: Transport = {
  request: () => new Promise(() => {}),
  subscribeServerSentEvents: () => ({ close: () => {} }),
  getSession: () => ({ bearer: null }),
  capabilities: () => ({
    substrate: "web",
    nativeSecretStorage: false,
    fileSystemAccess: false,
    clipboardWrite: true,
    openExternal: false,
  }),
};
const stubKv: KeyValueStore = {
  get: () => null,
  set: () => {},
  keys: () => [],
};
const stubPresence: PresenceSignal = {
  current: () => "visible",
  subscribe: () => () => {},
};

function mount(route: ArtifactRoute | null = null) {
  return render(
    <ChatShell
      transport={stubTransport}
      router={staticRouter(route)}
      keyValueStore={stubKv}
      presenceSignal={stubPresence}
    />,
  );
}

describe("ChatShell", () => {
  it("renders the four-region grid with the right rail open by default", () => {
    mount();
    const shell = screen.getByText(
      (_, el) => el?.getAttribute("data-component") === "chat-shell",
    );
    expect(shell).toHaveAttribute("data-right-rail-open", "open");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "52px 224px 1fr 380px",
    });
  });

  it("hosts AppRail, ContextPanel, Topbar, and RightRail", () => {
    mount();
    expect(
      screen.getByRole("navigation", { name: /atlas destinations/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: /home filters/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "Atlas conversation" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("topbar-breadcrumb")).toBeInTheDocument();
  });

  it("renders the chats destination for chat routes", () => {
    mount({ kind: "chat", conversationId: "c-42" });
    expect(screen.getByTestId("destination-outlet")).toHaveAttribute(
      "data-destination",
      "chats",
    );
  });

  it("maps mcp routes to the connectors destination", () => {
    mount({ kind: "mcp", serverId: "srv-x" });
    expect(screen.getByTestId("destination-outlet")).toHaveAttribute(
      "data-destination",
      "connectors",
    );
  });

  it("suppresses the DestinationOutlet when children are passed", () => {
    render(
      <ChatShell
        transport={stubTransport}
        router={staticRouter(null)}
        keyValueStore={stubKv}
        presenceSignal={stubPresence}
      >
        <div data-testid="custom-child">host-provided content</div>
      </ChatShell>,
    );
    expect(screen.getByTestId("custom-child")).toBeInTheDocument();
    expect(screen.queryByTestId("destination-outlet")).not.toBeInTheDocument();
  });

  it("collapses the right column when the right rail is toggled closed", () => {
    mount();
    const shell = screen.getByText(
      (_, el) => el?.getAttribute("data-component") === "chat-shell",
    );
    expect(shell).toHaveAttribute("data-right-rail-open", "open");
    fireEvent.click(screen.getByTestId("right-rail-toggle"));
    expect(shell).toHaveAttribute("data-right-rail-open", "closed");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "52px 224px 1fr 0",
    });
  });
});
