// PaletteHitRow — one row of the ⌘K palette result list.
//
// Source: team-memory-cmdk-prd.md §3.3 + §7.3.
//
// Rendering rules (per PaletteHitKind, cross-audit §1.1):
//   * "entity"               → wrap in <ItemLink ref={hit.target}> so
//                              navigation flows through the shared
//                              ItemRef registry. The host's registered
//                              resolver renders the label; we still
//                              show the subtitle + kind chip outside
//                              the link so the row feels uniform.
//   * "navigation" | "action" | "command"
//                            → a <button> that invokes onActivate(hit).
//
// All text is plain — no Streamdown / markdown in v1 (sub-PRD §1.3).
// ARIA: each row has role="option" + aria-selected; the parent listbox
// owns aria-activedescendant.

import type { CSSProperties, ReactElement } from "react";

import type { PaletteHit, PaletteHitKind } from "@0x-copilot/api-types";

import { ItemLink } from "../refs/ItemLink";

export interface PaletteHitRowProps {
  readonly hit: PaletteHit;
  readonly isSelected: boolean;
  /**
   * DOM id on the row; the listbox parent points
   * `aria-activedescendant` at the selected row's id.
   */
  readonly id: string;
  /**
   * Fired for non-entity hits. Entity hits are activated by the
   * embedded <ItemLink> (it calls router.navigate via the registry).
   */
  readonly onActivate: (hit: PaletteHit) => void;
  /** Fired when the cursor enters the row, so the list can update selection. */
  readonly onHover: () => void;
}

const KIND_LABELS: Readonly<Record<PaletteHitKind, string>> = {
  navigation: "Go to",
  entity: "Open",
  action: "Action",
  command: "Command",
};

const rowBase: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  width: "100%",
  textAlign: "left",
  padding: "8px 12px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid transparent",
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
  font: "inherit",
};

// Selected/hover row = the design's `.cmdk__row[data-on]` (bg --panel2, no
// border). Previously referenced `--color-surface-elevated` — a token that did
// not exist — so every selected row fell back to a hard-coded light-grey
// #2a2a2a band (PRD-B). Now the design's surface-muted, borderless.
const rowSelected: CSSProperties = {
  ...rowBase,
  backgroundColor: "var(--color-surface-muted)",
  borderColor: "transparent",
};

const titleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text, #ededee)",
  display: "block",
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  marginTop: 2,
  display: "block",
};

const chipStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  border: "1px solid var(--color-border, #2a2a2c)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "2px 6px",
  whiteSpace: "nowrap",
  flexShrink: 0,
};

const iconStyle: CSSProperties = {
  width: 18,
  minWidth: 18,
  fontSize: "var(--font-size-xs, 12px)",
  color: "var(--color-text-subtle, #7e7e84)",
  textAlign: "center",
};

const itemLinkSlotStyle: CSSProperties = {
  flex: 1,
  display: "flex",
  flexDirection: "column",
  gap: 0,
  minWidth: 0,
};

export function PaletteHitRow({
  hit,
  isSelected,
  id,
  onActivate,
  onHover,
}: PaletteHitRowProps): ReactElement {
  const ariaLabel = `${KIND_LABELS[hit.kind]}: ${hit.title}`;

  if (hit.kind === "entity" && hit.target !== undefined) {
    // Entity hits route through the shared ItemRef registry so the
    // host's resolver decides navigation. We still wrap the row in a
    // listbox option for ↑↓ + Enter selection — Enter activation for
    // entity hits is handled by the parent palette (which programmatically
    // clicks the embedded <ItemLink>).
    return (
      <li
        role="option"
        aria-selected={isSelected}
        id={id}
        style={isSelected ? rowSelected : rowBase}
        onMouseEnter={onHover}
        data-testid="palette-hit-row"
        data-hit-id={hit.id}
        data-hit-kind={hit.kind}
      >
        <span
          style={iconStyle}
          aria-hidden="true"
          data-testid="palette-hit-icon"
        >
          {iconGlyph(hit.icon_hint)}
        </span>
        <span style={itemLinkSlotStyle}>
          <ItemLink ref={hit.target} label={hit.title} />
          {hit.subtitle !== undefined && hit.subtitle.length > 0 ? (
            <span style={subtitleStyle} data-testid="palette-hit-subtitle">
              {hit.subtitle}
            </span>
          ) : null}
        </span>
        <span style={chipStyle} data-testid="palette-hit-chip">
          {KIND_LABELS[hit.kind]}
        </span>
      </li>
    );
  }

  return (
    <li
      role="option"
      aria-selected={isSelected}
      id={id}
      style={{ padding: 0, listStyle: "none" }}
      onMouseEnter={onHover}
      data-testid="palette-hit-row"
      data-hit-id={hit.id}
      data-hit-kind={hit.kind}
    >
      <button
        type="button"
        style={isSelected ? rowSelected : rowBase}
        onClick={() => onActivate(hit)}
        aria-label={ariaLabel}
        data-testid="palette-hit-button"
      >
        <span
          style={iconStyle}
          aria-hidden="true"
          data-testid="palette-hit-icon"
        >
          {iconGlyph(hit.icon_hint)}
        </span>
        <span style={itemLinkSlotStyle}>
          <span style={titleStyle} data-testid="palette-hit-title">
            {hit.title}
          </span>
          {hit.subtitle !== undefined && hit.subtitle.length > 0 ? (
            <span style={subtitleStyle} data-testid="palette-hit-subtitle">
              {hit.subtitle}
            </span>
          ) : null}
        </span>
        <span style={chipStyle} data-testid="palette-hit-chip">
          {KIND_LABELS[hit.kind]}
        </span>
      </button>
    </li>
  );
}

// Tiny glyph stub — the host can theme later. Kept inline so we don't
// pull design-system into the palette substrate seam.
function iconGlyph(hint: string | undefined): string {
  switch (hint) {
    case "person":
      return "@";
    case "library_file":
      return "≡";
    case "routine":
      return "↻";
    case "chat":
      return "✱";
    case "project":
      return "▣";
    case "inbox_item":
      return "✉";
    case "todo":
      return "☐";
    default:
      return "·";
  }
}
