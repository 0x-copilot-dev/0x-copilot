import { Select } from "@0x-copilot/design-system";
import { useMemo, type CSSProperties, type ReactElement } from "react";

/**
 * Sub-PRD (atlas-new-design / Agents destination §7 usage chart):
 *
 * Time-series of agent cost — daily / weekly / monthly view toggle.
 * Breakdown by Purpose (`main`, `tool_planning`, `tool_interpretation`,
 * `subagent_work`, `context_compression`, ...) as a stacked bar.
 * Cost chip shows the current-period total prominently.
 *
 * The shape mirrors `GET /v1/usage/agents/{agent_id}` (delivered by P8-A4).
 * It is duplicated locally as an interface so this presentation component
 * stays independent of the api-types worktree (and so the chart renders
 * correctly even if the wire shape grows new optional fields).
 */
export interface AgentUsageBucket {
  /** ISO timestamp at the start of this bucket (day / week / month). */
  readonly period_start: string;
  /**
   * Cost contribution per `Purpose` for this bucket, keyed by Purpose
   * StrEnum string value (`main`, `tool_planning`, `tool_interpretation`,
   * `subagent_work`, `context_compression`, ...). Missing keys count as 0.
   */
  readonly by_purpose: Readonly<Record<string, number>>;
}

export interface AgentUsageResponse {
  readonly agent_id: string;
  readonly period: { readonly start: string; readonly end: string };
  readonly granularity: "day" | "week" | "month";
  readonly currency: "USD";
  /** Total cost across the whole period, in micro-USD. */
  readonly total_cost_micro_usd: number;
  /** Ordered list of purposes seen in this response, used for stack order + legend. */
  readonly purposes: readonly string[];
  /** Stacked-bar buckets, ordered chronologically. */
  readonly buckets: readonly AgentUsageBucket[];
}

export type AgentUsagePeriod = "day" | "week" | "month";

export interface AgentUsageChartProps {
  readonly usage: AgentUsageResponse;
  readonly period: AgentUsagePeriod;
  readonly onPeriodChange: (next: AgentUsagePeriod) => void;
}

// Design tokens — keep names local for readability; values flow through theme.
const BACKGROUND = "var(--color-bg)";
const SURFACE = "var(--color-bg-elevated)";
const BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_SUBTLE = "var(--color-text-subtle)";
const CHIP_BG = "var(--color-bg-accent-subtle)";
const ACCENT = "var(--color-accent)";
const ACCENT_STRONG = "var(--color-accent-strong)";
const ACCENT_SOFT = "var(--color-accent-soft)";

/**
 * Palette is built from `--color-accent` family so theme/accent swaps cascade
 * automatically. Falls back through neutral/text-muted for additional purposes
 * beyond the named accent slots. Order matches typical Purpose frequency
 * (main → subagent_work → tool_planning → tool_interpretation → context_compression).
 */
const PURPOSE_PALETTE: readonly string[] = [
  ACCENT_STRONG,
  ACCENT,
  ACCENT_SOFT,
  "color-mix(in srgb, var(--color-accent) 45%, var(--color-text-muted))",
  "color-mix(in srgb, var(--color-accent) 25%, var(--color-text-subtle))",
  "color-mix(in srgb, var(--color-accent) 60%, var(--color-bg-elevated))",
];

const PURPOSE_LABEL: Readonly<Record<string, string>> = {
  main: "MAIN",
  tool_planning: "TOOL_PLANNING",
  tool_interpretation: "TOOL_INTERPRETATION",
  subagent_work: "SUBAGENT_WORK",
  context_compression: "CONTEXT_COMPRESSION",
};

function purposeLabel(p: string): string {
  return PURPOSE_LABEL[p] ?? p.toUpperCase();
}

function colorFor(index: number): string {
  return PURPOSE_PALETTE[index % PURPOSE_PALETTE.length] as string;
}

/**
 * Format a micro-USD integer as a $-prefixed string. Uses k/M suffixes for
 * larger amounts so the chip stays compact. micro-USD = USD * 1_000_000.
 */
export function formatCostMicroUsd(micro: number): string {
  const usd = micro / 1_000_000;
  if (!Number.isFinite(usd)) return "$0.00";
  const abs = Math.abs(usd);
  const sign = usd < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 10_000) return `${sign}$${(abs / 1_000).toFixed(1)}k`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(2)}k`;
  if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
  if (abs >= 0.01) return `${sign}$${abs.toFixed(2)}`;
  if (abs === 0) return "$0.00";
  return `${sign}$${abs.toFixed(4)}`;
}

function formatBucketLabel(iso: string, granularity: AgentUsagePeriod): string {
  // Display labels are derived purely from the ISO date prefix to avoid
  // timezone drift in tests. Server returns ISO-8601 timestamps.
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m === null) return iso.slice(0, 10);
  const [, y, mo, d] = m;
  if (granularity === "month") return `${y}-${mo}`;
  if (granularity === "week") return `${mo}-${d}`; // week-of label = bucket start day
  return `${mo}-${d}`;
}

interface BucketTotal {
  readonly bucket: AgentUsageBucket;
  readonly total: number;
}

/**
 * Pure presentation component for the Agents destination usage chart.
 *
 * Does NOT fetch — caller passes `usage` already loaded (typically from
 * `GET /v1/usage/agents/{agent_id}?period=...`). Period toggle is
 * controlled via `onPeriodChange` so the caller owns the re-fetch.
 */
export function AgentUsageChart({
  usage,
  period,
  onPeriodChange,
}: AgentUsageChartProps): ReactElement {
  const bucketTotals = useMemo<readonly BucketTotal[]>(
    () =>
      usage.buckets.map((bucket) => {
        let sum = 0;
        for (const key of Object.keys(bucket.by_purpose)) {
          const v = bucket.by_purpose[key] ?? 0;
          sum += v;
        }
        return { bucket, total: sum };
      }),
    [usage.buckets],
  );

  const maxBucketTotal = useMemo<number>(() => {
    let m = 0;
    for (const bt of bucketTotals) {
      if (bt.total > m) m = bt.total;
    }
    return m;
  }, [bucketTotals]);

  const hasData = maxBucketTotal > 0 && bucketTotals.length > 0;

  const containerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    padding: 16,
    backgroundColor: BACKGROUND,
    color: TEXT_PRIMARY,
    border: `1px solid ${BORDER}`,
    borderRadius: 8,
    boxSizing: "border-box",
  };

  const headerStyle: CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
    flexWrap: "wrap",
  };

  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 14px",
    borderRadius: 999,
    backgroundColor: CHIP_BG,
    border: `1px solid ${BORDER}`,
    color: TEXT_PRIMARY,
    fontVariantNumeric: "tabular-nums",
    fontWeight: 600,
  };

  const chipLabelStyle: CSSProperties = {
    color: TEXT_SECONDARY,
    fontWeight: 500,
    fontSize: "var(--font-size-xs)",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  };

  return (
    <section
      data-component="agent-usage-chart"
      data-testid="agent-usage-chart"
      aria-label="Agent usage chart"
      style={containerStyle}
    >
      <div style={headerStyle}>
        <div
          data-testid="agent-usage-cost-chip"
          aria-label={`Total cost ${formatCostMicroUsd(usage.total_cost_micro_usd)} ${usage.currency}`}
          style={chipStyle}
        >
          <span style={chipLabelStyle}>Period total</span>
          <span style={{ fontSize: "var(--font-size-lg)" }}>
            {formatCostMicroUsd(usage.total_cost_micro_usd)}
          </span>
          <span style={{ ...chipLabelStyle, fontSize: "var(--font-size-2xs)" }}>
            {usage.currency}
          </span>
        </div>
        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            color: TEXT_SECONDARY,
            fontSize: "var(--font-size-xs)",
          }}
        >
          <span>View by</span>
          <Select
            aria-label="Usage period"
            data-testid="agent-usage-period"
            value={period}
            onChange={(e) => onPeriodChange(e.target.value as AgentUsagePeriod)}
          >
            <option value="day">Daily</option>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
          </Select>
        </label>
      </div>

      {hasData ? (
        <UsageBars
          usage={usage}
          bucketTotals={bucketTotals}
          maxBucketTotal={maxBucketTotal}
        />
      ) : (
        <div
          data-testid="agent-usage-empty"
          role="status"
          style={{
            padding: 24,
            backgroundColor: SURFACE,
            border: `1px dashed ${BORDER}`,
            borderRadius: 6,
            color: TEXT_SECONDARY,
            textAlign: "center",
            fontSize: "var(--font-size-sm)",
          }}
        >
          This agent hasn&apos;t been used in this window.
        </div>
      )}

      <Legend purposes={usage.purposes} />

      {hasData ? (
        <p
          data-testid="agent-usage-sr-summary"
          style={{
            position: "absolute",
            width: 1,
            height: 1,
            margin: -1,
            padding: 0,
            overflow: "hidden",
            clip: "rect(0 0 0 0)",
            whiteSpace: "nowrap",
            border: 0,
          }}
        >
          {summaryText(usage, bucketTotals)}
        </p>
      ) : null}
    </section>
  );
}

interface UsageBarsProps {
  readonly usage: AgentUsageResponse;
  readonly bucketTotals: readonly BucketTotal[];
  readonly maxBucketTotal: number;
}

function UsageBars({
  usage,
  bucketTotals,
  maxBucketTotal,
}: UsageBarsProps): ReactElement {
  // Inline SVG — fixed viewBox, scales to container width. No chart library.
  const BAR_WIDTH = 24;
  const BAR_GAP = 12;
  const CHART_HEIGHT = 140;
  const LABEL_HEIGHT = 22;
  const TOP_PAD = 8;
  const SIDE_PAD = 8;
  const viewWidth =
    SIDE_PAD * 2 +
    bucketTotals.length * BAR_WIDTH +
    Math.max(0, bucketTotals.length - 1) * BAR_GAP;
  const viewHeight = TOP_PAD + CHART_HEIGHT + LABEL_HEIGHT;

  return (
    <div
      data-testid="agent-usage-bars"
      role="img"
      aria-label={summaryText(usage, bucketTotals)}
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
        {/* Baseline */}
        <line
          x1={SIDE_PAD}
          y1={TOP_PAD + CHART_HEIGHT}
          x2={viewWidth - SIDE_PAD}
          y2={TOP_PAD + CHART_HEIGHT}
          stroke="var(--color-border)"
          strokeWidth={1}
        />
        {bucketTotals.map((bt, bucketIndex) => {
          const x = SIDE_PAD + bucketIndex * (BAR_WIDTH + BAR_GAP);
          const heightForTotal = (bt.total / maxBucketTotal) * CHART_HEIGHT;
          let cursorY = TOP_PAD + CHART_HEIGHT;
          const segments: ReactElement[] = [];
          usage.purposes.forEach((purpose, purposeIndex) => {
            const value = bt.bucket.by_purpose[purpose] ?? 0;
            if (value <= 0) return;
            const segHeight = (value / bt.total) * heightForTotal;
            cursorY -= segHeight;
            segments.push(
              <rect
                key={`${purpose}-${bucketIndex}`}
                x={x}
                y={cursorY}
                width={BAR_WIDTH}
                height={segHeight}
                fill={colorFor(purposeIndex)}
                data-testid={`agent-usage-segment-${purpose}-${bucketIndex}`}
                data-purpose={purpose}
                data-bucket-index={bucketIndex}
              >
                <title>{`${purposeLabel(purpose)} — ${formatCostMicroUsd(value)} (${formatBucketLabel(bt.bucket.period_start, usage.granularity)})`}</title>
              </rect>,
            );
          });
          return (
            <g
              key={bt.bucket.period_start}
              data-testid="agent-usage-bar"
              data-bucket-start={bt.bucket.period_start}
            >
              {segments}
              <text
                x={x + BAR_WIDTH / 2}
                y={TOP_PAD + CHART_HEIGHT + LABEL_HEIGHT - 6}
                textAnchor="middle"
                fontSize={10}
                fill="var(--color-text-subtle)"
              >
                {formatBucketLabel(bt.bucket.period_start, usage.granularity)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

interface LegendProps {
  readonly purposes: readonly string[];
}

function Legend({ purposes }: LegendProps): ReactElement {
  return (
    <ul
      data-testid="agent-usage-legend"
      aria-label="Cost breakdown by purpose"
      style={{
        listStyle: "none",
        margin: 0,
        padding: 0,
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
      }}
    >
      {purposes.map((purpose, i) => (
        <li
          key={purpose}
          data-testid={`agent-usage-legend-${purpose}`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: "var(--font-size-xs)",
            color: TEXT_SUBTLE,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: 2,
              backgroundColor: colorFor(i),
              border: `1px solid ${BORDER}`,
            }}
          />
          <span style={{ color: TEXT_PRIMARY, fontWeight: 500 }}>
            {purposeLabel(purpose)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function summaryText(
  usage: AgentUsageResponse,
  bucketTotals: readonly BucketTotal[],
): string {
  const granLabel =
    usage.granularity === "day"
      ? "daily"
      : usage.granularity === "week"
        ? "weekly"
        : "monthly";
  const total = formatCostMicroUsd(usage.total_cost_micro_usd);
  const purposeCount = usage.purposes.length;
  const bucketCount = bucketTotals.length;
  return `Stacked bar chart of ${granLabel} agent cost across ${bucketCount} ${usage.granularity} buckets, broken down by ${purposeCount} purpose categories. Total cost ${total} ${usage.currency}.`;
}
