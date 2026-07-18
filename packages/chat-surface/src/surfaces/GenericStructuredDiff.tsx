import type { CSSProperties, ReactElement, ReactNode } from "react";

import { TIER3_SCHEME, type SaaSRendererAdapter } from "./SaaSRendererAdapter";
import { registerAdapter } from "./SurfaceRegistry";

export interface GenericCurrentState {
  readonly resourceId?: unknown;
  readonly saas?: unknown;
  readonly openUrl?: unknown;
  readonly fields?: unknown;
}

export interface GenericFieldChange {
  readonly field: string;
  readonly old?: unknown;
  readonly new?: unknown;
}

export interface GenericStructuredDiffPayload {
  readonly resourceId?: unknown;
  readonly saas?: unknown;
  readonly openUrl?: unknown;
  readonly reasoning?: unknown;
  readonly fieldChanges?: readonly GenericFieldChange[] | unknown;
  readonly proposed?: unknown;
  readonly current?: unknown;
}

const MAX_DEPTH = 5;
const MAX_STRING_BYTES = 2048;
const MAX_ARRAY_ITEMS = 50;
const MAX_OBJECT_KEYS = 50;

const PALETTE = {
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  headerBg: "#1f2226",
  textHi: "#f4f5f6",
  textMid: "#c8ccd1",
  textLo: "#9aa0a6",
  textMute: "#6c7178",
  lime: "var(--color-accent)",
  limeSoft: "color-mix(in srgb, var(--color-accent) 14%, transparent)",
  diffOld: "#ef5a5a",
  diffOldBg: "rgba(239, 90, 90, 0.10)",
  diffNew: "#3ddc97",
  diffNewBg: "rgba(61, 220, 151, 0.10)",
} as const;

function coerceString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFieldChange(value: unknown): value is GenericFieldChange {
  return (
    isPlainObject(value) &&
    typeof (value as Record<string, unknown>).field === "string"
  );
}

function safeOpenUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  const lower = trimmed.toLowerCase();
  if (lower.startsWith("http://") || lower.startsWith("https://")) {
    return trimmed;
  }
  return null;
}

function truncateString(value: string): {
  readonly text: string;
  readonly hidden: number;
} {
  if (value.length <= MAX_STRING_BYTES) {
    return { text: value, hidden: 0 };
  }
  return {
    text: value.slice(0, MAX_STRING_BYTES),
    hidden: value.length - MAX_STRING_BYTES,
  };
}

interface RenderCtx {
  readonly visited: WeakSet<object>;
  keyCounter: number;
}

function nextKey(ctx: RenderCtx): string {
  ctx.keyCounter += 1;
  return `g${ctx.keyCounter}`;
}

function renderPrimitive(value: string | number | boolean): ReactNode {
  return <span style={valueTextStyle}>{String(value)}</span>;
}

function renderString(value: string): ReactNode {
  const { text, hidden } = truncateString(value);
  if (hidden === 0) {
    return <span style={valueTextStyle}>{text}</span>;
  }
  return (
    <span style={valueTextStyle}>
      {text}
      <span style={truncationStyle} data-testid="generic-diff-truncation">
        {` … (+${hidden} chars hidden)`}
      </span>
    </span>
  );
}

function renderArray(
  value: readonly unknown[],
  depth: number,
  ctx: RenderCtx,
): ReactNode {
  if (value.length === 0) {
    return <span style={emptyStyle}>(empty list)</span>;
  }
  const slice = value.slice(0, MAX_ARRAY_ITEMS);
  const hiddenCount = value.length - slice.length;
  return (
    <ol style={listStyle} data-testid="generic-diff-array">
      {slice.map((item, idx) => (
        <li key={`${nextKey(ctx)}-${idx}`} style={listItemStyle}>
          {renderValue(item, depth + 1, ctx)}
        </li>
      ))}
      {hiddenCount > 0 ? (
        <li
          key={nextKey(ctx)}
          style={listOverflowStyle}
          data-testid="generic-diff-array-overflow"
        >
          {`… (+${hiddenCount} items hidden)`}
        </li>
      ) : null}
    </ol>
  );
}

function renderObject(
  value: Record<string, unknown>,
  depth: number,
  ctx: RenderCtx,
): ReactNode {
  const keys = Object.keys(value);
  if (keys.length === 0) {
    return <span style={emptyStyle}>(empty object)</span>;
  }
  const slice = keys.slice(0, MAX_OBJECT_KEYS);
  const hiddenCount = keys.length - slice.length;
  return (
    <dl style={dlStyle} data-testid="generic-diff-object">
      {slice.map((key) => (
        <div key={`${nextKey(ctx)}-${key}`} style={dlRowStyle}>
          <dt style={dtStyle}>{key}</dt>
          <dd style={ddStyle}>{renderValue(value[key], depth + 1, ctx)}</dd>
        </div>
      ))}
      {hiddenCount > 0 ? (
        <div
          key={nextKey(ctx)}
          style={dlOverflowStyle}
          data-testid="generic-diff-object-overflow"
        >
          {`… (+${hiddenCount} keys hidden)`}
        </div>
      ) : null}
    </dl>
  );
}

function renderValue(value: unknown, depth: number, ctx: RenderCtx): ReactNode {
  if (depth > MAX_DEPTH) {
    return (
      <span style={depthCapStyle} data-testid="generic-diff-depth-cap">
        …
      </span>
    );
  }
  if (value === null) {
    return <span style={nullStyle}>null</span>;
  }
  if (value === undefined) {
    return <span style={nullStyle}>—</span>;
  }
  if (typeof value === "string") {
    return renderString(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return renderPrimitive(value);
  }
  if (typeof value === "object") {
    if (ctx.visited.has(value as object)) {
      return (
        <span style={depthCapStyle} data-testid="generic-diff-circular">
          (circular)
        </span>
      );
    }
    ctx.visited.add(value as object);
    if (Array.isArray(value)) {
      return renderArray(value as readonly unknown[], depth, ctx);
    }
    return renderObject(value as Record<string, unknown>, depth, ctx);
  }
  return <span style={depthCapStyle}>(unrepresentable)</span>;
}

function freshCtx(): RenderCtx {
  return { visited: new WeakSet<object>(), keyCounter: 0 };
}

function CardHeader(props: {
  readonly resourceLabel: string;
  readonly saasLabel: string;
  readonly pending?: boolean;
}): ReactElement {
  return (
    <header style={headerRowStyle} data-testid="generic-diff-header">
      <div style={headerLeftStyle}>
        <span style={saasPillStyle} data-testid="generic-diff-saas">
          {props.saasLabel}
        </span>
        <span style={resourceIdStyle} data-testid="generic-diff-resource-id">
          {props.resourceLabel}
        </span>
      </div>
      {props.pending ? (
        <span style={pendingPillStyle} data-testid="generic-diff-pending-pill">
          PENDING DIFF
        </span>
      ) : null}
    </header>
  );
}

function OpenInLink(props: {
  readonly href: string;
  readonly saasLabel: string;
  readonly resourceLabel: string;
}): ReactElement {
  return (
    <a
      href={props.href}
      target="_blank"
      rel="noreferrer noopener"
      style={openLinkStyle}
      aria-label={`Open ${props.resourceLabel} in ${props.saasLabel}`}
      data-testid="generic-diff-open-link"
    >
      {`Open in ${props.saasLabel} →`}
    </a>
  );
}

function CurrentStateBody(props: {
  readonly state: GenericCurrentState;
}): ReactElement {
  const ctx = freshCtx();
  const fields = props.state.fields;
  if (isPlainObject(fields)) {
    return (
      <section style={bodyStyle} data-testid="generic-diff-current-body">
        {renderObject(fields, 1, ctx)}
      </section>
    );
  }
  if (fields === undefined) {
    return (
      <section style={bodyStyle} data-testid="generic-diff-current-body-empty">
        <span style={emptyStyle}>(no fields)</span>
      </section>
    );
  }
  return (
    <section style={bodyStyle} data-testid="generic-diff-current-body">
      {renderValue(fields, 1, ctx)}
    </section>
  );
}

function FieldChangeRow(props: {
  readonly change: GenericFieldChange;
}): ReactElement {
  const { change } = props;
  const oldCtx = freshCtx();
  const newCtx = freshCtx();
  return (
    <div style={changeRowStyle} data-testid="generic-diff-change-row">
      <div style={changeFieldNameStyle}>{change.field}</div>
      <div style={changeBodyStyle}>
        <div
          style={oldCellStyle}
          data-testid="generic-diff-change-old"
          aria-label={`${change.field} previous value`}
        >
          {renderValue(change.old, 1, oldCtx)}
        </div>
        <span aria-hidden="true" style={arrowStyle}>
          →
        </span>
        <div
          style={newCellStyle}
          data-testid="generic-diff-change-new"
          aria-label={`${change.field} new value`}
        >
          {renderValue(change.new, 1, newCtx)}
        </div>
      </div>
    </div>
  );
}

function resourceLabelFor(value: unknown): string {
  const coerced = coerceString(value);
  return coerced ?? "(no resource id)";
}

function saasLabelFor(value: unknown): string {
  const coerced = coerceString(value);
  return coerced ?? "(unknown saas)";
}

function renderCurrentImpl(state: GenericCurrentState): ReactElement {
  const resourceLabel = resourceLabelFor(state.resourceId);
  const saasLabel = saasLabelFor(state.saas);
  const href = safeOpenUrl(state.openUrl);
  return (
    <div
      role="group"
      aria-label={`${saasLabel} ${resourceLabel}`}
      style={cardStyle}
      data-testid="generic-structured-diff"
      data-mode="current"
    >
      <CardHeader resourceLabel={resourceLabel} saasLabel={saasLabel} />
      <CurrentStateBody state={state} />
      {href ? (
        <footer style={footerStyle}>
          <OpenInLink
            href={href}
            saasLabel={saasLabel}
            resourceLabel={resourceLabel}
          />
        </footer>
      ) : null}
    </div>
  );
}

function diffChangesFrom(value: unknown): readonly GenericFieldChange[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isFieldChange);
}

function renderDiffImpl(diff: GenericStructuredDiffPayload): ReactElement {
  const resourceLabel = resourceLabelFor(diff.resourceId);
  const saasLabel = saasLabelFor(diff.saas);
  const href = safeOpenUrl(diff.openUrl);
  const reasoning = coerceString(diff.reasoning);
  const changes = diffChangesFrom(diff.fieldChanges);

  const ariaLabel = `Pending diff: ${saasLabel} ${resourceLabel}`;

  if (changes.length === 0) {
    const fallback =
      diff.proposed !== undefined
        ? diff.proposed
        : diff.current !== undefined
          ? diff.current
          : undefined;
    const fallbackState: GenericCurrentState = isPlainObject(fallback)
      ? {
          resourceId: diff.resourceId,
          saas: diff.saas,
          openUrl: diff.openUrl,
          fields: (fallback as Record<string, unknown>).fields ?? fallback,
        }
      : {
          resourceId: diff.resourceId,
          saas: diff.saas,
          openUrl: diff.openUrl,
          fields: fallback,
        };
    return (
      <div
        role="group"
        aria-label={ariaLabel}
        style={cardStyle}
        data-testid="generic-structured-diff"
        data-mode="diff-current-only"
      >
        <CardHeader
          resourceLabel={resourceLabel}
          saasLabel={saasLabel}
          pending
        />
        {reasoning ? (
          <p style={reasoningStyle} data-testid="generic-diff-reasoning">
            {reasoning}
          </p>
        ) : null}
        <CurrentStateBody state={fallbackState} />
        {href ? (
          <footer style={footerStyle}>
            <OpenInLink
              href={href}
              saasLabel={saasLabel}
              resourceLabel={resourceLabel}
            />
          </footer>
        ) : null}
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      style={cardStyle}
      data-testid="generic-structured-diff"
      data-mode="diff"
    >
      <CardHeader resourceLabel={resourceLabel} saasLabel={saasLabel} pending />
      {reasoning ? (
        <p style={reasoningStyle} data-testid="generic-diff-reasoning">
          {reasoning}
        </p>
      ) : null}
      <section
        style={bodyStyle}
        data-testid="generic-diff-changes"
        aria-label="Field changes"
      >
        {changes.map((change, idx) => (
          <FieldChangeRow key={`${change.field}-${idx}`} change={change} />
        ))}
      </section>
      {href ? (
        <footer style={footerStyle}>
          <OpenInLink
            href={href}
            saasLabel={saasLabel}
            resourceLabel={resourceLabel}
          />
        </footer>
      ) : null}
    </div>
  );
}

export const GenericStructuredDiff: SaaSRendererAdapter<
  GenericCurrentState,
  GenericStructuredDiffPayload
> = {
  scheme: TIER3_SCHEME,
  matches: () => true,
  renderCurrent: renderCurrentImpl,
  renderDiff: renderDiffImpl,
  metadata: {
    origin: "first-party",
    schemaVersion: 1,
  },
};

export function registerGenericStructuredDiff(): void {
  registerAdapter(GenericStructuredDiff as SaaSRendererAdapter);
}

const cardStyle: CSSProperties = {
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 10,
  color: PALETTE.textHi,
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 14,
  width: "min(420px, 100%)",
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontSize: "var(--font-size-sm)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  background: PALETTE.headerBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 8,
  padding: "8px 10px",
};

const headerLeftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  minWidth: 0,
};

const saasPillStyle: CSSProperties = {
  background: PALETTE.limeSoft,
  color: PALETTE.lime,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 999,
  padding: "2px 8px",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  whiteSpace: "nowrap",
};

const resourceIdStyle: CSSProperties = {
  color: PALETTE.textHi,
  fontSize: "var(--font-size-sm)",
  fontWeight: 600,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const pendingPillStyle: CSSProperties = {
  background: PALETTE.lime,
  color: PALETTE.cardBg,
  borderRadius: 999,
  padding: "2px 8px",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 700,
  letterSpacing: 0.7,
};

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const dlStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  margin: 0,
};

const dlRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(80px, 28%) 1fr",
  gap: 10,
  alignItems: "baseline",
};

const dtStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: "var(--font-size-xs)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const ddStyle: CSSProperties = {
  margin: 0,
  color: PALETTE.textHi,
  fontSize: "var(--font-size-sm)",
  minWidth: 0,
  overflowWrap: "anywhere",
};

const dlOverflowStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontSize: "var(--font-size-2xs)",
  fontStyle: "italic",
};

const listStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const listItemStyle: CSSProperties = {
  color: PALETTE.textHi,
};

const listOverflowStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontSize: "var(--font-size-2xs)",
  fontStyle: "italic",
  listStyle: "none",
};

const valueTextStyle: CSSProperties = {
  color: PALETTE.textHi,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

const truncationStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontStyle: "italic",
};

const nullStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontStyle: "italic",
};

const emptyStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontStyle: "italic",
};

const depthCapStyle: CSSProperties = {
  color: PALETTE.textMute,
  fontStyle: "italic",
};

const reasoningStyle: CSSProperties = {
  margin: 0,
  color: PALETTE.textMid,
  fontSize: "var(--font-size-xs)",
  lineHeight: 1.5,
  borderLeft: `2px solid ${PALETTE.lime}`,
  paddingLeft: 10,
};

const changeRowStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  borderRadius: 8,
  background: PALETTE.headerBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  padding: "8px 10px",
};

const changeFieldNameStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: "var(--font-size-2xs)",
  textTransform: "uppercase",
  letterSpacing: 0.6,
  fontWeight: 600,
};

const changeBodyStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr auto 1fr",
  alignItems: "center",
  gap: 8,
};

const oldCellStyle: CSSProperties = {
  background: PALETTE.diffOldBg,
  border: `1px solid ${PALETTE.diffOld}`,
  borderRadius: 6,
  padding: "6px 8px",
  color: PALETTE.textMid,
  textDecoration: "line-through",
  fontSize: "var(--font-size-xs)",
  minWidth: 0,
  overflowWrap: "anywhere",
};

const newCellStyle: CSSProperties = {
  background: PALETTE.diffNewBg,
  border: `1px solid ${PALETTE.diffNew}`,
  borderRadius: 6,
  padding: "6px 8px",
  color: PALETTE.textHi,
  fontWeight: 500,
  fontSize: "var(--font-size-xs)",
  minWidth: 0,
  overflowWrap: "anywhere",
};

const arrowStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: "var(--font-size-md)",
};

const footerStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};

const openLinkStyle: CSSProperties = {
  color: PALETTE.lime,
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  textDecoration: "none",
  borderBottom: `1px dashed ${PALETTE.lime}`,
  paddingBottom: 1,
};
