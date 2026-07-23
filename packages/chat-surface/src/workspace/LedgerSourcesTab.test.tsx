import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { LedgerSourcesTab } from "./LedgerSourcesTab";
import type { LedgerSourcesProjection } from "../destinations/run/projectLedgerSources";

const projection: LedgerSourcesProjection = {
  total: 2,
  groups: [
    {
      connector: "linear",
      rows: [
        {
          op: "get_issue",
          title: "ENG-142",
          at: "2026-01-01T00:00:04Z",
          ledgerId: "rrun·004",
          latencyMs: 142,
          qualifier: "auto-ran (read)",
        },
        {
          op: "list_issues",
          title: "linear · list_issues",
          at: "2026-01-01T00:00:06Z",
          ledgerId: "rrun·006",
          latencyMs: null,
          qualifier: "auto-ran (read)",
        },
      ],
    },
  ],
};

describe("LedgerSourcesTab", () => {
  it("renders a connector group and its rows", () => {
    render(<LedgerSourcesTab ledgerSources={projection} />);
    expect(screen.getByTestId("ledger-sources-tab")).toBeInTheDocument();
    expect(screen.getByTestId("ledger-sources-group")).toHaveTextContent(
      "Linear",
    );
    expect(screen.getAllByTestId("ledger-sources-row")).toHaveLength(2);
    expect(
      screen.getAllByTestId("ledger-sources-qualifier")[0],
    ).toHaveTextContent("auto-ran (read)");
    expect(
      screen.getAllByTestId("ledger-sources-ledger-id")[0],
    ).toHaveTextContent("rrun·004");
  });

  it("omits latency when null", () => {
    render(<LedgerSourcesTab ledgerSources={projection} />);
    // Only the first row has a latency chip.
    expect(screen.getAllByTestId("ledger-sources-latency")).toHaveLength(1);
  });

  it("shows the empty state when nothing was read", () => {
    render(<LedgerSourcesTab ledgerSources={{ total: 0, groups: [] }} />);
    expect(screen.getByTestId("ledger-sources-empty")).toHaveTextContent(
      "Sources will appear here as the run reads your tools.",
    );
  });
});
