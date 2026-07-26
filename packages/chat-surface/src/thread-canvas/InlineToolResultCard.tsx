// Compact, schema-bound transcript results for reads that are usefully answered
// in chat rather than promoted into a Studio surface. These are deliberately
// derived from runtime facts: citations are linked by the backend-issued
// tool-call id; CSV facts require explicit scalar fields from the tool result.

import type { CitationSourceRef } from "@0x-copilot/api-types";
import type { CSSProperties, ReactElement } from "react";

import type { ToolCallEntry } from "./eventProjector";

export interface InlineToolResultCardProps {
  readonly toolCall: ToolCallEntry;
  /** Full run citation set; this component selects only backend-linked rows. */
  readonly citations: readonly CitationSourceRef[];
}

/**
 * Renders only cards whose underlying facts have been supplied by the runtime.
 * It never infers sources or analysis from a tool name, and it never renders
 * remote HTML or arbitrary component specs.
 */
export function InlineToolResultCard({
  toolCall,
  citations,
}: InlineToolResultCardProps): ReactElement | null {
  if (toolCall.status !== "complete") return null;

  const linkedSources = citations.filter(
    (citation) => citation.source_tool_call_id === toolCall.id,
  );
  const csv = readCsvSummary(toolCall);
  if (linkedSources.length === 0 && csv === null) return null;

  return (
    <div style={stackStyle}>
      {linkedSources.length > 0 ? (
        <WebSourcesCard sources={linkedSources} toolTitle={toolCall.title} />
      ) : null}
      {csv !== null ? <CsvSummaryCard summary={csv} /> : null}
    </div>
  );
}

function WebSourcesCard({
  sources,
  toolTitle,
}: {
  readonly sources: readonly CitationSourceRef[];
  readonly toolTitle: string;
}): ReactElement {
  const ordered = [...sources].sort((a, b) => a.ordinal - b.ordinal);
  return (
    <section
      data-testid="tc-inline-web-sources-card"
      aria-label={`${ordered.length} sources returned by ${toolTitle}`}
      style={cardStyle}
    >
      <div style={cardHeaderStyle}>
        <span data-testid="tc-inline-web-sources" style={eyebrowStyle}>
          {`SOURCES · ${ordered.length}`}
        </span>
        <span style={chatOnlyStyle}>Synthesized in chat</span>
      </div>
      <div role="list">
        {ordered.map((source) => (
          <SourceLine key={source.citation_id} source={source} />
        ))}
      </div>
    </section>
  );
}

function SourceLine({
  source,
}: {
  readonly source: CitationSourceRef;
}): ReactElement {
  const href = safeHttpUrl(source.source_url);
  const hostname = href === null ? source.source_connector : displayUrl(href);
  return (
    <div role="listitem" style={sourceRowStyle}>
      <span aria-hidden="true" style={sourceGlyphStyle}>
        {initials(source.title)}
      </span>
      <span style={sourceCopyStyle}>
        {href === null ? (
          <span style={sourceTitleStyle}>{source.title}</span>
        ) : (
          <a
            href={href}
            rel="noreferrer"
            target="_blank"
            style={sourceTitleStyle}
            aria-label={`Open source ${source.ordinal}: ${source.title}`}
          >
            {source.title}
          </a>
        )}
        <span style={sourceUrlStyle}>{hostname}</span>
      </span>
      <span style={citationStyle}>{`[${source.ordinal}]`}</span>
    </div>
  );
}

interface CsvSummary {
  readonly name: string;
  readonly rows: number;
  readonly columns: number;
  readonly byteSize: number | null;
  readonly metrics: readonly CsvMetric[];
  readonly preview: readonly CsvPreviewCell[];
  readonly previewRows: number;
}

interface CsvMetric {
  readonly label: string;
  readonly value: string;
}

interface CsvPreviewCell {
  readonly label: string;
  readonly value: string;
}

function CsvSummaryCard({
  summary,
}: {
  readonly summary: CsvSummary;
}): ReactElement {
  const meta = [
    `${summary.rows.toLocaleString()} rows`,
    `${summary.columns} ${summary.columns === 1 ? "column" : "columns"}`,
    summary.byteSize === null ? null : formatBytes(summary.byteSize),
  ].filter((part): part is string => part !== null);
  return (
    <section
      data-testid="tc-inline-csv-summary-card"
      aria-label={`CSV summary for ${summary.name}`}
      style={cardStyle}
    >
      <div style={cardHeaderStyle}>
        <span aria-hidden="true" style={csvGlyphStyle}>
          ▤
        </span>
        <span data-testid="tc-inline-csv-summary" style={csvNameStyle}>
          {summary.name}
        </span>
        <span style={chatOnlyStyle}>{meta.join(" · ")}</span>
      </div>
      {summary.metrics.length > 0 ? (
        <dl style={metricGridStyle}>
          {summary.metrics.map((metric) => (
            <div
              key={`${metric.label}:${metric.value}`}
              style={metricCellStyle}
            >
              <dt style={metricLabelStyle}>{metric.label}</dt>
              <dd style={metricValueStyle}>{metric.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {summary.preview.length > 0 ? (
        <div style={previewStyle}>
          <span
            style={eyebrowStyle}
          >{`PREVIEW · ${summary.previewRows} ${summary.previewRows === 1 ? "row" : "rows"}`}</span>
          <dl style={previewGridStyle}>
            {summary.preview.map((cell) => (
              <div key={cell.label} style={previewCellStyle}>
                <dt style={previewLabelStyle}>{cell.label}</dt>
                <dd style={previewValueStyle}>{cell.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      <div style={csvFooterStyle}>
        No custom surface — analysis stays in chat
      </div>
    </section>
  );
}

function readCsvSummary(toolCall: ToolCallEntry): CsvSummary | null {
  const result = toolCall.result;
  if (result === undefined) return null;
  const path =
    firstString(result, ["path", "file_name", "filename"]) ??
    firstString(toolCall.args, ["path", "file_name", "filename"]);
  const name = csvBaseName(path);
  const rows = readNonNegativeInteger(result.rows);
  const headers = readStringArray(result.headers);
  const columns =
    readNonNegativeInteger(result.columns) ?? headers?.length ?? null;
  if (name === null || rows === null || columns === null) return null;
  const previewRows = readPreviewRows(result);
  return {
    name,
    rows,
    columns,
    byteSize:
      readNonNegativeInteger(result.size_bytes) ??
      readNonNegativeInteger(result.bytes),
    metrics: readMetrics(result),
    preview: previewRows.cells,
    previewRows: previewRows.count,
  };
}

function firstString(
  record: Record<string, unknown> | undefined,
  keys: readonly string[],
): string | null {
  if (record === undefined) return null;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim() !== "") return value;
  }
  return null;
}

function csvBaseName(value: string | null): string | null {
  if (value === null) return null;
  const name = value.replaceAll("\\", "/").split("/").at(-1) ?? "";
  return name.toLowerCase().endsWith(".csv") && name.length <= 240
    ? name
    : null;
}

function readNonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function readStringArray(value: unknown): readonly string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : null;
}

function readMetrics(record: Record<string, unknown>): readonly CsvMetric[] {
  const raw = record.summary_metrics ?? record.metrics ?? record.stats;
  if (!Array.isArray(raw)) return [];
  const metrics: CsvMetric[] = [];
  for (const candidate of raw) {
    if (
      candidate === null ||
      typeof candidate !== "object" ||
      Array.isArray(candidate)
    )
      continue;
    const entry = candidate as Record<string, unknown>;
    const label = firstString(entry, ["label", "key", "name"]);
    const value = scalarText(entry.value ?? entry.v);
    if (
      label === null ||
      value === null ||
      label.length > 48 ||
      value.length > 96
    )
      continue;
    metrics.push({ label: label.toUpperCase(), value });
    if (metrics.length === 6) break;
  }
  return metrics;
}

function readPreviewRows(record: Record<string, unknown>): {
  readonly count: number;
  readonly cells: readonly CsvPreviewCell[];
} {
  const raw = record.preview_rows ?? record.preview;
  const rows = Array.isArray(raw)
    ? raw
    : raw === null || typeof raw !== "object"
      ? []
      : [raw];
  const first = rows[0];
  if (first === null || typeof first !== "object" || Array.isArray(first)) {
    return { count: 0, cells: [] };
  }
  const cells: CsvPreviewCell[] = [];
  for (const [label, value] of Object.entries(first)) {
    const text = scalarText(value);
    if (text === null || label.length > 48 || text.length > 96) continue;
    cells.push({ label, value: text });
    if (cells.length === 3) break;
  }
  return { count: rows.length, cells };
}

function scalarText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return null;
}

function safeHttpUrl(value: string | null): string | null {
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

function displayUrl(value: string): string {
  const parsed = new URL(value);
  return `${parsed.hostname}${parsed.pathname === "/" ? "" : parsed.pathname}`.slice(
    0,
    120,
  );
}

function initials(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean);
  return (
    words
      .slice(0, 2)
      .map((word) => word[0]?.toUpperCase() ?? "")
      .join("") || "•"
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

const stackStyle: CSSProperties = { display: "grid", gap: 8 };
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
const chatOnlyStyle: CSSProperties = {
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
const csvGlyphStyle: CSSProperties = {
  alignItems: "center",
  background: "var(--color-surface-elevated)",
  borderRadius: 5,
  color: "var(--color-success)",
  display: "inline-flex",
  flex: "0 0 auto",
  fontSize: 11,
  height: 18,
  justifyContent: "center",
  width: 18,
};
const csvNameStyle: CSSProperties = {
  // The design's --tx2 tier is the system's strong text rung: factual and
  // quieter than an action while still distinct from muted metadata.
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  lineHeight: "15px",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const metricGridStyle: CSSProperties = {
  background: "var(--color-border)",
  display: "grid",
  gap: 1,
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  margin: 0,
  padding: 0,
};
const metricCellStyle: CSSProperties = {
  background: "var(--color-surface)",
  minWidth: 0,
  padding: "9px 11px",
};
const metricLabelStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 8.5,
  letterSpacing: "0.08em",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const metricValueStyle: CSSProperties = {
  color: "var(--color-text)",
  fontFamily: "var(--font-sans)",
  fontSize: 13,
  fontWeight: 600,
  lineHeight: "18px",
  margin: "3px 0 0",
};
const previewStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  padding: "9px 11px",
};
const previewGridStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  margin: "6px 0 0",
};
const previewCellStyle: CSSProperties = { minWidth: 0 };
const previewLabelStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 8.5,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const previewValueStyle: CSSProperties = {
  color: "var(--color-text-secondary)",
  fontFamily: "var(--font-sans)",
  fontSize: 10,
  lineHeight: "14px",
  margin: "1px 0 0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const csvFooterStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  padding: "9px 11px",
};
