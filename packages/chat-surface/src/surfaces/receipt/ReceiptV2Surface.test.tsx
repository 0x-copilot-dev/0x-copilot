import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RunReceiptV2 } from "@0x-copilot/api-types";

import { ReceiptV2LaunchCard, ReceiptV2Surface } from "./ReceiptV2Surface";

function receipt(overrides: Partial<RunReceiptV2> = {}): RunReceiptV2 {
  return {
    run_id: "run_receipt_v2_01",
    status: "completed",
    generated_at: "2026-07-25T00:00:04Z",
    fold_ref: "ledger://run_receipt_v2_01@4",
    operations: { requested: 0, completed: 0, failed: 0, blocked: 0 },
    artifacts: { created: 1, revised: 0, promoted: 0 },
    reads: { completed: 2 },
    effects: {
      proposed: 1,
      approved: 1,
      rejected: 0,
      applied: 1,
      partial: 0,
      held: 0,
      indeterminate: 0,
      external: 1,
      internal: 0,
      unclassified: 0,
    },
    gates: { opened: 1, resolved: 1, pending: 0 },
    usage: {
      totals_by_purpose: [
        { purpose: "run", records: 3, tokens_in: 8, tokens_out: 5 },
      ],
      references: [],
    },
    unresolved_warnings: [],
    ...overrides,
  };
}

describe("ReceiptV2Surface", () => {
  it("renders only canonical receipt counts in the Studio surface", () => {
    render(<ReceiptV2Surface receipt={receipt()} />);

    expect(screen.getByTestId("receipt-v2-status")).toHaveTextContent(
      "Completed",
    );
    expect(screen.getByText(/2 completed/)).toBeInTheDocument();
    expect(screen.getByText(/3 recorded calls/)).toBeInTheDocument();
    expect(screen.queryByTestId("receipt-v2-open-studio")).toBeNull();
  });

  it("launches only after an explicit user action", () => {
    const onOpen = vi.fn();
    render(<ReceiptV2LaunchCard receipt={receipt()} onOpen={onOpen} />);

    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.getByTestId("receipt-v2-launch")).toHaveTextContent(
      "Run receipt",
    );
    expect(screen.getByTestId("receipt-v2-launch")).not.toHaveTextContent(
      "This receipt was assembled",
    );
    fireEvent.click(screen.getByTestId("receipt-v2-open"));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("uses generic warning copy rather than ledger payload detail", () => {
    render(
      <ReceiptV2Surface
        receipt={receipt({
          unresolved_warnings: [{ code: "malformed_events", count: 1 }],
        })}
      />,
    );

    expect(screen.getByTestId("receipt-v2-warning")).toHaveTextContent(
      "Some ledger entries could not be included in this receipt.",
    );
    expect(screen.queryByText("malformed_events")).toBeNull();
  });
});
