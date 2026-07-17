// <ToolUsageChart /> — read-only projection viz for a single Tool.
//
// Source:
//   - docs/atlas-new-design/destinations/tools-prd.md §3.1
//     `ToolUsageProjection` (calls_24h / calls_30d / p50_latency_ms_30d /
//     success_rate_30d / last_used_at).
//   - docs/atlas-new-design/destinations/tools-prd.md §3.2 — TU-1: this
//     is a READ-TIME GROUP BY, never a parallel tracker.
//   - Phase 8 `AgentUsageChart` — same architectural pattern (KPI chip
//     header + inline SVG bar chart + legend). The Agents chart axes are
//     cost × purpose stack; the Tools chart axis is calls / day. Those
//     are different enough projections that one component can't host
//     both without forking the rendering path internally — we mirror
//     the API, not the implementation. See agent file header.
//
// Why a tool-specific variant (not "reuse AgentUsageChart"):
//   - AgentUsageChart hard-codes `Purpose` stacking + currency micro-USD
//     formatting + period toggle. Tools have no purpose dimension, no
//     currency, and the time window is fixed at 30 days. Forcing the
//     agents shape would mean inventing a synthetic single-purpose key
//     ("calls") and the period-toggle UI we can't drive. The single-
//     source-of-truth here is the wire shape `ToolUsageProjection` from
//     @0x-copilot/api-types — both charts read from canonical
//     wire types; only the viz is per-destination.
//
// Invariants:
//   - Pure presentation. No data fetching, no transport.
//   - Reads `ToolUsageProjection` straight from api-types (zero brand
//     declarations, zero re-typing).
//   - Daily series is OPTIONAL; when missing, the chart still renders
//     the four KPI tiles + a "no daily series yet" hint.

import type { CSSProperties, ReactElement } from "react";

import type { ToolUsageProjection } from "@0x-copilot/api-types";

import { formatRelativeTime } from "../../util/time";

// ===========================================================================
// Public props.
// ===========================================================================

export interface ToolDailyCallPoint {
  /** YYYY-MM-DD or full ISO; the chart slices to YYYY-MM-DD for the label. */
  readonly date: string;
  readonly count: number;
}

export interface ToolUsageChartProps {
  /**
   * The canonical projection (24h / 30d KPIs + last-used + p50 + success).
   * Returned from `GET /v1/tools/{id}` (on the row) or
   * `GET /v1/tools/{id}/usage` (per-window rollup).
   */
  readonly usage: ToolUsageProjection;
  /**
   * Optional per-day call series for the 30-day window. The bar chart
   * renders one bar per element. When `undefined`, the chart hides the
   * bars and just shows the KPI strip.
   */
  readonly daily_calls?: ReadonlyArray<ToolDailyCallPoint>;
  /**
   * Frozen `now` for tests; defaults to `Date.now()`. Threaded through
   * to `formatRelativeTime` so the "last used" KPI is deterministic
   * under test.
   */
  readonly now?: number;
}

// ===========================================================================
// Component.
// ===========================================================================

export function ToolUsageChart(props: ToolUsageChartProps): ReactElement {
  const { usage, daily_calls, now } = props;

  const lastUsedLabel =
    usage.last_used_at === null
      ? "Never"
      : formatRelativeTime(usage.last_used_at, now);

  const p50Label =
    usage.p50_latency_ms_30d === null
      ? "—"
      : `${formatLatencyMs(usage.p50_latency_ms_30d)}`;

  // Success-rate band — the test brief asks us to render correctly for
  // null / 0 / 0.5 / 1. We keep the formatting band-agnostic; the band
  // text is derived deterministically from the numeric value.
  const successLabel =
    usage.success_rate_30d === null
      ? "—"
      : `${Math.round(usage.success_rate_30d * 100)}%`;
  const successBand = bandFor(usage.success_rate_30d);

  const hasBars = daily_calls !== undefined && daily_calls.length > 0;
  const maxCount = hasBars
    ? daily_calls.reduce((m, p) => (p.count > m ? p.count : m), 0)
    : 0;

  return (
    <section
      data-testid="tool-usage-chart"
      data-success-band={successBand}
      aria-label="Tool usage chart"
      style={containerStyle}
    >
      <div style={kpiStripStyle} data-testid="tool-usage-kpis">
        <Kpi
          testId="tool-usage-kpi-calls-24h"
          label="Calls · 24h"
          value={String(usage.calls_24h)}
        />
        <Kpi
          testId="tool-usage-kpi-calls-30d"
          label="Calls · 30d"
          value={String(usage.calls_30d)}
        />
        <Kpi
          testId="tool-usage-kpi-p50-latency"
          label="p50 latency · 30d"
          value={p50Label}
        />
        <Kpi
          testId="tool-usage-kpi-success-rate"
          label="Success · 30d"
          value={successLabel}
        />
      </div>

      <div style={subStripStyle}>
        <span
          data-testid="tool-usage-last-used"
          aria-label={`Last used ${lastUsedLabel}`}
          style={mutedStyle}
        >
          Last used: {lastUsedLabel}
        </span>
      </div>

      {hasBars ? (
        <DailyBars
          series={daily_calls}
          maxCount={maxCount > 0 ? maxCount : 1}
        />
      ) : (
        <div data-testid="tool-usage-empty" role="status" style={emptyStyle}>
          No daily call series available.
        </div>
      )}
    </section>
  );
}

// ===========================================================================
// Subcomponents.
// ===========================================================================

interface KpiProps {
  readonly testId: string;
  readonly label: string;
  readonly value: string;
}

function Kpi(props: KpiProps): ReactElement {
  return (
    <div style={kpiStyle} data-testid={props.testId}>
      <dt style={kpiLabelStyle}>{props.label}</dt>
      <dd style={kpiValueStyle}>{props.value}</dd>
    </div>
  );
}

interface DailyBarsProps {
  readonly series: ReadonlyArray<ToolDailyCallPoint>;
  readonly maxCount: number;
}

function DailyBars({ series, maxCount }: DailyBarsProps): ReactElement {
  // Inline SVG; matches AgentUsageChart layout discipline (fixed viewBox).
  const BAR_WIDTH = 12;
  const BAR_GAP = 4;
  const CHART_HEIGHT = 100;
  const LABEL_HEIGHT = 18;
  const TOP_PAD = 8;
  const SIDE_PAD = 8;
  const viewWidth =
    SIDE_PAD * 2 +
    series.length * BAR_WIDTH +
    Math.max(0, series.length - 1) * BAR_GAP;
  const viewHeight = TOP_PAD + CHART_HEIGHT + LABEL_HEIGHT;

  // Reduce x-axis tick density — only label the first, middle, last.
  const tickIndices = new Set<number>();
  if (series.length > 0) tickIndices.add(0);
  if (series.length > 1) tickIndices.add(series.length - 1);
  if (series.length > 2) tickIndices.add(Math.floor(series.length / 2));

  return (
    <div
      data-testid="tool-usage-bars"
      role="img"
      aria-label={`Daily tool calls over ${series.length} days`}
      style={{ width: "100%", overflowX: "auto" }}
    >
      <svg
        viewBox={`0 0 ${viewWidth} ${viewHeight}`}
        width="100%"
        height={viewHeight}
        preserveAspectRatio="xMinYMid meet"
        role="presentation"
        aria-hidden="true"
      >
        <line
          x1={SIDE_PAD}
          y1={TOP_PAD + CHART_HEIGHT}
          x2={viewWidth - SIDE_PAD}
          y2={TOP_PAD + CHART_HEIGHT}
          stroke="var(--color-border)"
          strokeWidth={1}
        />
        {series.map((p, idx) => {
          const x = SIDE_PAD + idx * (BAR_WIDTH + BAR_GAP);
          const h = (p.count / maxCount) * CHART_HEIGHT;
          const y = TOP_PAD + CHART_HEIGHT - h;
          return (
            <g
              key={`${p.date}-${idx}`}
              data-testid="tool-usage-bar"
              data-date={p.date.slice(0, 10)}
              data-count={p.count}
            >
              <rect
                x={x}
                y={y}
                width={BAR_WIDTH}
                height={h}
                fill="var(--color-accent)"
              >
                <title>{`${p.date.slice(0, 10)} — ${p.count} call${p.count === 1 ? "" : "s"}`}</title>
              </rect>
              {tickIndices.has(idx) ? (
                <text
                  x={x + BAR_WIDTH / 2}
                  y={TOP_PAD + CHART_HEIGHT + LABEL_HEIGHT - 4}
                  textAnchor="middle"
                  fontSize={10}
                  fill="var(--color-text-subtle)"
                >
                  {p.date.slice(5, 10)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ===========================================================================
// Helpers.
// ===========================================================================

/**
 * Map success rate ∈ [0,1] (or null) to a coarse band. Tests assert the
 * `data-success-band` attribute for each input case so the visual layer
 * can swap colors without changing the contract.
 */
export function bandFor(rate: number | null): string {
  if (rate === null) return "unknown";
  if (rate >= 0.95) return "good";
  if (rate >= 0.8) return "warning";
  return "bad";
}

function formatLatencyMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  padding: 16,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  boxSizing: "border-box",
};

const kpiStripStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
  gap: 8,
  margin: 0,
  padding: 0,
};

const kpiStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  padding: "8px 10px",
  background: "var(--color-bg-elevated)",
  borderRadius: 6,
};

const kpiLabelStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  margin: 0,
};

const kpiValueStyle: CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
  color: "var(--color-text)",
  margin: 0,
  fontVariantNumeric: "tabular-nums",
};

const subStripStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const mutedStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--color-text-muted)",
};

const emptyStyle: CSSProperties = {
  padding: 16,
  background: "var(--color-bg-elevated)",
  border: "1px dashed var(--color-border)",
  borderRadius: 6,
  color: "var(--color-text-muted)",
  textAlign: "center",
  fontSize: 13,
};
