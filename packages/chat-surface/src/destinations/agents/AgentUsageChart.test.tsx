import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AgentUsageChart,
  formatCostMicroUsd,
  type AgentUsageResponse,
} from "./AgentUsageChart";

const SAMPLE_USAGE: AgentUsageResponse = {
  agent_id: "agent-1",
  period: { start: "2026-05-01T00:00:00Z", end: "2026-05-08T00:00:00Z" },
  granularity: "day",
  currency: "USD",
  total_cost_micro_usd: 12_750_000, // $12.75
  purposes: ["main", "tool_planning", "subagent_work"],
  buckets: [
    {
      period_start: "2026-05-01T00:00:00Z",
      by_purpose: {
        main: 1_500_000,
        tool_planning: 500_000,
        subagent_work: 250_000,
      },
    },
    {
      period_start: "2026-05-02T00:00:00Z",
      by_purpose: {
        main: 3_000_000,
        tool_planning: 1_000_000,
        subagent_work: 750_000,
      },
    },
    {
      period_start: "2026-05-03T00:00:00Z",
      by_purpose: {
        main: 4_000_000,
        tool_planning: 1_250_000,
        subagent_work: 500_000,
      },
    },
  ],
};

describe("AgentUsageChart", () => {
  it("renders the cost chip with the period total in USD", () => {
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    const chip = screen.getByTestId("agent-usage-cost-chip");
    expect(within(chip).getByText("$12.75")).toBeInTheDocument();
    expect(within(chip).getByText("USD")).toBeInTheDocument();
  });

  it("renders one stacked bar per bucket with one segment per non-zero purpose", () => {
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    const bars = screen.getAllByTestId("agent-usage-bar");
    expect(bars).toHaveLength(3);
    // Each bucket has 3 non-zero purposes => 3 segments per bar.
    expect(screen.getAllByTestId(/^agent-usage-segment-main-/).length).toBe(3);
    expect(
      screen.getAllByTestId(/^agent-usage-segment-subagent_work-/).length,
    ).toBe(3);
  });

  it("skips zero-value purpose segments", () => {
    const usage: AgentUsageResponse = {
      ...SAMPLE_USAGE,
      buckets: [
        {
          period_start: "2026-05-01T00:00:00Z",
          by_purpose: { main: 2_000_000, tool_planning: 0, subagent_work: 0 },
        },
      ],
    };
    render(
      <AgentUsageChart
        usage={usage}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    expect(screen.getAllByTestId("agent-usage-bar")).toHaveLength(1);
    expect(
      screen.queryAllByTestId(/^agent-usage-segment-tool_planning-/),
    ).toHaveLength(0);
    expect(screen.queryAllByTestId(/^agent-usage-segment-main-/)).toHaveLength(
      1,
    );
  });

  it("renders the legend with one entry per purpose", () => {
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    const legend = screen.getByTestId("agent-usage-legend");
    expect(within(legend).getByText("MAIN")).toBeInTheDocument();
    expect(within(legend).getByText("TOOL_PLANNING")).toBeInTheDocument();
    expect(within(legend).getByText("SUBAGENT_WORK")).toBeInTheDocument();
  });

  it("invokes onPeriodChange when the period select changes", () => {
    const onPeriodChange = vi.fn();
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={onPeriodChange}
      />,
    );
    fireEvent.change(screen.getByTestId("agent-usage-period"), {
      target: { value: "week" },
    });
    expect(onPeriodChange).toHaveBeenCalledWith("week");
  });

  it("renders the empty state when no buckets carry any cost", () => {
    const empty: AgentUsageResponse = {
      ...SAMPLE_USAGE,
      total_cost_micro_usd: 0,
      buckets: [
        {
          period_start: "2026-05-01T00:00:00Z",
          by_purpose: { main: 0, tool_planning: 0, subagent_work: 0 },
        },
      ],
    };
    render(
      <AgentUsageChart
        usage={empty}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    expect(screen.getByTestId("agent-usage-empty")).toHaveTextContent(
      /hasn't been used/i,
    );
    expect(screen.queryByTestId("agent-usage-bar")).toBeNull();
  });

  it("renders the empty state when the buckets list is itself empty", () => {
    const empty: AgentUsageResponse = {
      ...SAMPLE_USAGE,
      total_cost_micro_usd: 0,
      buckets: [],
    };
    render(
      <AgentUsageChart
        usage={empty}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    expect(screen.getByTestId("agent-usage-empty")).toBeInTheDocument();
  });

  it("exposes an accessible chart description via role=img + aria-label", () => {
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    const chart = screen.getByTestId("agent-usage-bars");
    expect(chart).toHaveAttribute("role", "img");
    expect(chart.getAttribute("aria-label")).toMatch(
      /Stacked bar chart of daily agent cost/i,
    );
    expect(chart.getAttribute("aria-label")).toMatch(/\$12\.75 USD/);
  });

  it("does not introduce a chart library import (inline SVG only)", () => {
    render(
      <AgentUsageChart
        usage={SAMPLE_USAGE}
        period="day"
        onPeriodChange={() => undefined}
      />,
    );
    // Presence of an <svg> child under the chart container is the proxy.
    const chart = screen.getByTestId("agent-usage-bars");
    expect(chart.querySelector("svg")).not.toBeNull();
  });
});

describe("formatCostMicroUsd", () => {
  it("formats sub-dollar amounts with two decimals", () => {
    expect(formatCostMicroUsd(250_000)).toBe("$0.25");
  });

  it("formats whole-dollar amounts with two decimals", () => {
    expect(formatCostMicroUsd(12_750_000)).toBe("$12.75");
  });

  it("formats thousands with k suffix and two decimals", () => {
    expect(formatCostMicroUsd(2_500_000_000)).toBe("$2.50k");
  });

  it("formats large amounts with M suffix", () => {
    expect(formatCostMicroUsd(5_500_000_000_000)).toBe("$5.50M");
  });

  it("formats zero as $0.00", () => {
    expect(formatCostMicroUsd(0)).toBe("$0.00");
  });
});
