import type { Transport } from "@0x-copilot/chat-transport";
import {
  useRef,
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
import { DEFAULT_SHELL_WIDTH_CLASS } from "./layout";
import { RIGHT_RAIL_WIDTH, RightRail } from "./RightRail";
import { RunActivityBusProvider } from "./runActivityBus";
import { ShellWidthProvider } from "./ShellWidthProvider";
import { TOPBAR_HEIGHT, Topbar } from "./Topbar";
import { useActiveRunCount } from "./useActiveRunCount";
import { usePendingApprovalCount } from "./usePendingApprovalCount";
import { useObservedWidthClass } from "./useContainerWidth";

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

// `FULL_BLEED_DESTINATIONS` names the destinations that own their full width
// outright — `chats` and `run` — so no side column is even considered for them.
//
// It is NOT the whole story any more, and deliberately so. The side columns
// (224px context column + right rail) are now CONTENT-GATED: the shell reserves
// a column only when someone actually put something in it. This set was the
// only gate before, which meant every destination outside it got a 224px column
// whether or not it had content — and no host has ever passed `contextPanel`,
// so Projects / Activity / Tools / Skills each shipped a permanent, hardcoded
// "Nothing here yet." beside their content, plus an edge toggle that opened an
// equally empty right rail. DESIGN-SPEC §1 describes the shell as rail + topbar
// + main; there is no context column in the design at all. Content-gating fixes
// that structurally rather than by growing this list: a destination that gains
// a real panel gets its column back the moment it passes one.
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
   * package. OMITTING IT MEANS NO COLUMN: the shell used to fall back to an
   * empty panel labeled with the destination, which is how four destinations
   * shipped a permanent "Nothing here yet." next to their content. Ignored
   * entirely on full-bleed destinations (chats, run).
   */
  readonly contextPanel?: ReactNode | ContextPanelProps;

  /**
   * Optional right-rail content. Same content gate as `contextPanel`: no
   * content, no rail and no edge toggle. The rail was previously mounted
   * unconditionally off full-bleed destinations with nothing in it, so the
   * toggle's only outcome was revealing a 380px empty state.
   */
  readonly rightRail?: ReactNode;

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
  rightRail,
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
                rightRail={rightRail}
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
  readonly rightRail?: ReactNode;
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
  rightRail,
  railIdentity,
  walletChip,
  children,
}: ShellGridProps): ReactElement {
  // The active-run count is a server projection the shell owns (PRD-12 D1): one
  // hook, fed to the rail's Run badge. No host passes it — deleting the prop
  // makes the desktop "badge never wired" gap structurally impossible.
  const activeRunCount = useActiveRunCount();
  // Cross-conversation parked work, moved here from the Run header's
  // "N waiting" chip — a global count reads correctly on a global surface.
  const pendingApprovalCount = usePendingApprovalCount();
  // PRD-00 FR-0.3 — ONE ResizeObserver for the whole surface, on the shell root,
  // published via context. Descendants read `useShellWidthClass()`; nothing
  // threads a width prop. `wide` until the first observer callback, so the first
  // paint is the historical layout (FR-0.5) and narrowing is one transition.
  const rootRef = useRef<HTMLDivElement | null>(null);
  const widthClass = useObservedWidthClass(rootRef, DEFAULT_SHELL_WIDTH_CLASS);
  // Right rail starts closed. It only exists at all when a host passed content
  // for it (see `showRightRail`), so this is the first-open state of a real
  // rail rather than the old "collapsed empty scaffolding".
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

  // Content gate. A side column is chrome around content; with no content it is
  // just a narrower main column and a label. `!fullBleed` still has the final
  // say so chats/run/Settings stay full width even if a host passes something.
  const showContextPanel = !fullBleed && contextPanel != null;
  const showRightRail = !fullBleed && rightRail != null;

  // Profile-correct label for the active destination (e.g. "Tools"/"Skills"
  // in the solo view; the legacy label on web). `undefined` when the active
  // destination isn't in the rendered list — the Topbar then falls back to its
  // own total slug→label registry, which also covers `run`/`activity`.
  const activeLabel = destinations.find(
    (d) => d.slug === activeDestination,
  )?.label;

  // Built from what is actually rendered, so an absent column costs no track.
  // The old form always emitted four tracks (with a `0` right column when
  // closed), which is why an unfed destination still lost 224px to a panel
  // reading "Nothing here yet."
  //
  // The main track is `minmax(0, 1fr)`, NOT a bare `1fr`. A `1fr` track carries
  // an automatic minimum of `min-content`, so the main column refuses to shrink
  // past whatever its widest unbreakable child needs and pushes the surplus out
  // of the shell instead. On desktop that surplus is not even scrollable —
  // `.desktop-window-frame` is `overflow: hidden` by design (the document must
  // never scroll), so an overflowing row is silently CLIPPED: narrow the window
  // and the topbar's right-hand ⌘K trigger simply disappears off the edge with
  // no way to reach it. `minmax(0, …)` lets the track shrink and hands the
  // decision back to the children, which ellipsize.
  const gridTemplateColumns = [
    `${APP_RAIL_WIDTH}px`,
    ...(showContextPanel ? [`${CONTEXT_PANEL_WIDTH}px`] : []),
    "minmax(0, 1fr)",
    ...(showRightRail ? [rightOpen ? `${RIGHT_RAIL_WIDTH}px` : "0"] : []),
  ].join(" ");

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
    // This column is a grid in its own right, and declaring only ROWS leaves it
    // an implicit `auto` column — which carries the same min-content floor the
    // outer track just gave up. Both declarations are load-bearing and neither
    // is sufficient alone: `minWidth` frees the column's own BOX, this frees the
    // TRACK its children are laid out in. With only the former the box shrinks
    // to the window while the topbar inside stays at min-content and overflows,
    // which is precisely the shape of the original bug.
    gridTemplateColumns: "minmax(0, 1fr)",
    minHeight: 0,
    minWidth: 0,
    backgroundColor: "var(--color-bg)",
  };
  const mainBodyStyle: CSSProperties = {
    minHeight: 0,
    minWidth: 0,
    overflow: "auto",
  };

  return (
    <ShellWidthProvider value={widthClass}>
      <div
        ref={rootRef}
        data-component="chat-shell"
        // PRD-12 D7 — the shell root emits `data-active-destination`, leaving the
        // plainer per-element attribute to mean "a button/section FOR a
        // destination". A shipped web rule (`apps/frontend/src/styles.css`) selects
        // this root by the new name, updated in the same change.
        data-active-destination={activeDestination}
        // PRD-00 D-0.2 — publish the width class as a data attribute so plain CSS
        // in any descendant can respond without a prop. Same pattern as the
        // shipped `data-right-rail-open` below and the `[data-reduce-motion]` gate.
        data-width={widthClass}
        // Absent when there is no rail at all, so "closed" keeps meaning "there is
        // a rail and it is collapsed" rather than doubling as "no rail exists".
        data-right-rail-open={
          showRightRail ? (rightOpen ? "open" : "closed") : undefined
        }
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
          badges={railBadges(activeRunCount, pendingApprovalCount)}
        />
        {showContextPanel ? (
          <ContextPanelSlot
            activeDestination={activeDestination}
            destinationLabel={activeLabel ?? activeDestination}
            contextPanel={contextPanel}
          />
        ) : null}
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
            shell RightRail is suppressed there to avoid a duplicate, un-obvious
            panel — and everywhere else it now needs `rightRail` content to exist
            at all. Mounting it unfed left an edge toggle whose only outcome was a
            380px empty state. */}
        {showRightRail ? (
          <RightRail open={rightOpen} onToggle={() => setRightOpen((v) => !v)}>
            {rightRail}
          </RightRail>
        ) : null}
      </div>
    </ShellWidthProvider>
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

/**
 * Rail badge counts. `AppRail` renders a badge only when the count is > 0 AND
 * that destination is not the active one, so an omitted key and a zero read the
 * same — build the object from whatever is non-zero and let the rail decide.
 */
function railBadges(
  activeRunCount: number,
  pendingApprovalCount: number,
): Partial<Record<ShellDestinationSlug, number>> | undefined {
  const badges: Partial<Record<ShellDestinationSlug, number>> = {};
  if (activeRunCount > 0) badges.run = activeRunCount;
  if (pendingApprovalCount > 0) badges.chats = pendingApprovalCount;
  return Object.keys(badges).length > 0 ? badges : undefined;
}
