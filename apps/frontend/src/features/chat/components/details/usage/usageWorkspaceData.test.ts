import type {
  BudgetMeResponse,
  UsageOrgResponse,
} from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";

import { USAGE_PALETTE_OTHER_KEY } from "./usagePalette";
import {
  pickTopUsers,
  pivotByDayByUser,
  selectPlanLimit,
} from "./usageWorkspaceData";

const period = {
  start: "2026-04-05T00:00:00Z",
  end: "2026-05-05T00:00:00Z",
};

function makeUsage(byUser: Array<Record<string, unknown>>): UsageOrgResponse {
  return {
    period,
    currency: "USD",
    total: {
      input: 0,
      output: 0,
      cached_input: 0,
      total: 0,
      runs_count: 0,
      cost_micro_usd: null,
    },
    by_day: [],
    by_model: [],
    by_user: byUser as never,
    cold_start_fallback: false,
  };
}

describe("pickTopUsers", () => {
  it("ranks by cost when any row has cost", () => {
    const usage = makeUsage([
      makeUserRow("u1", "Alice", 100, 5_000_000),
      makeUserRow("u2", "Bob", 200, 1_000_000),
    ]);
    const result = pickTopUsers({ orgUsage: usage });
    expect(result.rankBy).toBe("cost");
    expect(result.top.map((r) => r.user_id)).toEqual(["u1", "u2"]);
    expect(result.other).toBeNull();
  });

  it("falls back to tokens when no row has cost", () => {
    const usage = makeUsage([
      makeUserRow("u1", "Alice", 100, null),
      makeUserRow("u2", "Bob", 200, null),
    ]);
    const result = pickTopUsers({ orgUsage: usage });
    expect(result.rankBy).toBe("tokens");
    expect(result.top.map((r) => r.user_id)).toEqual(["u2", "u1"]);
  });

  it("folds the long tail into a single 'other' row", () => {
    const usage = makeUsage(
      Array.from({ length: 10 }, (_, i) =>
        makeUserRow(`u${i}`, `User ${i}`, 100 + i, 1_000 * (10 - i)),
      ),
    );
    const result = pickTopUsers({ orgUsage: usage, limit: 3 });
    expect(result.top).toHaveLength(3);
    expect(result.other?.user_id).toBe(USAGE_PALETTE_OTHER_KEY);
    // 'Other' aggregates the 7 tail rows.
    expect(result.other?.runs_count ?? 0).toBeGreaterThan(0);
  });

  it("returns no 'other' row when the user count is under the limit", () => {
    const usage = makeUsage([makeUserRow("u1", "Alice", 100, null)]);
    const result = pickTopUsers({ orgUsage: usage, limit: 6 });
    expect(result.other).toBeNull();
  });

  it("respects an explicit rankBy override", () => {
    const usage = makeUsage([
      makeUserRow("u1", "Alice", 100, 5_000_000),
      makeUserRow("u2", "Bob", 200, 1_000_000),
    ]);
    const result = pickTopUsers({ orgUsage: usage, rankBy: "tokens" });
    expect(result.rankBy).toBe("tokens");
    expect(result.top.map((r) => r.user_id)).toEqual(["u2", "u1"]);
  });
});

describe("pivotByDayByUser", () => {
  it("returns empty when there are no daily rows", () => {
    const usage = makeUsage([]);
    expect(pivotByDayByUser({ orgUsage: usage, unit: "tokens" })).toHaveLength(
      0,
    );
  });

  it("emits one row per day with a 'total' series in tokens", () => {
    const usage: UsageOrgResponse = {
      ...makeUsage([]),
      by_day: [
        {
          day: "2026-05-01",
          input: 100,
          output: 50,
          cached_input: 0,
          total: 150,
          runs_count: 2,
          cost_micro_usd: 500_000,
        },
        {
          day: "2026-05-02",
          input: 80,
          output: 20,
          cached_input: 0,
          total: 100,
          runs_count: 1,
          cost_micro_usd: 250_000,
        },
      ],
    };
    const tokens = pivotByDayByUser({ orgUsage: usage, unit: "tokens" });
    expect(tokens).toHaveLength(2);
    expect(tokens[0].total).toBe(150);
    expect(tokens[1].total).toBe(100);
    const usd = pivotByDayByUser({ orgUsage: usage, unit: "usd" });
    expect(usd[0].total).toBeCloseTo(0.5);
    expect(usd[1].total).toBeCloseTo(0.25);
  });

  it("treats null cost as 0 in usd mode", () => {
    const usage: UsageOrgResponse = {
      ...makeUsage([]),
      by_day: [
        {
          day: "2026-05-01",
          input: 0,
          output: 0,
          cached_input: 0,
          total: 0,
          runs_count: 0,
          cost_micro_usd: null,
        },
      ],
    };
    const usd = pivotByDayByUser({ orgUsage: usage, unit: "usd" });
    expect(usd[0].total).toBe(0);
  });
});

describe("selectPlanLimit", () => {
  it("returns null without any budgets", () => {
    expect(selectPlanLimit(null)).toBeNull();
    expect(selectPlanLimit({ currency: "USD", budgets: [] })).toBeNull();
  });

  it("prefers the org-month-cost budget", () => {
    const budgets: BudgetMeResponse = {
      currency: "USD",
      budgets: [
        budgetRow("user-1", "user", "month", 100_000_000, null),
        budgetRow("org-1", "org", "month", 250_000_000, null),
      ],
    };
    const overlay = selectPlanLimit(budgets);
    expect(overlay?.unit).toBe("usd");
    expect(overlay?.value).toBe(250);
  });

  it("falls back to tokens when no cost limit exists", () => {
    const budgets: BudgetMeResponse = {
      currency: "USD",
      budgets: [budgetRow("org-1", "org", "month", null, 1_000_000)],
    };
    const overlay = selectPlanLimit(budgets);
    expect(overlay?.unit).toBe("tokens");
    expect(overlay?.value).toBe(1_000_000);
  });

  it("ignores non-monthly or non-org budgets", () => {
    const budgets: BudgetMeResponse = {
      currency: "USD",
      budgets: [
        budgetRow("user-1", "user", "month", 100_000_000, null),
        budgetRow("org-day", "org", "day", 5_000_000, null),
      ],
    };
    expect(selectPlanLimit(budgets)).toBeNull();
  });
});

function makeUserRow(
  userId: string,
  displayName: string | null,
  totalTokens: number,
  costMicroUsd: number | null,
) {
  return {
    conversation_id: userId,
    title: displayName,
    input: totalTokens,
    output: 0,
    cached_input: 0,
    total: totalTokens,
    runs_count: 1,
    cost_micro_usd: costMicroUsd,
  };
}

function budgetRow(
  id: string,
  scope: "org" | "user",
  budgetPeriod: "day" | "month",
  limitMicroUsd: number | null,
  limitTokens: number | null,
) {
  return {
    id,
    scope,
    period: budgetPeriod,
    enforcement: "soft" as const,
    status: "active" as const,
    limit_micro_usd: limitMicroUsd,
    limit_tokens: limitTokens,
    current_micro_usd: 0,
    current_tokens: 0,
    remaining_micro_usd: limitMicroUsd,
    remaining_tokens: limitTokens,
    period_start: "2026-05-01",
    period_end: "2026-05-31",
  };
}
