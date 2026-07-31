import type { CSSProperties } from "react";

/**
 * Shared compact chrome for transcript activity cards.
 *
 * Tool calls and subagent fleets intentionally share this geometry so a
 * conversation can mix the two without the cards reading as separate UI
 * systems. Their expanded bodies remain purpose-specific.
 */
export const activityCardFrameStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  boxSizing: "border-box",
  color: "var(--color-text)",
  display: "block",
  fontSize: 13,
  lineHeight: "19.5px",
  margin: 0,
  overflow: "hidden",
  padding: 0,
};

export const activityCardHeaderStyle: CSSProperties = {
  alignItems: "center",
  appearance: "none",
  background: "transparent",
  border: 0,
  boxSizing: "border-box",
  color: "inherit",
  cursor: "pointer",
  display: "flex",
  gap: 9,
  listStyle: "none",
  minWidth: 0,
  padding: "9px 11px",
  textAlign: "left",
  userSelect: "none",
  width: "100%",
};

export const activityCardStaticHeaderStyle: CSSProperties = {
  ...activityCardHeaderStyle,
  cursor: "auto",
};

export const activityCardTileStyle: CSSProperties = {
  alignItems: "center",
  background: "var(--color-surface-elevated)",
  borderRadius: 6,
  color: "var(--color-accent)",
  display: "inline-flex",
  flex: "0 0 auto",
  fontFamily: "var(--font-sans)",
  fontSize: 9,
  fontWeight: 700,
  height: 22,
  justifyContent: "center",
  lineHeight: 1,
  width: 22,
};

export const activityCardTitleStyle: CSSProperties = {
  color: "var(--color-text)",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  fontWeight: 500,
  lineHeight: "15px",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export const activityCardMetaStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  fontWeight: 400,
  lineHeight: "13.5px",
  whiteSpace: "nowrap",
};

export const activityCardChevronStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "0 0 auto",
  fontSize: 11,
  lineHeight: 1,
  width: 10,
};

export const activityCardDetailStyle: CSSProperties = {
  background: "var(--color-surface-muted)",
  borderTop: "1px solid var(--color-border)",
  padding: "10px 12px",
};

/** Native-summary and button disclosure behaviour shared by activity cards. */
export const ACTIVITY_CARD_INTERACTION_CSS = `
.tc-activity-card__head::-webkit-details-marker { display: none; }
.tc-activity-card__head::marker { content: ""; }
.tc-activity-card__head:hover { background: var(--color-surface-muted); }
.tc-activity-card__head:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
.tc-activity-card__head .tc-activity-card__chevron {
  transition: transform 120ms ease;
}
details[open] > .tc-activity-card__head .tc-activity-card__chevron,
.tc-activity-card[data-expanded="true"] .tc-activity-card__chevron {
  transform: rotate(180deg);
}
@media (prefers-reduced-motion: reduce) {
  .tc-activity-card__head .tc-activity-card__chevron { transition: none; }
}
`;
