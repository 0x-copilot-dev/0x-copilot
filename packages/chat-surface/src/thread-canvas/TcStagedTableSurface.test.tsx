import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  TcStagedTableSurface,
  countsHeader,
  resultLine,
} from "./TcStagedTableSurface";
import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";

function row(overrides: Partial<LedgerStagedRow> = {}): LedgerStagedRow {
  return {
    rowKey: "r1",
    title: "Acme renewal",
    changes: [{ field: "priority", old: 1, new: 2 }],
    stance: "will_apply",
    agentHoldReason: null,
    decidedBy: null,
    applyOutcome: null,
    ...overrides,
  };
}

function stage(overrides: Partial<LedgerStagedWrite> = {}): LedgerStagedWrite {
  const rows = overrides.rows ?? [row()];
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
      applied: rows.filter((r) => r.applyOutcome === "applied").length,
      failed: rows.filter((r) => r.applyOutcome === "failed").length,
    },
    ...overrides,
  };
}

const noop = () => {};

describe("TcStagedTableSurface", () => {
  it("renders per-row title + old→new field diff", () => {
    render(
      <TcStagedTableSurface
        stage={stage()}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-table-row-title").textContent).toBe(
      "Acme renewal",
    );
    const change = screen.getByTestId("tc-table-row-change").textContent ?? "";
    expect(change).toContain("priority");
    expect(change).toContain("1");
    expect(change).toContain("2");
  });

  it("row Hold toggle sends the row_key", () => {
    const onRowDecision = vi.fn();
    render(
      <TcStagedTableSurface
        stage={stage()}
        onRowDecision={onRowDecision}
        onApply={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-table-row-hold"));
    expect(onRowDecision).toHaveBeenCalledWith("stage_1", "hold", "r1");
  });

  it("row Approve toggle appears for a held row and sends the row_key", () => {
    const onRowDecision = vi.fn();
    render(
      <TcStagedTableSurface
        stage={stage({
          rows: [row({ stance: "held", agentHoldReason: "recent reply" })],
        })}
        onRowDecision={onRowDecision}
        onApply={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("tc-table-row-approve"));
    expect(onRowDecision).toHaveBeenCalledWith("stage_1", "approve", "r1");
  });

  it("shows the agent pre-hold chip `{reason} — agent pre-held` before AND after override", () => {
    // Held (before override).
    const { rerender } = render(
      <TcStagedTableSurface
        stage={stage({
          rows: [
            row({
              stance: "held",
              agentHoldReason: "call yesterday",
              decidedBy: "agent",
            }),
          ],
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-table-row-reason").textContent).toBe(
      "call yesterday — agent pre-held",
    );
    // After override: stance flips to will_apply but the reason STAYS (FR-C7).
    rerender(
      <TcStagedTableSurface
        stage={stage({
          rows: [
            row({
              stance: "will_apply",
              agentHoldReason: "call yesterday",
              decidedBy: "user",
            }),
          ],
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-table-row-reason").textContent).toBe(
      "call yesterday — agent pre-held",
    );
  });

  it("counts header tracks will-apply and held", () => {
    render(
      <TcStagedTableSurface
        stage={stage({
          rows: [row({ rowKey: "a" }), row({ rowKey: "b", stance: "held" })],
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-staged-table-counts").textContent).toBe(
      countsHeader(1, 1),
    );
  });

  it("renders the FR-C9 result line when applied (N updated · M held, untouched)", () => {
    render(
      <TcStagedTableSurface
        stage={stage({
          status: "applied",
          rows: [
            row({ rowKey: "a", applyOutcome: "applied" }),
            row({ rowKey: "b", stance: "held" }),
          ],
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    expect(screen.getByTestId("tc-staged-table-counts").textContent).toBe(
      resultLine(1, 1),
    );
    expect(resultLine(1, 1)).toBe("1 updated · 1 held, untouched");
    // The apply bar drops at a terminal state.
    expect(screen.queryByTestId("tc-bulk-apply-bar")).toBeNull();
  });

  it("shows a partial state with per-row outcomes", () => {
    render(
      <TcStagedTableSurface
        stage={stage({
          status: "partially_applied",
          rows: [
            row({ rowKey: "a", applyOutcome: "applied" }),
            row({ rowKey: "b", applyOutcome: "failed" }),
          ],
        })}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
    const outcomes = screen.getAllByTestId("tc-table-row-outcome");
    expect(outcomes.map((o) => o.textContent)).toEqual(["updated", "failed"]);
  });
});
