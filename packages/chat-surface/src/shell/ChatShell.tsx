import type { Transport } from "@0x-copilot/chat-transport";
import {
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { ShellHostBinding } from "../contract/shellBinding";
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
import { RunActivityBusProvider } from "./runActivityBus";
import { TOPBAR_HEIGHT, Topbar } from "./Topbar";
import { useActiveRunCount } from "./useActiveRunCount";

// PRD-09 D5 — the two shell decisions are INDEPENDENT, matching the design:
// `showTopbar = dest !== "workspace" && dest !== "settings"` (copilot-app.jsx:739),
// while NO destination in the mock has a context column or right rail. The old
// single `fullBleed` conflated "no topbar" with "no side columns".
//
// `SUPPRESS_TOPBAR` — destinations that hide the shell topbar: only `run` (the
// flagship cockpit owns its own header) plus Settings via the `settingsActive`
// flag (Settings is not a rail destination — it opens from the rail foot). Chats
// is NOT here: it gains a topbar (title "Chats" + subtitle + ⌘K), matching the
// design. PRD-12 consumes this set and only adds "web passes settingsActive".
const SUPPRESS_TOPBAR: ReadonlySet<ShellDestinationSlug> = new Set(["run"]);

// `FULL_BLEED_DESTINATIONS` now governs ONLY the side columns (224px context
// column + right rail): `chats` and `run` render without them, which is what the
// mock shows. Chats gains a topbar AND no side columns — the design's exact split.
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
   * TOTAL host binding for the shell's host-owned capabilities (PRD-03 Move 2):
   * `railIdentity` (rail-foot avatar name), `walletChip` (FTUE topbar chip),
   * `topbarLeaf` (topbar sub-crumb) and `settingsActive` (the Settings surface
   * is full-height full-bleed while active). Every field is REQUIRED and never
   * `undefined` — a host that omits one fails to compile, and an opt-out is a
   * literal `null` in the diff. This replaces the four discrete optional props
   * that let capabilities ship dark when a host silently declined them.
   *
   * The rail Run-badge count is deliberately NOT in the binding — PRD-12 owns
   * its data source end to end via `useActiveRunCount` (C1), so no host feeds it.
   */
  readonly binding: ShellHostBinding;

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

  // NOTE: there is deliberately NO host prop for the rail Run-badge count
  // (PRD-12 D1). It is a server projection the shell owns end to end via
  // `useActiveRunCount` — a host prop would re-open the drift door where the
  // desktop rail silently shipped without a count. `AppRail.badges` stays a pure
  // view prop; the shell is its only feeder.

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
  binding,
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
            {/* One run-activity bus for the whole shell subtree (PRD-12 D1),
                mounted OUTSIDE `ShellGrid` so the rail (subscriber, via
                `useActiveRunCount`) and the Run cockpit in `children`
                (publisher, via `useRunSession`) share the same instance. */}
            <RunActivityBusProvider>
              <ShellGrid
                activeDestination={activeDestination}
                destinations={railDestinations}
                onNavigate={onNavigate}
                onOpenSettings={onOpenSettings}
                onOpenCommandPalette={onOpenCommandPalette}
                settingsActive={binding.settingsActive}
                topbarLeaf={binding.topbarLeaf}
                contextPanel={contextPanel}
                railIdentity={binding.railIdentity}
                walletChip={binding.walletChip}
              >
                {children}
              </ShellGrid>
            </RunActivityBusProvider>
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
  // PRD-03 carries the raw display name; PRD-12's AppRail takes `{ displayName }`
  // and derives the glyph/title itself. `null` = neutral glyph.
  readonly railIdentity: { readonly displayName: string } | null;
  readonly walletChip: ReactNode | null;
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
  railIdentity,
  walletChip,
  children,
}: ShellGridProps): ReactElement {
  // The active-run count is a server projection the shell owns (PRD-12 D1): one
  // hook, fed to the rail's Run badge. No host passes it — deleting the prop
  // makes the desktop "badge never wired" gap structurally impossible.
  const activeRunCount = useActiveRunCount();
  // Default to closed: the right rail has no destination-specific content
  // wired yet (Activity / Approvals tabs are a Wave 5 thread-canvas job)
  // so an open empty rail is visual noise. Users open it via the edge
  // toggle when there's something to show.
  const [rightOpen, setRightOpen] = useState(false);
  // PRD-09 D5 — two independent decisions:
  //  * `suppressTopbar` — hide the shell topbar (run cockpit + Settings only).
  //  * `fullBleed` — drop the side columns (chats + run + Settings).
  // Chats suppresses NEITHER the topbar (it gets one) but IS full-bleed (no side
  // columns), exactly as the design shows.
  const suppressTopbar =
    settingsActive || SUPPRESS_TOPBAR.has(activeDestination);
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
    // The topbar row is reserved unless the destination suppresses it (run
    // cockpit + Settings own their own header). Chats keeps the row (PRD-09 D5).
    gridTemplateRows: suppressTopbar ? "100%" : `${TOPBAR_HEIGHT}px 1fr`,
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
      // PRD-12 D7 — the shell root emits `data-active-destination`, leaving the
      // plainer per-element attribute to mean "a button/section FOR a
      // destination". A shipped web rule (`apps/frontend/src/styles.css`) selects
      // this root by the new name, updated in the same change.
      data-active-destination={activeDestination}
      data-right-rail-open={rightOpen ? "open" : "closed"}
      style={outerStyle}
    >
      <AppRail
        activeDestination={activeDestination}
        destinations={destinations}
        onNavigate={onNavigate}
        onOpenSettings={onOpenSettings}
        settingsActive={settingsActive}
        // AppRail takes the raw display name and derives the glyph/title itself
        // (PRD-12 D5). `null` → the neutral glyph.
        identity={railIdentity ?? undefined}
        badges={activeRunCount > 0 ? { run: activeRunCount } : undefined}
      />
      {fullBleed ? null : (
        <ContextPanelSlot
          activeDestination={activeDestination}
          destinationLabel={activeLabel ?? activeDestination}
          contextPanel={contextPanel}
        />
      )}
      <div style={mainColumnStyle}>
        {suppressTopbar ? null : (
          <Topbar
            activeDestination={activeDestination}
            title={activeLabel}
            leaf={topbarLeaf ?? null}
            onOpenCommandPalette={onOpenCommandPalette}
            walletChip={walletChip}
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
