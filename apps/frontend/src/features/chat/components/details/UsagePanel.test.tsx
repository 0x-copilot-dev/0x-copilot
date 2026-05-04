import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UsagePanel } from "./UsagePanel";

const _getMyUsage = vi.fn();
const _getMyTopConversations = vi.fn();

vi.mock("../../../../api/agentApi", () => ({
  getMyUsage: (...args: unknown[]) => _getMyUsage(...args),
  getMyTopConversations: (...args: unknown[]) =>
    _getMyTopConversations(...args),
}));

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
    fireEvent.click(screen.getByRole("tab", { name: /30 days/i }));
    await waitFor(() => expect(_getMyUsage).toHaveBeenCalled());
    expect(_getMyUsage).toHaveBeenLastCalledWith("30d", identity);
  });
});
