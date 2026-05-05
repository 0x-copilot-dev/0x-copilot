/**
 * PR 4.5 — Workspace usage chart.
 *
 * Stacked area chart of daily usage for the selected period. When the server
 * returns a per-day-per-user grid (future enhancement), the chart paints one
 * stack per top-user plus an "Other" fold-in. Until then, it renders a single
 * org-wide stack — see `pivotByDayByUser` for the contract.
 *
 * Plan-limit overlay (a horizontal threshold line) renders when an org budget
 * is configured. Reduce-motion respected through Recharts' built-in MQ check
 * plus the design-system `[data-reduce-motion]` overrides.
 *
 * Pure presentation: no fetch, no derived async work.
 */

import type {
  BudgetMeResponse,
  UsageOrgResponse,
} from "@enterprise-search/api-types";
import { Card } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  pivotByDayByUser,
  selectPlanLimit,
  type ChartUnit,
} from "./usageWorkspaceData";
import { usagePalette } from "./usagePalette";
import { formatMicroUsd } from "../../../utils/formatMicroUsd";

export interface UsageWorkspaceChartProps {
  orgUsage: UsageOrgResponse;
  budgets: BudgetMeResponse | null;
}

const ACCENT = "var(--color-accent, #d97757)";
const ACCENT_FILL = "var(--color-accent-soft, rgba(217, 119, 87, 0.18))";
const GRID = "var(--color-border-soft, #232325)";
const TEXT_DIM = "var(--color-text-subtle, #7e7e84)";

export function UsageWorkspaceChart({
  orgUsage,
  budgets,
}: UsageWorkspaceChartProps): ReactElement {
  const planLimit = useMemo(() => selectPlanLimit(budgets), [budgets]);
  const unit: ChartUnit = planLimit?.unit ?? "tokens";

  const data = useMemo(
    () => pivotByDayByUser({ orgUsage, unit }),
    [orgUsage, unit],
  );

  const palette = useMemo(
    () => usagePalette({ keys: ["total"], includeOther: false }),
    [],
  );

  if (data.length === 0) {
    return (
      <Card tone="muted" className="details-panel__section">
        <p className="details-panel__empty">
          No usage in the last {labelForRange(orgUsage)}.
        </p>
      </Card>
    );
  }

  return (
    <Card tone="default" className="details-panel__section">
      <div className="details-panel__chart">
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart
            data={[...data]}
            margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient id="atlas-usage-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={ACCENT} stopOpacity={0.55} />
                <stop offset="100%" stopColor={ACCENT} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID} strokeDasharray="2 4" />
            <XAxis
              dataKey="day"
              tick={{ fill: TEXT_DIM, fontSize: 11 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              tick={{ fill: TEXT_DIM, fontSize: 11 }}
              axisLine={{ stroke: GRID }}
              tickLine={false}
              tickFormatter={(value: number) => axisFormat(value, unit)}
              width={48}
            />
            <Tooltip
              cursor={{ stroke: GRID }}
              contentStyle={tooltipStyle()}
              formatter={(value: number) => tooltipFormat(value, unit)}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, color: TEXT_DIM }}
              formatter={() => (unit === "usd" ? "Cost" : "Tokens")}
            />
            <Area
              type="monotone"
              dataKey="total"
              stackId="1"
              stroke={palette.total ?? ACCENT}
              fill="url(#atlas-usage-fill)"
              strokeWidth={1.5}
              isAnimationActive={false}
            />
            {planLimit ? (
              <ReferenceLine
                y={planLimit.value}
                stroke="var(--color-warn, #d9a857)"
                strokeDasharray="4 4"
                label={{
                  value: planLimitLabel(planLimit.value, planLimit.unit),
                  position: "insideTopRight",
                  fill: "var(--color-warn, #d9a857)",
                  fontSize: 11,
                }}
              />
            ) : null}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function labelForRange(orgUsage: UsageOrgResponse): string {
  const dayCount = orgUsage.by_day.length;
  if (dayCount === 0) {
    return "selected period";
  }
  if (dayCount === 1) {
    return "day";
  }
  return `${dayCount} days`;
}

function planLimitLabel(value: number, unit: ChartUnit): string {
  if (unit === "usd") {
    return `Plan limit · ${formatMicroUsd(value * 1_000_000)}`;
  }
  return `Plan limit · ${value.toLocaleString()} tok`;
}

function axisFormat(value: number, unit: ChartUnit): string {
  if (unit === "usd") {
    return value === 0
      ? "$0"
      : value >= 1
        ? `$${value.toFixed(0)}`
        : `$${value.toFixed(2)}`;
  }
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}k`;
  return value.toString();
}

function tooltipFormat(value: number, unit: ChartUnit): [string, string] {
  if (unit === "usd") {
    return [formatMicroUsd(value * 1_000_000), "Cost"];
  }
  return [`${value.toLocaleString()} tok`, "Tokens"];
}

function tooltipStyle(): React.CSSProperties {
  return {
    background: "var(--color-surface-2, #222224)",
    border: "1px solid var(--color-border, #2a2a2c)",
    borderRadius: 6,
    fontSize: 12,
    color: "var(--color-text, #ededee)",
    padding: "6px 10px",
    boxShadow: "0 4px 12px rgba(0,0,0,0.32)",
  };
}

export { ACCENT_FILL };
