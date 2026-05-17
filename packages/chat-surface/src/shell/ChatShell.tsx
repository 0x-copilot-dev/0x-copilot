import type { Transport } from "@enterprise-search/chat-transport";
import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { PresenceSignal } from "../presence/presence-signal";
import { KeyValueStoreProvider } from "../providers/KeyValueStoreProvider";
import { PresenceSignalProvider } from "../providers/PresenceSignalProvider";
import { RouterProvider } from "../providers/RouterProvider";
import { TransportProvider } from "../providers/TransportProvider";
import type { Router } from "../routing/router";
import type { KeyValueStore } from "../storage/key-value-store";

import { APP_RAIL_WIDTH, AppRail } from "./AppRail";
import {
  CONTEXT_PANEL_WIDTH,
  ContextPanel,
  type ContextPanelProps,
} from "./ContextPanel";
import { SHELL_DESTINATIONS, type ShellDestinationSlug } from "./destinations";
import { RIGHT_RAIL_WIDTH, RightRail } from "./RightRail";
import { TOPBAR_HEIGHT, Topbar } from "./Topbar";

// Destinations that intentionally skip the 224px context column. Chats is
// the only one for now — the os-css reference (line 596-611) makes the
// chats destination full-bleed because the chat surface brings its own
// thread sidebar. Add destinations here if more want the same treatment.
const FULL_BLEED_DESTINATIONS: ReadonlySet<ShellDestinationSlug> = new Set([
  "chats",
]);

export interface ChatShellProps<TRoute> {
  /** Transport singleton. Made available via context to descendants. */
  readonly transport: Transport;
  /** Substrate-side router (HashRouter on web, native router on desktop). */
  readonly router: Router<TRoute>;
  readonly keyValueStore: KeyValueStore;
  readonly presenceSignal: PresenceSignal;

  /**
   * Active destination, controlled by the host. The shell never derives
   * destination from the route — that mapping lives in the host (App.tsx
   * on web) so the host's route type can carry web-only screens (settings,
   * share, admin-…) without leaking into the shell.
   */
  readonly activeDestination: ShellDestinationSlug;
  /** Click on a rail item. The host translates slug → route. */
  readonly onNavigate: (slug: ShellDestinationSlug) => void;

  /**
   * Optional Settings click handler. When supplied, the AppRail renders
   * a Settings button in its foot section. Settings is intentionally
   * not a destination (it's a per-user/admin screen, not a workspace
   * surface), so it gets its own slot rather than expanding the 11-slug
   * `ShellDestinationSlug` enum.
   */
  readonly onOpenSettings?: () => void;

  /**
   * Optional sub-crumb for the topbar (e.g. conversation id, server id).
   */
  readonly topbarLeaf?: string | null;

  /**
   * Optional per-destination ContextPanel content. The host supplies it
   * — destination panels live next to the destination, not in the shell
   * package. When omitted on non-full-bleed destinations, an empty
   * panel labeled with the destination is rendered. Ignored entirely
   * on full-bleed destinations (chats).
   */
  readonly contextPanel?: ReactNode | ContextPanelProps;

  /** Main column content. */
  readonly children?: ReactNode;
}

export function ChatShell<TRoute>({
  transport,
  router,
  keyValueStore,
  presenceSignal,
  activeDestination,
  onNavigate,
  onOpenSettings,
  topbarLeaf,
  contextPanel,
  children,
}: ChatShellProps<TRoute>): ReactElement {
  return (
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <KeyValueStoreProvider store={keyValueStore}>
          <PresenceSignalProvider signal={presenceSignal}>
            <ShellGrid
              activeDestination={activeDestination}
              onNavigate={onNavigate}
              onOpenSettings={onOpenSettings}
              topbarLeaf={topbarLeaf}
              contextPanel={contextPanel}
            >
              {children}
            </ShellGrid>
          </PresenceSignalProvider>
        </KeyValueStoreProvider>
      </RouterProvider>
    </TransportProvider>
  );
}

interface ShellGridProps {
  readonly activeDestination: ShellDestinationSlug;
  readonly onNavigate: (slug: ShellDestinationSlug) => void;
  readonly onOpenSettings?: () => void;
  readonly topbarLeaf?: string | null;
  readonly contextPanel?: ReactNode | ContextPanelProps;
  readonly children?: ReactNode;
}

function ShellGrid({
  activeDestination,
  onNavigate,
  onOpenSettings,
  topbarLeaf,
  contextPanel,
  children,
}: ShellGridProps): ReactElement {
  // Default to closed: the right rail has no destination-specific content
  // wired yet (Activity / Approvals tabs are a Wave 5 thread-canvas job)
  // so an open empty rail is visual noise. Users open it via the edge
  // toggle when there's something to show.
  const [rightOpen, setRightOpen] = useState(false);
  const fullBleed = FULL_BLEED_DESTINATIONS.has(activeDestination);

  const rightCol = rightOpen ? `${RIGHT_RAIL_WIDTH}px` : "0";
  // Three vs four column layouts. Chats (full-bleed) gets rail + main +
  // right rail; everything else inserts the 224px context column between.
  const gridTemplateColumns = fullBleed
    ? `${APP_RAIL_WIDTH}px 1fr ${rightCol}`
    : `${APP_RAIL_WIDTH}px ${CONTEXT_PANEL_WIDTH}px 1fr ${rightCol}`;

  const outerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    display: "grid",
    gridTemplateColumns,
    gridTemplateRows: "100%",
    boxSizing: "border-box",
  };
  const mainColumnStyle: CSSProperties = {
    display: "grid",
    gridTemplateRows: `${TOPBAR_HEIGHT}px 1fr`,
    minHeight: 0,
    backgroundColor: "var(--color-bg)",
  };
  const mainBodyStyle: CSSProperties = {
    minHeight: 0,
    minWidth: 0,
    overflow: "auto",
  };

  return (
    <div
      data-component="chat-shell"
      data-destination={activeDestination}
      data-right-rail-open={rightOpen ? "open" : "closed"}
      style={outerStyle}
    >
      <AppRail
        activeDestination={activeDestination}
        onNavigate={onNavigate}
        onOpenSettings={onOpenSettings}
      />
      {fullBleed ? null : (
        <ContextPanelSlot
          activeDestination={activeDestination}
          contextPanel={contextPanel}
        />
      )}
      <div style={mainColumnStyle}>
        <Topbar
          activeDestination={activeDestination}
          leaf={topbarLeaf ?? null}
        />
        <div style={mainBodyStyle} data-testid="chat-shell-main">
          {children}
        </div>
      </div>
      <RightRail open={rightOpen} onToggle={() => setRightOpen((v) => !v)} />
    </div>
  );
}

function ContextPanelSlot({
  activeDestination,
  contextPanel,
}: {
  readonly activeDestination: ShellDestinationSlug;
  readonly contextPanel?: ReactNode | ContextPanelProps;
}): ReactElement {
  // If the host passed a fully composed ReactNode (anything that isn't a
  // plain ContextPanelProps shape), render it as-is. Otherwise build a
  // default `<ContextPanel>` from the props bag (or the destination label
  // when nothing was passed). Single source of truth for the empty-state
  // copy and styling — destinations can opt out of building their own
  // shell yet still get a consistent panel.
  if (contextPanel === undefined || contextPanel === null) {
    const label =
      SHELL_DESTINATIONS.find((d) => d.slug === activeDestination)?.label ??
      activeDestination;
    return <ContextPanel title={label} destination={activeDestination} />;
  }
  if (isContextPanelProps(contextPanel)) {
    return (
      <ContextPanel
        {...contextPanel}
        destination={contextPanel.destination ?? activeDestination}
      />
    );
  }
  return <>{contextPanel}</>;
}

function isContextPanelProps(value: unknown): value is ContextPanelProps {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    // React elements have a $$typeof symbol; plain props bags don't.
    !("$$typeof" in (value as object)) &&
    "title" in (value as object) &&
    typeof (value as { title: unknown }).title === "string"
  );
}
