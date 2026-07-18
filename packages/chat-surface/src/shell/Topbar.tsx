import { type CSSProperties, type ReactElement } from "react";

import { CommandPaletteTrigger } from "./CommandPaletteTrigger";
import {
  SHELL_DESTINATIONS,
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
}

function resolveSubtitle(leaf: string | null | undefined): string | null {
  if (leaf === undefined || leaf === null || leaf === "" || leaf === "—") {
    return null;
  }
  return leaf;
}

export function Topbar({
  activeDestination,
  title,
  leaf,
  onOpenCommandPalette = () => {},
}: TopbarProps): ReactElement {
  const resolvedTitle = title ?? TITLE_BY_SLUG[activeDestination];
  const subtitle = resolveSubtitle(leaf);

  const barStyle: CSSProperties = {
    height: TOPBAR_HEIGHT,
    minHeight: TOPBAR_HEIGHT,
    backgroundColor: "var(--color-bg)",
    borderBottom: "1px solid var(--color-border)",
    color: "var(--color-text)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 16,
    padding: "0 16px",
    boxSizing: "border-box",
  };
  // Left cluster: title over subtitle. `minWidth: 0` lets a long title/subtitle
  // ellipsize instead of shoving the command trigger off the row.
  const leadStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    gap: 1,
    minWidth: 0,
    flex: 1,
  };
  const titleStyle: CSSProperties = {
    fontFamily: "var(--font-display)",
    fontSize: "var(--font-size-sm)", // 13.6px ≈ DESIGN-SPEC §1 13.5px
    fontWeight: "var(--font-weight-semibold)" as CSSProperties["fontWeight"],
    lineHeight: 1.2,
    color: "var(--color-text)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const subtitleStyle: CSSProperties = {
    fontSize: "var(--font-size-2xs)", // 11.2px ≈ DESIGN-SPEC §1 11.5px
    lineHeight: 1.2,
    color: "var(--color-text-muted)",
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
        <span style={titleStyle} data-testid="topbar-title">
          {resolvedTitle}
        </span>
        {subtitle !== null ? (
          <span style={subtitleStyle} data-testid="topbar-subtitle">
            {subtitle}
          </span>
        ) : null}
      </div>
      <CommandPaletteTrigger
        className={TRIGGER_CLASS}
        onOpen={onOpenCommandPalette}
      />
    </header>
  );
}

export { TOPBAR_HEIGHT };
