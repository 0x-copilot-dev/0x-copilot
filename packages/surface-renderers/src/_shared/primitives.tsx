import type { CSSProperties, ReactElement, ReactNode } from "react";

import { SURFACE_PALETTE as PALETTE } from "./palette";
import { isSafeHttpUrl } from "./path";

// Shared, pure presentational primitives for the ArchetypeRenderer pack
// (PRD-03). These reuse the visual grammar of the tier-1 renderers
// (OpportunityRenderer / EmailRenderer) so generic archetypes read as the same
// system. No interactivity, no globals, no I/O — D28.

export const SURFACE_FONT =
  "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

export const pageStyle: CSSProperties = {
  background: PALETTE.pageBg,
  minHeight: "100%",
  padding: 24,
  fontFamily: SURFACE_FONT,
  color: PALETTE.textHi,
  display: "flex",
  justifyContent: "center",
};

export const cardStyle: CSSProperties = {
  background: PALETTE.surface,
  border: `1px solid ${PALETTE.border}`,
  borderRadius: 14,
  width: "100%",
  maxWidth: 820,
  display: "flex",
  flexDirection: "column",
  gap: 16,
  padding: 22,
  boxShadow: "0 8px 28px rgba(0,0,0,0.4)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  borderBottom: `1px solid ${PALETTE.border}`,
  paddingBottom: 12,
  gap: 12,
};

const headerTitleStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  minWidth: 0,
};

const kickerStyle: CSSProperties = {
  fontSize: 11,
  letterSpacing: 0.6,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  fontSize: 18,
  color: PALETTE.textHi,
  fontWeight: 600,
  overflowWrap: "anywhere",
};

const subtitleStyle: CSSProperties = {
  fontSize: 13,
  color: PALETTE.textMid,
  overflowWrap: "anywhere",
};

const badgeStyle: CSSProperties = {
  background: "transparent",
  border: `1px solid ${PALETTE.border}`,
  color: PALETTE.textMid,
  fontSize: 11,
  padding: "3px 8px",
  borderRadius: 999,
  letterSpacing: 0.4,
  whiteSpace: "nowrap",
};

export interface SurfaceHeaderProps {
  readonly kicker: string;
  readonly title: string;
  readonly subtitle?: string;
  readonly badge?: string;
}

export function SurfaceHeader(props: SurfaceHeaderProps): ReactElement {
  const { kicker, title, subtitle, badge } = props;
  return (
    <header style={headerRowStyle} data-testid="surface-header">
      <div style={headerTitleStyle}>
        <span style={kickerStyle}>{kicker}</span>
        <span style={titleStyle} data-testid="surface-title">
          {title || "Untitled"}
        </span>
        {subtitle ? (
          <span style={subtitleStyle} data-testid="surface-subtitle">
            {subtitle}
          </span>
        ) : null}
      </div>
      {badge ? (
        <span style={badgeStyle} data-testid="surface-badge">
          {badge}
        </span>
      ) : null}
    </header>
  );
}

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "150px 1fr",
  alignItems: "baseline",
  gap: 12,
  paddingBlock: 6,
  borderBottom: `1px solid ${PALETTE.border}`,
};

const fieldLabelStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 12,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  fontWeight: 600,
};

const fieldValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: 13,
  overflowWrap: "anywhere",
};

const numericValueStyle: CSSProperties = {
  ...fieldValueStyle,
  fontVariantNumeric: "tabular-nums",
};

export interface FieldRowProps {
  readonly fieldKey: string;
  readonly label: string;
  readonly value: string;
  readonly numeric?: boolean;
}

export function FieldRow(props: FieldRowProps): ReactElement {
  const { fieldKey, label, value, numeric } = props;
  return (
    <div style={rowStyle} data-testid={`field-${fieldKey}`}>
      <span style={fieldLabelStyle}>{label}</span>
      <span
        style={numeric ? numericValueStyle : fieldValueStyle}
        data-testid={`field-${fieldKey}-value`}
      >
        {value || " "}
      </span>
    </div>
  );
}

export const fieldGridStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const fallbackStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
  fontSize: 12,
  color: PALETTE.textLo,
  letterSpacing: 0.3,
};

const fallbackDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.textLo,
};

/** The "spec not ready yet" hint shown above the generic field list. */
export function PreparingHint(): ReactElement {
  return (
    <div style={fallbackStyle} data-testid="surface-preparing-hint">
      <span aria-hidden="true" style={fallbackDotStyle} />
      Preparing view…
    </div>
  );
}

const linkRowStyle: CSSProperties = {
  paddingTop: 6,
};

const linkStyle: CSSProperties = {
  color: PALETTE.lime,
  fontSize: 13,
  textDecoration: "none",
};

/** Renders `link.url_path` as a real anchor only when it resolves to an
 * `http(s)` URL; anything else is inert text (D9 injection bound, PRD-03 AC3). */
export function SurfaceLinkRow(props: {
  readonly label: string;
  readonly value: unknown;
}): ReactElement {
  const { label, value } = props;
  if (isSafeHttpUrl(value)) {
    return (
      <div style={linkRowStyle}>
        <a
          style={linkStyle}
          href={value}
          rel="noreferrer noopener"
          data-testid="surface-link"
        >
          {label || value}
        </a>
      </div>
    );
  }
  return (
    <div style={linkRowStyle}>
      <span
        style={{ ...fieldValueStyle, color: PALETTE.textLo }}
        data-testid="surface-link-text"
      >
        {label || String(value ?? "")}
      </span>
    </div>
  );
}

const emptyStyle: CSSProperties = {
  fontSize: 13,
  color: PALETTE.textLo,
  paddingBlock: 6,
};

export function EmptyBody(props: {
  readonly children?: ReactNode;
}): ReactElement {
  return (
    <div style={emptyStyle} data-testid="surface-empty">
      {props.children ?? "No data to display."}
    </div>
  );
}

/** The maximum number of top-level entries the spec-less generic list paints. */
export const GENERIC_FIELD_CAP = 40;

function titleize(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * A compact label/value list over the top-level primitive entries of an
 * `unknown` payload — the spec-less fallback body. Nested objects/arrays are
 * summarised, never expanded, so it can't recurse into a hostile deep tree.
 */
export function GenericFieldList(props: {
  readonly data: unknown;
  readonly format: (value: unknown) => string;
}): ReactElement {
  const { data, format } = props;
  if (typeof data !== "object" || data === null) {
    return (
      <div style={fieldGridStyle} data-testid="surface-generic-fields">
        <FieldRow fieldKey="value" label="Value" value={format(data)} />
      </div>
    );
  }
  const entries = Array.isArray(data)
    ? data.map((value, index) => [String(index), value] as const)
    : Object.entries(data as Record<string, unknown>);
  const shown = entries.slice(0, GENERIC_FIELD_CAP);
  return (
    <div style={fieldGridStyle} data-testid="surface-generic-fields">
      {shown.map(([key, value]) => (
        <FieldRow
          key={key}
          fieldKey={key}
          label={titleize(key)}
          value={summarise(value, format)}
        />
      ))}
    </div>
  );
}

function summarise(value: unknown, format: (value: unknown) => string): string {
  if (Array.isArray(value)) {
    return `${value.length} item${value.length === 1 ? "" : "s"}`;
  }
  if (value !== null && typeof value === "object") {
    const count = Object.keys(value as Record<string, unknown>).length;
    return `{ ${count} field${count === 1 ? "" : "s"} }`;
  }
  return format(value);
}

// ---- Diff primitives (reuse the OpportunityFieldRow grammar) --------------

const changedRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  paddingBlock: 8,
  paddingInline: 10,
  borderRadius: 8,
  background: PALETTE.limeBgSoft,
  border: `1px solid ${PALETTE.lime}`,
  marginBlock: 2,
};

const changeHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};

const provenancePillStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 8px",
  borderRadius: 999,
  border: `1px solid ${PALETTE.border}`,
  fontSize: 11,
  letterSpacing: 0.4,
  color: PALETTE.textLo,
  textTransform: "uppercase",
};

const provenanceDotStyle: CSSProperties = {
  display: "inline-block",
  width: 6,
  height: 6,
  borderRadius: "50%",
  background: PALETTE.lime,
};

const diffPairStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  fontSize: 13,
};

const previousValueStyle: CSSProperties = {
  color: PALETTE.textLo,
  textDecoration: "line-through",
  textDecorationColor: PALETTE.textLo,
  overflowWrap: "anywhere",
};

const arrowStyle: CSSProperties = {
  color: PALETTE.textMid,
  fontSize: 12,
};

const nextValueStyle: CSSProperties = {
  color: PALETTE.textHi,
  background: "color-mix(in srgb, var(--color-accent) 18%, transparent)",
  padding: "1px 6px",
  borderRadius: 4,
  overflowWrap: "anywhere",
};

export interface DiffFieldRowProps {
  readonly fieldKey: string;
  readonly label: string;
  readonly previousValue: string;
  readonly nextValue: string;
  readonly provenance?: string;
}

/** A single before→after change row: struck-through old, accent-highlighted
 * new, provenance pill — the shared diff grammar for every archetype. */
export function DiffFieldRow(props: DiffFieldRowProps): ReactElement {
  const { fieldKey, label, previousValue, nextValue, provenance } = props;
  return (
    <div
      style={changedRowStyle}
      data-testid={`field-${fieldKey}`}
      data-changed="true"
    >
      <div style={changeHeaderStyle}>
        <span style={fieldLabelStyle}>{label}</span>
        <span
          style={provenancePillStyle}
          data-testid={`field-${fieldKey}-provenance`}
        >
          <span aria-hidden="true" style={provenanceDotStyle} />
          {provenance ?? "Proposed"}
        </span>
      </div>
      <div style={diffPairStyle}>
        <span
          style={previousValueStyle}
          data-testid={`field-${fieldKey}-previous`}
        >
          {previousValue || " "}
        </span>
        <span aria-hidden="true" style={arrowStyle}>
          →
        </span>
        <span style={nextValueStyle} data-testid={`field-${fieldKey}-next`}>
          {nextValue || " "}
        </span>
      </div>
    </div>
  );
}
