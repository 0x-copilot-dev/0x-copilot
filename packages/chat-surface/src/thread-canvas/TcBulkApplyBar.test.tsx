import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  TcBulkApplyBar,
  bulkApplyLabel,
  bulkApplyPledge,
} from "./TcBulkApplyBar";
import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";

function row(rowKey: string, stance: "will_apply" | "held"): LedgerStagedRow {
  return {
    rowKey,
    title: rowKey,
    changes: [],
    stance,
    agentHoldReason: null,
    decidedBy: null,
    applyOutcome: null,
  };
}

function stage(
  rows: LedgerStagedRow[],
  overrides: Partial<LedgerStagedWrite> = {},
): LedgerStagedWrite {
  return {
    stageId: "stage_1",
    surfaceId: "surf_1",
    draftId: "",
    target: { connector: "linear", op: "update_issue" },
    latestRev: 1,
    approvedRev: null,
    status: "staged",
    revisions: [],
    decisions: [],
    createdSeq: 2,
    lastSeq: 3,
    ledgerId: "rrun1·002",
    latestRevision: null,
    applyResult: null,
    applyFailureCode: null,
    rows,
    rowCounts: {
      total: rows.length,
      willApply: rows.filter((r) => r.stance === "will_apply").length,
      held: rows.filter((r) => r.stance === "held").length,
      applied: 0,
      failed: 0,
    },
    ...overrides,
  };
}

describe("TcBulkApplyBar", () => {
  it('labels "Apply {N} changes →" tracking the will-apply count', () => {
    render(
      <TcBulkApplyBar
        stage={stage([
          row("a", "will_apply"),
          row("b", "will_apply"),
          row("c", "held"),
        ])}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-bulk-apply").textContent).toBe(
      bulkApplyLabel(2),
    );
  });

  it("renders the exact contract-grade pledge microcopy", () => {
    render(
      <TcBulkApplyBar
        stage={stage([row("a", "will_apply")])}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByTestId("tc-bulk-pledge").textContent).toBe(
      bulkApplyPledge,
    );
    expect(bulkApplyPledge).toBe(
      "Writes apply only to rows you approve. Held rows stay untouched.",
    );
  });

  it("disables the button when no rows will apply", () => {
    render(
      <TcBulkApplyBar stage={stage([row("a", "held")])} onApply={vi.fn()} />,
    );
    expect(
      (screen.getByTestId("tc-bulk-apply") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("apply callback sends {rev, row_keys} = the displayed will-apply set", () => {
    const onApply = vi.fn();
    render(
      <TcBulkApplyBar
        stage={stage([
          row("a", "will_apply"),
          row("b", "held"),
          row("c", "will_apply"),
        ])}
        onApply={onApply}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-bulk-apply"));
    expect(onApply).toHaveBeenCalledWith("stage_1", 1, ["a", "c"]);
  });

  it("shows a busy state while apply is pending", () => {
    render(
      <TcBulkApplyBar
        stage={stage([row("a", "will_apply")], { status: "apply_pending" })}
        onApply={vi.fn()}
      />,
    );
    const btn = screen.getByTestId("tc-bulk-apply") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toBe("Applying…");
  });
});
