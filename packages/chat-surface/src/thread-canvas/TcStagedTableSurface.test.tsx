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
    sends: [
      {
        arg: "issue_id",
        origin: "carried",
        column: "issue_id",
        old: "PAR-9",
        new: "PAR-9",
      },
      {
        arg: "priority",
        origin: "edited",
        column: "priority",
        old: 1,
        new: 2,
      },
    ],
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
        sends: [
          {
            arg: "a_very_long_column_name",
            origin: "edited",
            column: "a_very_long_column_name",
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

  // The invariant: the object the user approves is the object that is sent.
  // A row's unedited arguments ride the wire either way — the only question is
  // whether they were on screen when Approve became available.
  it("renders every argument the row will send, not only the edited one", () => {
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(
          stage([
            row("r1", {
              sends: [
                {
                  arg: "message_id",
                  origin: "carried",
                  column: "message_id",
                  old: "m-1",
                  new: "m-1",
                },
                {
                  arg: "to",
                  origin: "carried",
                  column: "to",
                  old: "dana@acme.example",
                  new: "dana@acme.example",
                },
                {
                  arg: "body",
                  origin: "proposed",
                  column: null,
                  old: null,
                  new: "Confirming the renewal terms.",
                },
                {
                  arg: "cc",
                  origin: "edited",
                  column: "cc",
                  old: "",
                  new: "legal@acme.example",
                },
              ],
            }),
          ]),
        )}
        onRowDecision={noop}
        onApply={noop}
      />,
    );

    const sends = screen.getAllByTestId("tc-table-row-send");
    expect(sends.map((node) => node.getAttribute("data-arg"))).toEqual([
      "message_id",
      "to",
      "body",
      "cc",
    ]);
    // The recipient and the model-authored body are the two values the old
    // `cc: "" → legal@acme.example` diff hid entirely.
    const outbound = screen.getByTestId("tc-table-row-change");
    expect(outbound).toHaveTextContent("dana@acme.example");
    expect(outbound).toHaveTextContent("Confirming the renewal terms.");
    expect(outbound).toHaveTextContent("legal@acme.example");
  });

  it("names who authored each outbound value", () => {
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(stage())}
        onRowDecision={noop}
        onApply={noop}
      />,
    );

    const notes = screen
      .getAllByTestId("tc-table-row-send-origin")
      .map((node) => node.textContent);
    expect(notes).toEqual(["sending unchanged", "you edited"]);
    const carried = screen
      .getAllByTestId("tc-table-row-send")
      .find((node) => node.getAttribute("data-arg") === "issue_id");
    expect(carried).toHaveAttribute("data-origin", "carried");
  });

  // The client's own fail-closed arm: the server refuses to stage a row whose
  // outbound arguments are undisclosed, and a replayed old run must not be
  // approvable through this surface either.
  it("makes a row with no disclosed outbound arguments undecidable", () => {
    const model = projectRowsetReviewModel(
      stage([row("accounted"), row("dark", { sends: [] })]),
    );
    render(
      <TcStagedTableSurface
        model={model}
        onRowDecision={noop}
        onApply={noop}
      />,
    );

    const dark = screen
      .getAllByTestId("tc-table-row")
      .find((node) => node.getAttribute("data-row-key") === "dark")!;
    expect(
      within(dark).getByTestId("tc-table-row-unaccounted"),
    ).toHaveTextContent(
      "Cannot be reviewed — this row's outbound fields were not disclosed.",
    );
    expect(within(dark).getByTestId("tc-table-row-approve")).toBeDisabled();
    expect(within(dark).getByTestId("tc-table-row-hold")).toBeDisabled();
    // …and it is outside the apply scope, so Approve-all cannot carry it.
    expect(model.action?.rowKeys).toEqual(["accounted"]);
    expect(screen.getByTestId("tc-bulk-apply")).toHaveTextContent(
      "Apply 1 changes",
    );
  });

  it("preserves public summary helper behavior", () => {
    expect(countsHeader(6, 2)).toBe("6 will apply · 2 held");
    expect(resultLine(7, 1)).toBe("7 updated · 1 held, untouched");
  });
});
