import type { Transport } from "@0x-copilot/chat-transport";
import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { PresenceSignal } from "../presence/presence-signal";
import {
  useOptionalDeploymentProfile,
  type DeploymentProfile,
} from "../providers/DeploymentProfileProvider";
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
import {
  SHELL_DESTINATIONS,
  destinationsForProfile,
  type ShellDestination,
  type ShellDestinationSlug,
} from "./destinations";
import { RIGHT_RAIL_WIDTH, RightRail } from "./RightRail";
import { TOPBAR_HEIGHT, Topbar } from "./Topbar";

// Destinations that intentionally skip the 224px context column AND suppress
// the shell topbar — the surface owns full height (DESIGN-SPEC §1). `chats`
// (its ChatScreen brings its own thread sidebar + header) and `run` (the
// flagship cockpit) both render full-bleed. Settings is likewise full-height
// but is NOT a rail destination (it opens from the rail foot via
// `onOpenSettings`), so it arrives through the `settingsActive` flag rather
// than this slug set.
const FULL_BLEED_DESTINATIONS: ReadonlySet<ShellDestinationSlug> = new Set([
  "chats",
  "run",
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
   * Opens the ⌘K command palette. Wired to the topbar's single
   * `CommandPaletteTrigger` (the one search affordance); the host owns the
   * palette open-state. When omitted the trigger is an inert no-op — but hosts
   * must supply it (else they'd add a second, competing trigger, the exact
   * duplicate this prop removes).
   */
  readonly onOpenCommandPalette?: () => void;

  /**
   * When `true` the shell renders full-bleed (topbar + context column +
   * right rail suppressed) regardless of `activeDestination` — for the
   * Settings surface, which is full-height (DESIGN-SPEC §1) but is not a rail
   * destination (it opens from the rail foot via `onOpenSettings`). The rail
   * still highlights `activeDestination`, mirroring the web host's behaviour
   * of keeping the last destination active while Settings is open.
   */
  readonly settingsActive?: boolean;

  /**
   * Optional sub-crumb for the topbar (e.g. conversation id, server id).
   */
  readonly topbarLeaf?: string | null;

  /**
   * Optional explicit rail destinations. When supplied, this list is rendered
   * as-is (host passthrough). When omitted, the shell resolves the list from
   * the DeploymentProfile port: `destinationsForProfile(profile)` when a
   * provider is present, else the legacy `SHELL_DESTINATIONS` (the frozen web
   * rail — so a web host with no provider stays byte-identical).
   */
  readonly destinations?: readonly ShellDestination[];

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
  onOpenCommandPalette,
  settingsActive,
  topbarLeaf,
  destinations,
  contextPanel,
  children,
}: ChatShellProps<TRoute>): ReactElement {
  const profile = useOptionalDeploymentProfile();
  // Resolve the rail destination list ONCE: an explicit `destinations` prop
  // wins (host passthrough), else the profile-derived view when a provider is
  // present, else the frozen legacy list (web-safe default). The relabelled
  // profile labels ("Tools"/"Skills") flow from here into both the rail and
  // the topbar title, so the two never disagree.
  const railDestinations =
    destinations ??
    (profile !== null ? destinationsForProfile(profile) : SHELL_DESTINATIONS);

  return (
    <TransportProvider transport={transport}>
      <RouterProvider router={router}>
        <KeyValueStoreProvider store={keyValueStore}>
          <PresenceSignalProvider signal={presenceSignal}>
            <ShellGrid
              activeDestination={activeDestination}
              destinations={railDestinations}
              onNavigate={onNavigate}
              onOpenSettings={onOpenSettings}
              onOpenCommandPalette={onOpenCommandPalette}
              settingsActive={settingsActive ?? false}
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
  readonly destinations: readonly ShellDestination[];
  readonly onNavigate: (slug: ShellDestinationSlug) => void;
  readonly onOpenSettings?: () => void;
  readonly onOpenCommandPalette?: () => void;
  readonly settingsActive: boolean;
  readonly topbarLeaf?: string | null;
  readonly contextPanel?: ReactNode | ContextPanelProps;
  readonly children?: ReactNode;
}

function ShellGrid({
  activeDestination,
  destinations,
  onNavigate,
  onOpenSettings,
  onOpenCommandPalette,
  settingsActive,
  topbarLeaf,
  contextPanel,
  children,
}: ShellGridProps): ReactElement {
  // Default to closed: the right rail has no destination-specific content
  // wired yet (Activity / Approvals tabs are a Wave 5 thread-canvas job)
  // so an open empty rail is visual noise. Users open it via the edge
  // toggle when there's something to show.
  const [rightOpen, setRightOpen] = useState(false);
  // Full-bleed = the surface owns full height: chats/run by slug, plus the
  // Settings surface via the flag. Topbar + context column + right rail are
  // all suppressed in that state.
  const fullBleed =
    settingsActive || FULL_BLEED_DESTINATIONS.has(activeDestination);

  // Profile-correct label for the active destination (e.g. "Tools"/"Skills"
  // in the solo view; the legacy label on web). `undefined` when the active
  // destination isn't in the rendered list — the Topbar then falls back to its
  // own total slug→label registry, which also covers `run`/`activity`.
  const activeLabel = destinations.find(
    (d) => d.slug === activeDestination,
  )?.label;

  const rightCol = rightOpen ? `${RIGHT_RAIL_WIDTH}px` : "0";
  // Three vs four column layouts. Full-bleed surfaces get rail + main +
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
    // Full-bleed surfaces bring their own top bar via the main content
    // (ChatScreen's own header; the Run cockpit / Settings own full height),
    // so the shell Topbar is suppressed there to avoid a duplicated bar + the
    // "<destination> / —" placeholder row.
    gridTemplateRows: fullBleed ? "100%" : `${TOPBAR_HEIGHT}px 1fr`,
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
        destinations={destinations}
        onNavigate={onNavigate}
        onOpenSettings={onOpenSettings}
      />
      {fullBleed ? null : (
        <ContextPanelSlot
          activeDestination={activeDestination}
          destinationLabel={activeLabel ?? activeDestination}
          contextPanel={contextPanel}
        />
      )}
      <div style={mainColumnStyle}>
        {fullBleed ? null : (
          <Topbar
            activeDestination={activeDestination}
            title={activeLabel}
            leaf={topbarLeaf ?? null}
            onOpenCommandPalette={onOpenCommandPalette}
          />
        )}
        <div style={mainBodyStyle} data-testid="chat-shell-main">
          {children}
        </div>
      </div>
      {/* Full-bleed surfaces own their right panel via the main content
          (ChatScreen's workspace pane; the Run cockpit's right rail), so the
          shell RightRail — empty scaffolding until Activity/Approvals is wired
          in the canvas wave — is suppressed there to avoid a duplicate,
          un-obvious panel. */}
      {fullBleed ? null : (
        <RightRail open={rightOpen} onToggle={() => setRightOpen((v) => !v)} />
      )}
    </div>
  );
}

function ContextPanelSlot({
  activeDestination,
  destinationLabel,
  contextPanel,
}: {
  readonly activeDestination: ShellDestinationSlug;
  readonly destinationLabel: string;
  readonly contextPanel?: ReactNode | ContextPanelProps;
}): ReactElement {
  // If the host passed a fully composed ReactNode (anything that isn't a
  // plain ContextPanelProps shape), render it as-is. Otherwise build a
  // default `<ContextPanel>` from the props bag (or the destination label
  // when nothing was passed). Single source of truth for the empty-state
  // copy and styling — destinations can opt out of building their own
  // shell yet still get a consistent panel. The label is resolved by the
  // grid from the profile-aware list, so a relabelled destination
  // (connectors → "Tools") reads correctly here too.
  if (contextPanel === undefined || contextPanel === null) {
    return (
      <ContextPanel title={destinationLabel} destination={activeDestination} />
    );
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
