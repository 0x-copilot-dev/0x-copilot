import type { Transport } from "@0x-copilot/chat-transport";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresenceSignal } from "../presence/presence-signal";
import {
  DeploymentProfileProvider,
  type DeploymentProfile,
} from "../providers/DeploymentProfileProvider";
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
  readonly onOpenSettings?: () => void;
  readonly settingsActive?: boolean;
  readonly topbarLeaf?: string | null;
  /**
   * When supplied, ChatShell is wrapped in a DeploymentProfileProvider so the
   * rail resolves via `destinationsForProfile(profile)`. Omitted = the web
   * host path (no provider → the frozen legacy 12-destination rail).
   */
  readonly profile?: DeploymentProfile;
  readonly children?: React.ReactNode;
}

function mount({
  activeDestination = "home",
  onNavigate = () => {},
  onOpenSettings,
  settingsActive,
  topbarLeaf,
  profile,
  children,
}: MountOptions = {}) {
  const shell = (
    <ChatShell
      transport={stubTransport}
      router={staticRouter()}
      keyValueStore={stubKv}
      presenceSignal={stubPresence}
      activeDestination={activeDestination}
      onNavigate={onNavigate}
      onOpenSettings={onOpenSettings}
      settingsActive={settingsActive}
      topbarLeaf={topbarLeaf ?? null}
    >
      {children}
    </ChatShell>
  );
  return render(
    profile === undefined ? (
      shell
    ) : (
      <DeploymentProfileProvider profile={profile}>
        {shell}
      </DeploymentProfileProvider>
    ),
  );
}

function shellRoot(): HTMLElement {
  // The shell mounts AppRail as a nav with the literal aria-label
  // "Copilot destinations" — walk from there to the [data-component]
  // ancestor without touching the substrate-banned `document` global.
  const rail = screen.getByRole("navigation", {
    name: /copilot destinations/i,
  });
  let el: HTMLElement | null = rail;
  while (el !== null && el.getAttribute("data-component") !== "chat-shell") {
    el = el.parentElement;
  }
  if (el === null) throw new Error("chat-shell not mounted");
  return el;
}

describe("ChatShell", () => {
  it("renders a four-region grid for non-full-bleed destinations and starts with the right rail closed", () => {
    mount({ activeDestination: "home" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-destination", "home");
    // Right rail defaults to closed — Activity / Approvals content is a
    // Wave 5 thread-canvas job; an open empty rail was visual noise.
    expect(shell).toHaveAttribute("data-right-rail-open", "closed");
    // v2 geometry: the rail is 48px wide (was 52), then the 224px context
    // column, main, and a collapsed (0) right column.
    expect(shell).toHaveStyle({
      gridTemplateColumns: "48px 224px 1fr 0",
    });
  });

  it("hides the ContextPanel column when the destination is chats (full-bleed)", () => {
    mount({ activeDestination: "chats" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-destination", "chats");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "48px 1fr 0",
    });
    // The ContextPanel is absent for chats — single source of truth: no
    // double-sidebar.
    expect(screen.queryByRole("complementary", { name: /panel/i })).toBeNull();
  });

  it("renders AppRail, ContextPanel, Topbar (title/subtitle), and RightRail on non-full-bleed destinations", () => {
    mount({ activeDestination: "home" });
    expect(
      screen.getByRole("navigation", { name: /copilot destinations/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: /home panel/i }),
    ).toBeInTheDocument();
    // Right rail starts collapsed; the collapsed-state aside is still
    // present (it owns the edge toggle) but its aria-label carries the
    // "(collapsed)" suffix.
    expect(
      screen.getByRole("complementary", {
        name: "Copilot conversation (collapsed)",
      }),
    ).toBeInTheDocument();
    // v2 topbar is a title/subtitle header (not the old breadcrumb). The
    // title resolves from the destinations registry — "Home" for the legacy
    // web rail's `home` slug.
    const title = screen.getByTestId("topbar-title");
    expect(title).toBeInTheDocument();
    expect(title).toHaveTextContent("Home");
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

  it("toggles the right column open when the edge toggle is clicked", () => {
    mount({ activeDestination: "home" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-right-rail-open", "closed");
    fireEvent.click(screen.getByTestId("right-rail-toggle"));
    expect(shell).toHaveAttribute("data-right-rail-open", "open");
    expect(shell).toHaveStyle({
      gridTemplateColumns: "48px 224px 1fr 380px",
    });
  });

  it("renders a Settings button in the rail foot when onOpenSettings is supplied", () => {
    const onOpenSettings = vi.fn();
    mount({ activeDestination: "home", onOpenSettings });
    const settingsBtn = screen.getByRole("button", { name: "Settings" });
    expect(settingsBtn).toBeInTheDocument();
    expect(settingsBtn).toHaveAttribute("data-rail-action", "settings");
    fireEvent.click(settingsBtn);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("omits the rail Settings button when onOpenSettings is absent", () => {
    mount({ activeDestination: "home" });
    expect(screen.queryByRole("button", { name: "Settings" })).toBeNull();
  });

  it("forwards the topbar leaf as the subtitle when supplied (non-full-bleed destinations)", () => {
    // Full-bleed destinations suppress the shell Topbar (the surface brings
    // its own header), so subtitle forwarding is exercised on a destination
    // that renders it.
    mount({ activeDestination: "home", topbarLeaf: "c-123" });
    expect(screen.getByTestId("topbar-subtitle")).toHaveTextContent("c-123");
  });

  it("suppresses the shell Topbar on full-bleed chats", () => {
    mount({ activeDestination: "chats", topbarLeaf: "c-123" });
    expect(screen.queryByTestId("topbar-title")).toBeNull();
  });

  it("suppresses the shell RightRail on full-bleed chats", () => {
    // ChatScreen owns the right panel on chats; the empty shell rail would
    // be a duplicate. (It still renders on non-full-bleed destinations —
    // covered by "toggles the right column open ...".)
    mount({ activeDestination: "chats" });
    expect(screen.queryByTestId("right-rail-toggle")).toBeNull();
  });

  it("renders Run full-bleed (topbar + context column + right rail suppressed)", () => {
    // `run` is the flagship cockpit — it owns full height (DESIGN-SPEC §1),
    // so it gets the same full-bleed treatment as chats.
    mount({ activeDestination: "run", profile: "single_user_desktop" });
    const shell = shellRoot();
    expect(shell).toHaveAttribute("data-destination", "run");
    expect(shell).toHaveStyle({ gridTemplateColumns: "48px 1fr 0" });
    expect(screen.queryByRole("complementary", { name: /panel/i })).toBeNull();
    expect(screen.queryByTestId("topbar-title")).toBeNull();
    expect(screen.queryByTestId("right-rail-toggle")).toBeNull();
  });

  it("renders the Settings surface full-bleed via settingsActive, regardless of the active destination", () => {
    // Settings is full-height (DESIGN-SPEC §1) but is not a rail destination
    // — it arrives via `settingsActive`. Even opened from a non-full-bleed
    // destination (projects), the shell suppresses the topbar + context
    // column, while the rail keeps `projects` highlighted.
    mount({
      activeDestination: "projects",
      settingsActive: true,
      profile: "single_user_desktop",
    });
    const shell = shellRoot();
    expect(shell).toHaveStyle({ gridTemplateColumns: "48px 1fr 0" });
    expect(screen.queryByRole("complementary", { name: /panel/i })).toBeNull();
    expect(screen.queryByTestId("topbar-title")).toBeNull();
    expect(screen.getByRole("button", { name: "Projects" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders the legacy 12-destination rail when no DeploymentProfile provider is present (web default)", () => {
    mount({ activeDestination: "home" });
    // Legacy-only slugs are present…
    expect(screen.getByRole("button", { name: "Home" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connectors" }),
    ).toBeInTheDocument();
    // …and the Phase-2 solo-only labels are absent.
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Skills" })).toBeNull();
  });

  it("renders the profile-derived 6-destination rail under a single_user_desktop provider", () => {
    mount({ activeDestination: "run", profile: "single_user_desktop" });
    // Solo set: Run, Chats, Projects, Activity, Tools (slug connectors),
    // Skills (slug tools).
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tools" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skills" })).toBeInTheDocument();
    // Legacy-only labels never leak into the solo rail.
    expect(screen.queryByRole("button", { name: "Home" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Connectors" })).toBeNull();
  });

  it("shows the profile-relabelled topbar title (connectors → 'Tools') in the solo view", () => {
    // In the solo view the `connectors` slug is relabelled "Tools"; the shell
    // passes that profile-correct label to the Topbar so rail and topbar
    // agree — without the Topbar becoming profile-aware.
    mount({ activeDestination: "connectors", profile: "single_user_desktop" });
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Tools");
  });

  it("resolves the label for the new `activity` slug without crashing (registry-safe)", () => {
    // `activity` is a Phase-2 addition; the topbar title and context panel
    // label must resolve from the registry rather than render `undefined`.
    mount({ activeDestination: "activity", profile: "single_user_desktop" });
    expect(screen.getByTestId("topbar-title")).toHaveTextContent("Activity");
    expect(
      screen.getByRole("complementary", { name: /activity panel/i }),
    ).toBeInTheDocument();
  });
});
