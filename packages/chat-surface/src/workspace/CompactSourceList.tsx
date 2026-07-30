// The dense source list — one line per source, in a bordered card.
//
// Lifted VERBATIM (markup + styles) out of `InlineToolResultCard`'s
// `WebSourcesCard`, which used to render under each `web_search` tool card in
// the transcript. It moved here on a product call: sources belong in the Sources
// rail, not repeated inline under every tool call, and this compact treatment is
// the one that reads well — a glyph, a title, its URL, and the citation ordinal
// on a single 8px-padded row.
//
// It replaced the rail's previous `.atlas-source-row` cards, which stacked a
// badge row, a wrapped snippet and a footnote per source and ate the panel at
// four results.
//
// Presentational only: callers normalise whatever they have (citation registry
// rows, ledger facts) into `CompactSourceItem`. That keeps this component from
// learning either shape, and lets provenance kinds that carry no URL (see
// `SourceFactV2`) render honestly by passing `href: null`.

import type { CSSProperties, ReactElement } from "react";

export interface CompactSourceItem {
  /** Stable key. */
  readonly id: string;
  /** Citation ordinal shown at the row's right edge; omit when unnumbered. */
  readonly ordinal: number | null;
  /** Row title. Linked when `href` is set. */
  readonly title: string;
  /** Second line — a URL for citations, a metadata line for ledger facts. */
  readonly subtitle: string;
  /** Absolute http(s) URL, already validated by the caller, or null. */
  readonly href: string | null;
  /** Fires instead of navigation when supplied (owner-routed artifact open). */
  readonly onActivate?: () => void;
}

export interface CompactSourceListProps {
  /** Eyebrow label, e.g. `WEB SEARCH`. The count is appended. */
  readonly label: string;
  readonly items: readonly CompactSourceItem[];
  /** Right-aligned note in the header, e.g. "Synthesized in chat". */
  readonly note?: string;
  readonly testId?: string;
  /** Per-row testid, so a caller keeps its own established row contract. */
  readonly rowTestId?: string;
}

export function CompactSourceList({
  label,
  items,
  note,
  testId,
  rowTestId,
}: CompactSourceListProps): ReactElement | null {
  if (items.length === 0) {
    return null;
  }
  return (
    <section
      data-testid={testId ?? "compact-source-list"}
      aria-label={`${items.length} ${label} sources`}
      style={cardStyle}
    >
      <div style={cardHeaderStyle}>
        <span
          style={eyebrowStyle}
        >{`${label.toUpperCase()} · ${items.length}`}</span>
        {note !== undefined ? <span style={noteStyle}>{note}</span> : null}
      </div>
      <div role="list">
        {items.map((item) => (
          <CompactSourceRow key={item.id} item={item} testId={rowTestId} />
        ))}
      </div>
    </section>
  );
}

function CompactSourceRow({
  item,
  testId,
}: {
  readonly item: CompactSourceItem;
  readonly testId?: string;
}): ReactElement {
  return (
    <div
      role="listitem"
      style={sourceRowStyle}
      data-testid={testId ?? "compact-source-row"}
      // Owner-routed openability, assertable without depending on which glyph
      // the row happens to draw.
      data-openable={item.onActivate !== undefined ? "true" : "false"}
    >
      <span aria-hidden="true" style={sourceGlyphStyle}>
        {initials(item.title)}
      </span>
      <span style={sourceCopyStyle}>
        {item.href !== null ? (
          <a
            href={item.href}
            rel="noreferrer"
            target="_blank"
            style={sourceTitleStyle}
            aria-label={`Open source${item.ordinal === null ? "" : ` ${item.ordinal}`}: ${item.title}`}
          >
            {item.title}
          </a>
        ) : item.onActivate !== undefined ? (
          <button
            type="button"
            onClick={item.onActivate}
            style={sourceTitleButtonStyle}
            aria-label={`Open ${item.title}`}
          >
            {item.title}
          </button>
        ) : (
          <span style={sourceTitleStyle}>{item.title}</span>
        )}
        <span style={sourceUrlStyle}>{item.subtitle}</span>
      </span>
      {item.ordinal !== null ? (
        <span style={citationStyle}>{`[${item.ordinal}]`}</span>
      ) : null}
    </div>
  );
}

/** Two-letter monogram for the row glyph; `•` when the title has no words. */
function initials(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean);
  return (
    words
      .slice(0, 2)
      .map((word) => word[0]?.toUpperCase() ?? "")
      .join("") || "•"
  );
}

/** Absolute http(s) URL or null — callers normalise untrusted values through this. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

/** `host/path` for display, capped so a long URL cannot blow out the row. */
export function displayUrl(value: string): string {
  const parsed = new URL(value);
  return `${parsed.hostname}${parsed.pathname === "/" ? "" : parsed.pathname}`.slice(
    0,
    120,
  );
}

const cardStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 10,
  color: "var(--color-text)",
  overflow: "hidden",
};
const cardHeaderStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 8,
  minWidth: 0,
  padding: "8px 11px",
};
const eyebrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  letterSpacing: "0.1em",
  lineHeight: "13.5px",
};
const noteStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  lineHeight: 1.3,
  marginLeft: "auto",
  textAlign: "right",
};
const sourceRowStyle: CSSProperties = {
  alignItems: "center",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 9,
  minWidth: 0,
  padding: "8px 11px",
};
const sourceGlyphStyle: CSSProperties = {
  alignItems: "center",
  background: "var(--color-surface-elevated)",
  borderRadius: 5,
  color: "var(--color-text-secondary)",
  display: "inline-flex",
  flex: "0 0 auto",
  fontFamily: "var(--font-sans)",
  fontSize: 8,
  fontWeight: 700,
  height: 18,
  justifyContent: "center",
  width: 18,
};
const sourceCopyStyle: CSSProperties = {
  display: "flex",
  flex: "1 1 auto",
  flexDirection: "column",
  minWidth: 0,
};
const sourceTitleStyle: CSSProperties = {
  color: "var(--color-text-secondary)",
  fontFamily: "var(--font-sans)",
  fontSize: 11,
  lineHeight: "15px",
  overflow: "hidden",
  textDecoration: "none",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const sourceTitleButtonStyle: CSSProperties = {
  ...sourceTitleStyle,
  background: "none",
  border: "none",
  cursor: "pointer",
  padding: 0,
  textAlign: "left",
};
const sourceUrlStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  lineHeight: "13px",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const citationStyle: CSSProperties = {
  color: "var(--color-accent)",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
};
