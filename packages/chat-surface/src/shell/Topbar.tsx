import { type CSSProperties, type ReactElement, type ReactNode } from "react";

import { CommandPaletteTrigger } from "./CommandPaletteTrigger";
import {
  SHELL_DESTINATIONS,
  SUBLABEL_BY_SLUG,
  destinationsForProfile,
  type ShellDestinationSlug,
} from "./destinations";

// DESIGN-SPEC §1: the topbar is 46px tall (was 44 for the legacy breadcrumb).
const TOPBAR_HEIGHT = 46;

// DESIGN-SPEC §1: the command/search trigger sits at 250px on the right. The
// shared `CommandPaletteTrigger` ships `minWidth: 200` inline; per FR-2.16 we
// size it here via its `className` prop rather than editing the shared default.
// The component only declares `min-width` inline (never `width`), so a class
// rule setting `width` wins without a specificity fight. `flex: none` stops it
// shrinking when a long title competes for the row.
const TRIGGER_CLASS = "cs-topbar-cmd-trigger";
const TRIGGER_WIDTH_CSS = `.${TRIGGER_CLASS}{width:250px;flex:none;}`;

// Registry-resolved titles for the FULL widened slug union.
//
// `destinations.ts` is the single source of truth for slug↔label — the topbar
// never hard-codes one. But its `DESTINATION_REGISTRY` isn't exported, so we
// rebuild an exhaustive slug→label lookup from the two exported views:
//   - the `team` view is the only derived list that includes the Phase-2
//     slugs `run`/`activity`/`members`/`billing`;
//   - the legacy `SHELL_DESTINATIONS` view then overrides the 12 stable slugs
//     so the web topbar stays byte-identical (`connectors` → "Connectors",
//     `tools` → "Tools", not the solo relabels).
// Every slug in `ShellDestinationSlug` appears in one of the two, so the record
// is total (no `undefined` for any slug).
const TITLE_BY_SLUG: Record<ShellDestinationSlug, string> = (() => {
  const map = {} as Record<ShellDestinationSlug, string>;
  for (const d of destinationsForProfile("team")) map[d.slug] = d.label;
  for (const d of SHELL_DESTINATIONS) map[d.slug] = d.label;
  return map;
})();

export interface TopbarProps {
  /** The destination the host considers active. Resolves the title from the
   *  destinations registry unless `title` is supplied. */
  readonly activeDestination: ShellDestinationSlug;
  /**
   * Optional title override. When omitted the title is resolved from the
   * destinations registry (canonical/legacy label). Hosts that know the
   * deployment profile (e.g. `ChatShell` in solo mode) can pass the
   * profile-relabelled label ("Tools"/"Skills") so the topbar matches the
   * rail — without making this component profile-aware.
   */
  readonly title?: string;
  /**
   * Optional sub-line (e.g. conversation id, run id, server id). Feeds the
   * subtitle slot; an empty string, em-dash, `null`, or `undefined` renders
   * NO subtitle (the topbar shows the title alone).
   */
  readonly leaf?: string | null;
  /**
   * Opens the command palette. Wired to the right-aligned search trigger.
   * Phase 2 leaves this a deferred no-op — Phase 6A supplies the real
   * palette-open behaviour and the ⌘K hotkey.
   */
  readonly onOpenCommandPalette?: () => void;
  /**
   * Optional host-injected chip rendered between the title group and the ⌘K
   * command trigger (FTUE P4 wallet chip). The package stays substrate-agnostic
   * — the host owns the port-fed content. Additive: when absent the topbar row
   * is byte-identical to before (no wrapper node, no extra flex gap).
   */
  readonly walletChip?: ReactNode;
}

function resolveSubtitle(
  leaf: string | null | undefined,
  fallback: string | undefined,
): string | null {
  // A run/conversation leaf (an explicit sub-crumb) wins; otherwise fall back
  // to the destination's registry sublabel (PRD-09 D5). Never hard-code a
  // string in the topbar — the sublabel lives in `destinations.ts`.
  if (leaf !== undefined && leaf !== null && leaf !== "" && leaf !== "—") {
    return leaf;
  }
  return fallback ?? null;
}

export function Topbar({
  activeDestination,
  title,
  leaf,
  onOpenCommandPalette = () => {},
  walletChip,
}: TopbarProps): ReactElement {
  const resolvedTitle = title ?? TITLE_BY_SLUG[activeDestination];
  const subtitle = resolveSubtitle(leaf, SUBLABEL_BY_SLUG[activeDestination]);

  // Design `.topbar { height:46px; gap:12px; padding:0 18px }` (copilot.css:388-397).
  const barStyle: CSSProperties = {
    height: TOPBAR_HEIGHT,
    minHeight: TOPBAR_HEIGHT,
    backgroundColor: "var(--color-bg)",
    borderBottom: "1px solid var(--color-border)",
    color: "var(--color-text)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    padding: "0 18px",
    boxSizing: "border-box",
  };
  // Left cluster: title and subtitle share ONE baseline row (design
  // `.tb-title { display:flex; align-items:baseline; gap:9px }`,
  // copilot.css:398-403), not a stacked column. `minWidth: 0` lets a long
  // title/subtitle ellipsize instead of shoving the command trigger off the row.
  const leadStyle: CSSProperties = {
    display: "flex",
    alignItems: "baseline",
    gap: 9,
    minWidth: 0,
    flex: 1,
  };
  const titleStyle: CSSProperties = {
    fontFamily: "var(--font-display)",
    // Design `.tb-title h1 { font-size:13.5px }` — the sans ladder has no 13.5px
    // rung and cross-cutting rule 1 forbids minting one, so the title keeps
    // `--font-size-sm`; the residual 0.5px is a recorded `expectDivergence` (D5).
    fontSize: "var(--font-size-sm)",
    fontWeight: "var(--font-weight-semibold)" as CSSProperties["fontWeight"],
    // Design heads carry the display face's tight tracking (`h1,h2,h3,h4 {
    // letter-spacing:-0.01em }`, copilot.css:112-117); the live title is a
    // <span>, so it does not inherit that rule and must re-declare it. -0.01em is
    // font-size-relative, so it tracks the title at whatever rung it renders (no
    // hard-coded px, no token needed) and matches the design's -0.135px within
    // the comparator's 0.5px band.
    letterSpacing: "-0.01em",
    lineHeight: 1.2,
    color: "var(--color-text)",
    // The design title is a semantic <h1> (copilot.css `.tb-title h1`); render
    // the same tag here so the destination title is a real page heading. The UA
    // <h1> ships a block margin the design's base reset zeroes — restate it so
    // the heading contributes no vertical margin to the baseline row.
    margin: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subtitleStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)", // 11.2px ≈ DESIGN-SPEC §1 11.5px
    lineHeight: 1.2,
    // Design `.tb-title .sub { color:var(--mut2) }` #64646d = the existing
    // `--color-text-subtle` token (styles.css:178), NOT `--color-text-muted`
    // #98989f (D5). The token already exists; the call site picked wrong.
    color: "var(--color-text-subtle)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };

  return (
    <header style={barStyle} data-component="topbar">
      {/* display:none <style>: sizes the shared trigger to 250px via its
          className without touching the shared component. */}
      <style>{TRIGGER_WIDTH_CSS}</style>
      <div style={leadStyle} data-testid="topbar-title-group">
        <h1 style={titleStyle} data-testid="topbar-title">
          {resolvedTitle}
        </h1>
        {subtitle !== null ? (
          <span style={subtitleStyle} data-testid="topbar-subtitle">
            {subtitle}
          </span>
        ) : null}
      </div>
      {/* Additive FTUE P4 slot: the host-injected wallet chip sits between the
          title group and the command trigger. Rendered ONLY when supplied, so
          the wrapper (and its flex gap) never touches the byte-identical
          no-chip layout. */}
      {walletChip !== undefined && walletChip !== null ? (
        <div
          style={{ display: "flex", alignItems: "center", flex: "none" }}
          data-testid="topbar-wallet-chip"
        >
          {walletChip}
        </div>
      ) : null}
      <CommandPaletteTrigger
        className={TRIGGER_CLASS}
        onOpen={onOpenCommandPalette}
      />
    </header>
  );
}

export { TOPBAR_HEIGHT };
