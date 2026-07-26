// Generic record fallback — the honest, useful floor for a hydrated record
// whose connector-specific renderer is unavailable.
//
// This is deliberately a pre-built, data-only archetype. It never interprets
// markup, generates code, or guesses fields. A compact set of top-level scalar
// fields is shown when available; the complete serialized record remains
// inspectable through the native disclosure below.

import { useMemo, type CSSProperties, type ReactElement } from "react";

export interface GenericRecordFallbackProps {
  /** Human tab title supplied by the canvas; it is already safe display text. */
  readonly title?: string;
  /** Hydrated surface state. It may legitimately be absent while loading. */
  readonly state: unknown;
}

interface RecordField {
  readonly label: string;
  readonly value: string;
}

const MAX_FIELDS = 8;
const MAX_VALUE_LENGTH = 180;

const rootStyle: CSSProperties = {
  alignItems: "center",
  background: "transparent",
  color: "var(--color-text)",
  display: "grid",
  flex: "1 1 auto",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  lineHeight: "19.5px",
  minHeight: 0,
  padding: 26,
  placeItems: "center",
};

const contentStyle: CSSProperties = {
  maxWidth: "100%",
  textAlign: "center",
  width: 440,
};

const iconStyle: CSSProperties = {
  alignItems: "center",
  border: "1px dashed var(--color-border-stronger)",
  borderRadius: 14,
  color: "var(--color-text-subtle)",
  display: "inline-flex",
  height: 52,
  justifyContent: "center",
  margin: "0 auto 16px",
  width: 52,
};

const eyebrowStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-8-5)",
  letterSpacing: "0.08em",
  margin: 0,
  textTransform: "uppercase",
};

const titleStyle: CSSProperties = {
  fontFamily: "var(--font-display, var(--font-sans))",
  fontSize: 15,
  letterSpacing: "-0.01em",
  margin: "6px 0 0",
};

const copyStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontSize: 12,
  lineHeight: 1.65,
  margin: "8px 0 0",
};

const fieldsStyle: CSSProperties = {
  display: "grid",
  gap: 1,
  gridTemplateColumns: "minmax(0, 1fr)",
  margin: "16px 0 0",
  textAlign: "left",
};

const fieldStyle: CSSProperties = {
  background: "var(--color-surface)",
  display: "grid",
  gap: 8,
  gridTemplateColumns: "minmax(5rem, auto) minmax(0, 1fr)",
  padding: "8px 11px",
};

const fieldLabelStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-8-5)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const fieldValueStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  fontSize: "var(--font-size-sm)",
  minWidth: 0,
  overflowWrap: "anywhere",
};

const detailsStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-8-5)",
  marginTop: 14,
  textAlign: "left",
};

const rawStyle: CSSProperties = {
  background: "var(--color-surface-muted)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: 1.5,
  margin: "8px 0 0",
  maxHeight: 240,
  overflow: "auto",
  padding: 10,
  textAlign: "left",
  whiteSpace: "pre-wrap",
};

/**
 * Renders a safely bounded, data-led record card. A lack of hydrated data is
 * a normal loading condition, not an adapter failure, so the component says
 * exactly that instead of showing an error-shaped placeholder.
 */
export function GenericRecordFallback({
  title,
  state,
}: GenericRecordFallbackProps): ReactElement {
  const record = useMemo(() => extractRecord(state), [state]);
  const fields = useMemo(() => scalarFields(record), [record]);
  const raw = useMemo(() => serializeRecord(record), [record]);
  const displayTitle = nonEmpty(title) ?? "Record ready";

  return (
    <div
      role="status"
      data-testid="surface-placeholder"
      data-record-state={record === null ? "hydrating" : "ready"}
      style={rootStyle}
    >
      <div style={contentStyle}>
        <span style={iconStyle} aria-hidden="true">
          ▦
        </span>
        <p style={eyebrowStyle}>Connected record</p>
        <h2 style={titleStyle} data-testid="surface-record-fallback-title">
          {displayTitle}
        </h2>
        <p style={copyStyle} data-testid="surface-record-fallback-copy">
          {record === null
            ? "The record is ready. Its fields will appear here when the source payload finishes loading."
            : "The source returned a record without a connector-specific view. These fields are shown directly from the source."}
        </p>
        {fields.length > 0 ? (
          <dl data-testid="surface-record-fallback-fields" style={fieldsStyle}>
            {fields.map((field) => (
              <div key={field.label} style={fieldStyle}>
                <dt style={fieldLabelStyle}>{field.label}</dt>
                <dd style={fieldValueStyle}>{field.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {raw !== null ? (
          <details style={detailsStyle}>
            <summary>Inspect raw record</summary>
            <pre data-testid="surface-record-fallback-raw" style={rawStyle}>
              {raw}
            </pre>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function extractRecord(state: unknown): Record<string, unknown> | null {
  if (!isRecord(state)) return null;
  return isRecord(state.data) ? state.data : state;
}

function scalarFields(
  record: Record<string, unknown> | null,
): readonly RecordField[] {
  if (record === null) return [];
  const fields: RecordField[] = [];
  for (const [key, value] of Object.entries(record)) {
    const text = scalarText(value);
    if (text === null) continue;
    fields.push({ label: formatLabel(key), value: truncate(text) });
    if (fields.length === MAX_FIELDS) break;
  }
  return fields;
}

function scalarText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (value === null) return "None";
  return null;
}

function formatLabel(value: string): string {
  return truncate(value.replaceAll(/[_-]+/g, " "), 48);
}

function nonEmpty(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : null;
}

function truncate(value: string, limit = MAX_VALUE_LENGTH): string {
  return value.length <= limit ? value : `${value.slice(0, limit)}…`;
}

function serializeRecord(
  record: Record<string, unknown> | null,
): string | null {
  if (record === null) return null;
  try {
    return JSON.stringify(record, null, 2);
  } catch {
    return "[Record could not be serialized]";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
