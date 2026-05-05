/**
 * PR 4.5 — Pure transforms over `UsageOrgResponse` and `BudgetMeResponse` for the
 * workspace usage chart and top-users table.
 *
 * `pickTopUsers` ranks `by_user` by the cost-or-tokens key in use and keeps
 * the top-N. `pivotByDayByUser` projects the response into the shape recharts
 * expects for an `<AreaChart>`. `selectPlanLimitMicroUsd` chooses the most
 * relevant `org`-scoped budget for the plan-limit overlay.
 *
 * All transforms are pure and unit-tested; no React, no fetch.
 */

import type {
  BudgetMeResponse,
  BudgetMeRow,
  UsageConversationRow,
  UsageDailyRow,
  UsageOrgResponse,
} from "@enterprise-search/api-types";

import { USAGE_PALETTE_OTHER_KEY } from "./usagePalette";

/**
 * Server `by_user` rows reuse `UsageConversationRow`. The `conversation_id`
 * field carries the user_id and `title` carries the display name. We expose
 * a typed alias to make consumer call-sites readable.
 */
export interface UsageUserRow {
  readonly user_id: string;
  readonly display_name: string | null;
  readonly input: number;
  readonly output: number;
  readonly cached_input: number;
  readonly total: number;
  readonly runs_count: number;
  readonly cost_micro_usd: number | null;
}

export function asUserRow(row: UsageConversationRow): UsageUserRow {
  return {
    user_id: row.conversation_id,
    display_name: row.title,
    input: row.input,
    output: row.output,
    cached_input: row.cached_input,
    total: row.total,
    runs_count: row.runs_count,
    cost_micro_usd: row.cost_micro_usd,
  };
}

export type UsageRankBy = "cost" | "tokens";

export interface PickTopUsersInput {
  readonly orgUsage: UsageOrgResponse;
  /** Defaults to 6 (matches the chart's stack budget). */
  readonly limit?: number;
  /**
   * Defaults to `cost` when any row carries a non-null cost; falls back to
   * `tokens`. Lets callers override to drive the table's sort header.
   */
  readonly rankBy?: UsageRankBy;
}

export interface TopUsersResult {
  readonly top: ReadonlyArray<UsageUserRow>;
  readonly other: UsageUserRow | null;
  readonly rankBy: UsageRankBy;
}

export function pickTopUsers(input: PickTopUsersInput): TopUsersResult {
  const limit = input.limit ?? 6;
  const rows = input.orgUsage.by_user.map(asUserRow);
  const rankBy: UsageRankBy =
    input.rankBy ?? (anyRowHasCost(rows) ? "cost" : "tokens");
  const ranked = [...rows].sort(
    (a, b) => weight(b, rankBy) - weight(a, rankBy),
  );
  const top = ranked.slice(0, limit);
  const tail = ranked.slice(limit);
  const other = tail.length > 0 ? aggregateOther(tail) : null;
  return { top, other, rankBy };
}

function anyRowHasCost(rows: ReadonlyArray<UsageUserRow>): boolean {
  return rows.some((row) => row.cost_micro_usd !== null);
}

function weight(row: UsageUserRow, rankBy: UsageRankBy): number {
  if (rankBy === "cost") {
    return row.cost_micro_usd ?? 0;
  }
  return row.total;
}

function aggregateOther(rows: ReadonlyArray<UsageUserRow>): UsageUserRow {
  let input = 0;
  let output = 0;
  let cached_input = 0;
  let total = 0;
  let runs_count = 0;
  let cost_micro_usd: number | null = null;
  for (const row of rows) {
    input += row.input;
    output += row.output;
    cached_input += row.cached_input;
    total += row.total;
    runs_count += row.runs_count;
    if (row.cost_micro_usd !== null) {
      cost_micro_usd = (cost_micro_usd ?? 0) + row.cost_micro_usd;
    }
  }
  return {
    user_id: USAGE_PALETTE_OTHER_KEY,
    display_name: "Other",
    input,
    output,
    cached_input,
    total,
    runs_count,
    cost_micro_usd,
  };
}

/**
 * Recharts `<AreaChart data>` shape. One row per day, one numeric column per
 * series. The y-axis renders dollars when the budget overlay is in dollars,
 * tokens otherwise — matched by `chartUnit` below.
 */
export interface UsageDailyPoint {
  readonly day: string;
  readonly [seriesKey: string]: number | string;
}

export type ChartUnit = "usd" | "tokens";

export interface PivotByDayByUserInput {
  readonly orgUsage: UsageOrgResponse;
  readonly unit: ChartUnit;
  /**
   * Optional set of series keys (user_ids + 'other'). When omitted, the chart
   * renders one synthetic `total` series — useful when the server doesn't
   * return per-day-per-user breakdowns yet (the current default for
   * `/v1/usage/org`).
   */
  readonly seriesKeys?: ReadonlyArray<string>;
}

/**
 * Server reality vs. design intent: today `/v1/usage/org` returns
 * `by_day` (org-wide daily totals) and `by_user` (per-user period totals)
 * but **not** a per-day-per-user grid. Until that arrives, we render the
 * single org-wide stack on the chart. The top-users table still gets full
 * per-user weight from `by_user`. When the server later adds per-day-per-user
 * rollups we plumb them through `seriesKeys` without a visual change to
 * existing callers.
 */
export function pivotByDayByUser(
  input: PivotByDayByUserInput,
): ReadonlyArray<UsageDailyPoint> {
  return input.orgUsage.by_day.map((row: UsageDailyRow) => ({
    day: shortDay(row.day),
    total: dailyValue(row, input.unit),
  }));
}

function dailyValue(row: UsageDailyRow, unit: ChartUnit): number {
  if (unit === "usd") {
    return microUsdToUsd(row.cost_micro_usd);
  }
  return row.total;
}

function microUsdToUsd(micro: number | null): number {
  if (micro === null) {
    return 0;
  }
  return micro / 1_000_000;
}

const DAY_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
});

function shortDay(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return DAY_FORMAT.format(parsed);
}

export interface PlanLimitOverlay {
  readonly value: number;
  readonly unit: ChartUnit;
  readonly label: string;
  readonly source: BudgetMeRow;
}

/**
 * Pick the `org`-scoped, monthly budget if present (cost preferred, tokens
 * fallback). Returns `null` when no org budget applies — chart omits the
 * overlay and y-axis fits its own data.
 */
export function selectPlanLimit(
  budgets: BudgetMeResponse | null,
): PlanLimitOverlay | null {
  if (!budgets) {
    return null;
  }
  const org = budgets.budgets.find(
    (entry) =>
      entry.scope === "org" &&
      entry.status === "active" &&
      entry.period === "month",
  );
  if (!org) {
    return null;
  }
  if (org.limit_micro_usd !== null) {
    return {
      value: org.limit_micro_usd / 1_000_000,
      unit: "usd",
      label: "Plan limit",
      source: org,
    };
  }
  if (org.limit_tokens !== null) {
    return {
      value: org.limit_tokens,
      unit: "tokens",
      label: "Plan limit",
      source: org,
    };
  }
  return null;
}
