import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  TcBulkApplyBar,
  bulkApplyLabel,
  bulkApplyPledge,
  bulkRetryLabel,
} from "./TcBulkApplyBar";
import type { ApplyContext, RecoveryContext } from "./rowsetReviewModel";

function applyAction(overrides: Partial<ApplyContext> = {}): ApplyContext {
  return {
    kind: "apply",
    stageId: "stage_1",
    revision: 2,
    proposalDigest: "a".repeat(64),
    targetDigest: "b".repeat(64),
    rowKeys: ["a", "c"],
    basisSequence: 9,
    basisLedgerId: null,
    label: bulkApplyLabel(2),
    message: bulkApplyPledge,
    accessibleLabel: "Apply exactly 2 approved rows",
    pending: false,
    disabled: false,
    ...overrides,
  };
}

function recoveryAction(
  overrides: Partial<RecoveryContext> = {},
): RecoveryContext {
  return {
    kind: "retry_failed",
    stageId: "stage_1",
    revision: 2,
    proposalDigest: "a".repeat(64),
    targetDigest: "b".repeat(64),
    rowKeys: ["failed-a", "failed-c"],
    failedRowKeys: ["failed-a", "failed-c"],
    basisSequence: 12,
    basisLedgerId: "rrun1·012",
    label: bulkRetryLabel(2),
    message: "Some writes failed. Applied rows are safe — nothing lost.",
    accessibleLabel: "Retry exactly 2 failed rows",
    pending: false,
    disabled: false,
    ...overrides,
  };
}

describe("TcBulkApplyBar", () => {
  it("renders the projected apply action without inspecting rows", () => {
    render(<TcBulkApplyBar action={applyAction()} onApply={vi.fn()} />);
    expect(screen.getByTestId("tc-bulk-apply")).toHaveTextContent(
      bulkApplyLabel(2),
    );
    expect(screen.getByTestId("tc-bulk-pledge")).toHaveTextContent(
      bulkApplyPledge,
    );
  });

  it("returns the same immutable action object unchanged", () => {
    const action = recoveryAction();
    const onApply = vi.fn();
    render(<TcBulkApplyBar action={action} onApply={onApply} />);

    fireEvent.click(screen.getByTestId("tc-bulk-retry"));

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledWith(action);
    expect(onApply.mock.calls[0][0]).toBe(action);
  });

  it("keeps pending action scope visible but disabled", () => {
    render(
      <TcBulkApplyBar
        action={recoveryAction({
          pending: true,
          disabled: true,
          label: "Retrying…",
        })}
        onApply={vi.fn()}
      />,
    );
    const button = screen.getByTestId("tc-bulk-retry") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button).toHaveTextContent("Retrying…");
    expect(screen.getByTestId("tc-bulk-apply-bar")).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  it("never invokes a disabled zero-scope recovery action", () => {
    const onApply = vi.fn();
    render(
      <TcBulkApplyBar
        action={recoveryAction({
          rowKeys: [],
          failedRowKeys: [],
          disabled: true,
          label: bulkRetryLabel(0),
        })}
        onApply={onApply}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-bulk-retry"));
    expect(onApply).not.toHaveBeenCalled();
  });
});
