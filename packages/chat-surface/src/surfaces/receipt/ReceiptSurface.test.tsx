import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { RunReceipt } from "@0x-copilot/api-types";

import {
  ReceiptSurface,
  RECEIPT_ASSEMBLED_LINE,
  RECEIPT_DECIDED_ON_SURFACE_LINE,
  serializeReceipt,
} from "./ReceiptSurface";

function receipt(overrides: Partial<RunReceipt> = {}): RunReceipt {
  return {
    run_id: "run00000001abcdef",
    surface_id: "receipt://run00000001abcdef",
    fold_ref: "ledger://run00000001abcdef@16",
    generated_at: "2026-01-01T00:00:16Z",
    tiles: {
      reads_auto_ran: 2,
      writes_proposed: 3,
      writes_approved: 1,
      holds_untouched: 2,
    },
    rows: [
      {
        ledger_id: "rrun·004",
        event_type: "read.executed",
        title: "linear · get_issue",
        attribution: "auto_ran",
        at: "2026-01-01T00:00:04Z",
      },
      {
        ledger_id: "rrun·016",
        event_type: "write.applied",
        title: "ENG-142 Fix reconnect",
        attribution: "auto_applied",
        at: "2026-01-01T00:00:16Z",
      },
    ],
    ...overrides,
  };
}

describe("ReceiptSurface", () => {
  it("renders four stat tiles with the fold counts", () => {
    render(<ReceiptSurface receipt={receipt()} emittedSeq={17} />);
    expect(screen.getByTestId("receipt-tile-reads_auto_ran")).toHaveTextContent(
      "2",
    );
    expect(
      screen.getByTestId("receipt-tile-writes_proposed"),
    ).toHaveTextContent("3");
    expect(
      screen.getByTestId("receipt-tile-writes_approved"),
    ).toHaveTextContent("1");
    expect(
      screen.getByTestId("receipt-tile-holds_untouched"),
    ).toHaveTextContent("2");
  });

  it("maps attributions to their display labels (incl. allow-always)", () => {
    render(<ReceiptSurface receipt={receipt()} emittedSeq={17} />);
    const labels = screen
      .getAllByTestId("receipt-row-attribution")
      .map((el) => el.textContent);
    expect(labels).toContain("auto-ran");
    expect(labels).toContain("auto-sent under allow-always");
  });

  it("renders both contract sentences verbatim", () => {
    render(<ReceiptSurface receipt={receipt()} emittedSeq={17} />);
    expect(screen.getByTestId("receipt-decided-on-surface")).toHaveTextContent(
      RECEIPT_DECIDED_ON_SURFACE_LINE,
    );
    expect(screen.getByTestId("receipt-assembled")).toHaveTextContent(
      RECEIPT_ASSEMBLED_LINE,
    );
    // The "nothing was approved from chat" promise is present.
    expect(RECEIPT_DECIDED_ON_SURFACE_LINE).toContain(
      "nothing was approved from chat",
    );
  });

  it("Copy receipt invokes onCopyText with the serialized rows", () => {
    const onCopyText = vi.fn();
    const r = receipt();
    render(
      <ReceiptSurface receipt={r} emittedSeq={17} onCopyText={onCopyText} />,
    );
    fireEvent.click(screen.getByTestId("receipt-copy"));
    expect(onCopyText).toHaveBeenCalledWith(serializeReceipt(r));
    expect(onCopyText.mock.calls[0][0]).toContain(RECEIPT_ASSEMBLED_LINE);
  });

  it("renders a hostile row title as text (no markup injection)", () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const r = receipt({
      rows: [
        {
          ledger_id: "rrun·004",
          event_type: "read.executed",
          title: hostile,
          attribution: "auto_ran",
          at: "2026-01-01T00:00:04Z",
        },
      ],
    });
    render(<ReceiptSurface receipt={r} emittedSeq={17} />);
    const title = screen.getByTestId("receipt-row-title");
    expect(title.textContent).toBe(hostile);
    expect(title.querySelector("img")).toBeNull();
  });

  it("omits the Copy button when no onCopyText is supplied", () => {
    render(<ReceiptSurface receipt={receipt()} />);
    expect(screen.queryByTestId("receipt-copy")).toBeNull();
  });
});
