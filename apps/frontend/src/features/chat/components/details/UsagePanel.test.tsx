import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UsagePanel } from "./UsagePanel";

const _getMyUsage = vi.fn();
const _getMyTopConversations = vi.fn();
const _getOrgUsage = vi.fn();
const _getMyBudgets = vi.fn();

vi.mock("../../../../api/agentApi", () => ({
  getMyUsage: (...args: unknown[]) => _getMyUsage(...args),
  getMyTopConversations: (...args: unknown[]) =>
    _getMyTopConversations(...args),
  getOrgUsage: (...args: unknown[]) => _getOrgUsage(...args),
  getMyBudgets: (...args: unknown[]) => _getMyBudgets(...args),
}));

beforeEach(() => {
  _getMyUsage.mockReset();
  _getMyTopConversations.mockReset();
  _getOrgUsage.mockReset();
  _getMyBudgets.mockReset();
});

const identity = { orgId: "org_a", userId: "user_1" };

function _seed({ withCost = true }: { withCost?: boolean } = {}) {
  _getMyUsage.mockResolvedValue({
    period: { start: "2026-04-27T00:00:00Z", end: "2026-05-04T12:00:00Z" },
    currency: "USD",
    total: {
      input: 1_000,
      output: 500,
      cached_input: 100,
      total: 1_600,
      runs_count: 4,
      cost_micro_usd: withCost ? 12_345 : null,
    },
    by_day: [],
    by_model: [
      {
        provider: "openai",
        model: "gpt-5.4-mini",
        input: 1_000,
        output: 500,
        cached_input: 100,
        total: 1_600,
        runs_count: 4,
        cost_micro_usd: withCost ? 12_345 : null,
      },
    ],
    cold_start_fallback: false,
  });
  _getMyTopConversations.mockResolvedValue([
    {
      conversation_id: "conv-1",
      title: "demo",
      input: 800,
      output: 200,
      cached_input: 0,
      total: 1_000,
      runs_count: 3,
      cost_micro_usd: withCost ? 9_000 : null,
    },
  ]);
}

describe("UsagePanel", () => {
  it("renders cost columns when pricing is configured", async () => {
    _seed({ withCost: true });
    render(<UsagePanel identity={identity} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText(/4 runs/)).toBeInTheDocument());
    // Cost cell rendered.
    expect(screen.getByText(/By model/i)).toBeInTheDocument();
  });

  it("hides cost columns when every row has null cost", async () => {
    _seed({ withCost: false });
    render(<UsagePanel identity={identity} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText(/4 runs/)).toBeInTheDocument());
    // The "Cost" header must not appear when every value is null.
    expect(screen.queryByRole("columnheader", { name: /cost/i })).toBeNull();
  });

  it("refetches when the period switches", async () => {
    _seed({ withCost: true });
    render(<UsagePanel identity={identity} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText(/4 runs/)).toBeInTheDocument());
    _getMyUsage.mockClear();
    // Default period is "30 days" (matches design's workspace default), so
    // click "7 days" to force a refetch.
    fireEvent.click(screen.getByRole("tab", { name: /7 days/i }));
    await waitFor(() => expect(_getMyUsage).toHaveBeenCalled());
    expect(_getMyUsage).toHaveBeenLastCalledWith("7d", identity);
  });

  it("switches to the workspace tab and loads org usage", async () => {
    _seed({ withCost: true });
    _getOrgUsage.mockResolvedValue({
      period: { start: "2026-04-05T00:00:00Z", end: "2026-05-05T00:00:00Z" },
      currency: "USD",
      total: {
        input: 100,
        output: 50,
        cached_input: 0,
        total: 150,
        runs_count: 2,
        cost_micro_usd: 0,
      },
      by_day: [
        {
          day: "2026-05-04",
          input: 100,
          output: 50,
          cached_input: 0,
          total: 150,
          runs_count: 2,
          cost_micro_usd: 0,
        },
      ],
      by_model: [],
      by_user: [],
      cold_start_fallback: false,
    });
    _getMyBudgets.mockResolvedValue({ currency: "USD", budgets: [] });
    render(<UsagePanel identity={identity} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText(/4 runs/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /workspace/i }));
    await waitFor(() => expect(_getOrgUsage).toHaveBeenCalled());
    expect(_getOrgUsage).toHaveBeenCalledWith("30d", identity);
  });

  it("renders the admin-only state on 403 from /v1/usage/org", async () => {
    _seed({ withCost: true });
    _getOrgUsage.mockRejectedValue(new Error("403 Forbidden"));
    _getMyBudgets.mockResolvedValue({ currency: "USD", budgets: [] });
    render(<UsagePanel identity={identity} onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText(/4 runs/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /workspace/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/Workspace usage is admin-only/i),
      ).toBeInTheDocument(),
    );
  });
});
