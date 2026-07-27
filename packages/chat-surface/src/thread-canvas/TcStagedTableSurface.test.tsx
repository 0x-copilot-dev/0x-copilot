import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import {
  TcStagedTableSurface,
  countsHeader,
  resultLine,
} from "./TcStagedTableSurface";
import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";
import { projectRowsetReviewModel } from "./rowsetReviewModel";

function row(
  rowKey = "r1",
  overrides: Partial<LedgerStagedRow> = {},
): LedgerStagedRow {
  return {
    rowKey,
    title: `Acme renewal ${rowKey}`,
    changes: [{ field: "priority", old: 1, new: 2 }],
    stance: "will_apply",
    agentHoldReason: null,
    decidedBy: null,
    applyOutcome: null,
    ...overrides,
  };
}

function stage(
  rows: readonly LedgerStagedRow[] = [row()],
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
      willApply: rows.filter((item) => item.stance === "will_apply").length,
      held: rows.filter((item) => item.stance === "held").length,
      applied: rows.filter((item) => item.applyOutcome === "applied").length,
      failed: rows.filter((item) => item.applyOutcome === "failed").length,
    },
    ...overrides,
  };
}

const noop = () => {};

describe("TcStagedTableSurface", () => {
  it("renders semantic title, old→new diff, and provenance", () => {
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(stage(), {
          title: "Renewal changes",
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-staged-table-connector")).toHaveTextContent(
      "Renewal changes",
    );
    expect(screen.getByTestId("tc-table-row-old")).toHaveTextContent("1");
    expect(screen.getByTestId("tc-table-row-change")).toHaveTextContent(
      "priority",
    );
    expect(screen.getByTestId("tc-table-row-change")).toHaveTextContent("2");
    expect(screen.getByTestId("tc-staged-table-ledger-id")).toHaveTextContent(
      "linear.update_issue · per-row approval · rrun1·002",
    );
  });

  it("sends one row decision using the model identity", () => {
    const onRowDecision = vi.fn();
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(stage())}
        onRowDecision={onRowDecision}
        onApply={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-table-row-hold"));
    expect(onRowDecision).toHaveBeenCalledWith({
      stageId: "stage_1",
      revision: 1,
      proposalDigest: "",
      targetDigest: "",
      rowKey: "r1",
      decision: "hold",
      basisSequence: 3,
    });
  });

  it("keeps the sticky agent hold reason after override", () => {
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(
          stage([
            row("r1", {
              stance: "will_apply",
              agentHoldReason: "call yesterday",
              decidedBy: "user",
            }),
          ]),
        )}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-table-row-reason")).toHaveTextContent(
      "call yesterday — agent pre-held",
    );
  });

  it("keeps header, exact recovery, and provenance outside the row viewport", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("applied", { applyOutcome: "applied" }),
          row("failed", { applyOutcome: "failed" }),
          row("held", { stance: "held" }),
        ],
        { status: "partially_applied", applyResult: "partial" },
      ),
    );
    render(
      <TcStagedTableSurface
        model={model}
        onRowDecision={noop}
        onApply={noop}
      />,
    );

    const surface = screen.getByTestId("tc-staged-table");
    const viewport = screen.getByTestId("tc-review-table-viewport");
    const recovery = screen.getByTestId("tc-bulk-apply-bar");
    const provenance = screen
      .getByTestId("tc-staged-table-ledger-id")
      .closest("footer");

    expect(viewport.parentElement).toBe(surface);
    expect(recovery.parentElement).toBe(surface);
    expect(provenance?.parentElement).toBe(surface);
    expect(viewport.contains(recovery)).toBe(false);
    expect(viewport.contains(provenance)).toBe(false);
    expect(viewport).toHaveAttribute("tabindex", "0");
    expect(viewport).toHaveAttribute("role", "region");
  });

  it("renders 200 wide rows inside one bounded viewport", () => {
    const rows = Array.from({ length: 200 }, (_, index) =>
      row(`row-${index}`, {
        title: `Customer with a deliberately long title ${index} ${"x".repeat(120)}`,
        changes: [
          {
            field: "a_very_long_column_name",
            old: `old-${index}-${"y".repeat(100)}`,
            new: `new-${index}-${"z".repeat(100)}`,
          },
        ],
      }),
    );
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(stage(rows))}
        onRowDecision={noop}
        onApply={noop}
      />,
    );

    const viewport = screen.getByTestId("tc-review-table-viewport");
    expect(within(viewport).getAllByTestId("tc-table-row")).toHaveLength(200);
    expect(screen.getByTestId("tc-bulk-apply")).toHaveTextContent(
      "Apply 200 changes",
    );
  });

  it("submits the same exact failed-key action projected by the model", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("success", { applyOutcome: "applied" }),
          row("failed-a", { applyOutcome: "failed" }),
          row("held", { stance: "held" }),
          row("failed-b", { applyOutcome: "failed" }),
        ],
        { status: "partially_applied", applyResult: "partial" },
      ),
    );
    const onApply = vi.fn();
    render(
      <TcStagedTableSurface
        model={model}
        onRowDecision={noop}
        onApply={onApply}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-bulk-retry"));

    expect(model.action).toMatchObject({
      kind: "retry_failed",
      rowKeys: ["failed-a", "failed-b"],
    });
    expect(onApply).toHaveBeenCalledWith(model.action);
    expect(onApply.mock.calls[0][0]).toBe(model.action);
  });

  it("preserves public summary helper behavior", () => {
    expect(countsHeader(6, 2)).toBe("6 will apply · 2 held");
    expect(resultLine(7, 1)).toBe("7 updated · 1 held, untouched");
  });
});
