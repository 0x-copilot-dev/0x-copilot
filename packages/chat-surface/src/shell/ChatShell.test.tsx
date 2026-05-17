import type { Transport } from "@enterprise-search/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresenceSignal } from "../presence/presence-signal";
import type { ArtifactRoute, Router } from "../routing/router";
import type { KeyValueStore } from "../storage/key-value-store";

import { ChatShell } from "./ChatShell";
import type { ShellDestinationSlug } from "./destinations";

function staticRouter(): Router<ArtifactRoute> {
  return {
    current(): ArtifactRoute {
      // ChatShell does not consult the router for destination anymore —
      // a static stub is enough for the descendants that still pull from
      // RouterProvider (transport/router are just provided as context).
      throw new Error("no route");
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

interface MountOptions {
  readonly activeDestination?: ShellDestinationSlug;
  readonly onNavigate?: (slug: ShellDestinationSlug) => void;
  readonly topbarLeaf?: string | null;
  readonly children?: React.ReactNode;
}

function mount({
  activeDestination = "home",
  onNavigate = () => {},
  topbarLeaf,
  children,
}: MountOptions = {}) {
  return render(
    <ChatShell
      transport={stubTransport}
      router={staticRouter()}
      keyValueStore={stubKv}
      presenceSignal={stubPresence}
      activeDestination={activeDestination}
      onNavigate={onNavigate}
      topbarLeaf={topbarLeaf ?? null}
    >
      {children}
    </ChatShell>,
  );
}

function shellRoot(): HTMLElement {
  // The shell mounts AppRail as a nav with the literal aria-label
  // "Atlas destinations" — walk from there to the [data-component]
  // ancestor without touching the substrate-banned `document` global.
  const rail = screen.getByRole("navigation", {
    name: /atlas destinations/i,
  });
  let el: HTMLElement | null = rail;
  while (el !== null && el.getAttribute("data-component") !== "chat-shell") {
    el = el.parentElement;
  }
  if (el === null) throw new Error("chat-shell not mounted");
  return el;
}

describe("ChatShell", () => {
  it("renders a four-region grid for non-chats destinations", () => {
    mount({ activeDestination: "home" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-destination", "home");
    expect(shell).toHaveAttribute("data-right-rail-open", "open");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "52px 224px 1fr 380px",
    });
  });

  it("hides the ContextPanel column when the destination is chats (full-bleed)", () => {
    mount({ activeDestination: "chats" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-destination", "chats");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "52px 1fr 380px",
    });
    // The ContextPanel is absent for chats — single source of truth: no
    // double-sidebar.
    expect(screen.queryByRole("complementary", { name: /panel/i })).toBeNull();
  });

  it("renders AppRail, ContextPanel, Topbar, and RightRail on non-chats destinations", () => {
    mount({ activeDestination: "home" });
    expect(
      screen.getByRole("navigation", { name: /atlas destinations/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: /home panel/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "Atlas conversation" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("topbar-breadcrumb")).toBeInTheDocument();
  });

  it("clicking a rail item bubbles the destination slug to onNavigate", () => {
    const onNavigate = vi.fn<(slug: ShellDestinationSlug) => void>();
    mount({ activeDestination: "home", onNavigate });
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    expect(onNavigate).toHaveBeenCalledWith("chats");
  });

  it("renders the host-provided body inside the main column", () => {
    mount({
      activeDestination: "home",
      children: <div data-testid="custom-body">host content</div>,
    });
    expect(screen.getByTestId("custom-body")).toBeInTheDocument();
  });

  it("collapses the right column when the right rail is toggled closed", () => {
    mount({ activeDestination: "home" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-right-rail-open", "open");
    fireEvent.click(screen.getByTestId("right-rail-toggle"));
    expect(shell).toHaveAttribute("data-right-rail-open", "closed");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "52px 224px 1fr 0",
    });
  });

  it("forwards the topbar leaf when supplied", () => {
    mount({ activeDestination: "chats", topbarLeaf: "c-123" });
    expect(screen.getByTestId("topbar-breadcrumb-leaf")).toHaveTextContent(
      "c-123",
    );
  });
});
