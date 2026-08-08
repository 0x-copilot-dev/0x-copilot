import { describe, expect, it } from "vitest";

import type { LedgerStagedRow, LedgerStagedWrite } from "./ledgerProjection";
import type { RowSetEffectReview } from "@0x-copilot/api-types";
import {
  projectCanonicalRowsetReviewModel,
  projectRowsetReviewModel,
  rowsetResultSummary,
} from "./rowsetReviewModel";

function row(
  rowKey: string,
  overrides: Partial<LedgerStagedRow> = {},
): LedgerStagedRow {
  return {
    rowKey,
    title: `Row ${rowKey}`,
    sends: [
      {
        arg: "issue_id",
        origin: "carried",
        column: "issue_id",
        old: rowKey,
        new: rowKey,
      },
      {
        arg: "status",
        origin: "edited",
        column: "status",
        old: "open",
        new: "closed",
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
  rows: readonly LedgerStagedRow[],
  overrides: Partial<LedgerStagedWrite> = {},
): LedgerStagedWrite {
  return {
    stageId: "stage_1",
    surfaceId: "surface_1",
    draftId: "",
    target: { connector: "local-csv", op: "update_rows" },
    latestRev: 3,
    approvedRev: null,
    status: "staged",
    revisions: [],
    decisions: [],
    createdSeq: 2,
    lastSeq: 9,
    ledgerId: "rrun1·009",
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

describe("projectRowsetReviewModel", () => {
  it("projects fresh apply scope once from every pending approved row", () => {
    const model = projectRowsetReviewModel(
      stage([row("a"), row("b", { stance: "held" }), row("c")]),
    );

    expect(model.action).toMatchObject({
      kind: "apply",
      rowKeys: ["a", "c"],
      revision: 3,
      basisSequence: 9,
      disabled: false,
    });
    expect(model.rows.map((item) => item.decision)).toEqual([
      "approved",
      "held",
      "approved",
    ]);
  });

  it("projects every and only failed approved row for partial recovery", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("applied", { applyOutcome: "applied" }),
          row("failed-a", { applyOutcome: "failed" }),
          row("held", { stance: "held" }),
          row("failed-held", {
            stance: "held",
            applyOutcome: "failed",
          }),
          row("failed-b", { applyOutcome: "failed" }),
        ],
        { status: "partially_applied", applyResult: "partial" },
      ),
    );

    expect(model.action).toMatchObject({
      kind: "retry_failed",
      rowKeys: ["failed-a", "failed-b"],
      failedRowKeys: ["failed-a", "failed-b"],
      label: "Retry 2 failed →",
      disabled: false,
    });
    expect(model.action?.rowKeys).not.toContain("applied");
    expect(model.action?.rowKeys).not.toContain("held");
    expect(model.action?.rowKeys).not.toContain("failed-held");
  });

  it("keeps an all-failed first attempt recoverable", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("a", { applyOutcome: "failed" }),
          row("b", { applyOutcome: "failed" }),
        ],
        { status: "staged", applyResult: "failed" },
      ),
    );

    expect(model.action).toMatchObject({
      kind: "retry_failed",
      rowKeys: ["a", "b"],
      label: "Retry 2 failed →",
    });
    expect(model.rows.every((item) => item.canDecide === false)).toBe(true);
  });

  it("freezes the exact action while the request or ledger is pending", () => {
    const localPending = projectRowsetReviewModel(stage([row("a")]), {
      actionPending: true,
    });
    expect(localPending.action).toMatchObject({
      rowKeys: ["a"],
      label: "Applying…",
      pending: true,
      disabled: true,
    });

    const ledgerPending = projectRowsetReviewModel(
      stage([row("a")], { status: "apply_pending" }),
    );
    expect(ledgerPending.action).toMatchObject({
      rowKeys: ["a"],
      label: "Applying…",
      pending: true,
      disabled: true,
    });
  });

  it("has no command in terminal or corrupt states", () => {
    expect(
      projectRowsetReviewModel(
        stage([row("a", { applyOutcome: "applied" })], {
          status: "applied",
          applyResult: "applied",
        }),
      ).action,
    ).toBeNull();
    expect(
      projectRowsetReviewModel(stage([row("a")], { status: "corrupt" })).action,
    ).toBeNull();
  });

  it("projects one diff per outbound argument, keyed by the connector's name", () => {
    const model = projectRowsetReviewModel(
      stage([
        row("a", {
          sends: [
            {
              arg: "issue_id",
              origin: "carried",
              column: "id",
              old: "PAR-9",
              new: "PAR-9",
            },
            {
              arg: "priority",
              origin: "edited",
              column: "priority",
              old: "high",
              new: "low",
            },
          ],
        }),
      ]),
    );

    // `field` is the CONNECTOR arg (`issue_id`), not the surface column (`id`)
    // — a rename between the two is the thing this is meant to expose.
    expect(model.rows[0].diffs).toEqual([
      {
        field: "issue_id",
        before: "PAR-9",
        after: "PAR-9",
        origin: "carried",
        column: "id",
      },
      {
        field: "priority",
        before: "high",
        after: "low",
        origin: "edited",
        column: "priority",
      },
    ]);
    expect(model.rows[0].unaccounted).toBe(false);
  });

  it("holds an unaccounted row out of every decision and action scope", () => {
    const model = projectRowsetReviewModel(
      stage([row("a"), row("dark", { sends: [] }), row("c")]),
    );

    const dark = model.rows[1];
    expect(dark.unaccounted).toBe(true);
    expect(dark.canDecide).toBe(false);
    expect(dark.diffs).toEqual([]);
    expect(model.action).toMatchObject({ kind: "apply", rowKeys: ["a", "c"] });
  });

  it("holds an unaccounted row out of a retry scope too", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("failed-a", { applyOutcome: "failed" }),
          row("dark", { sends: [], applyOutcome: "failed" }),
        ],
        { status: "partially_applied", applyResult: "partial" },
      ),
    );

    expect(model.action).toMatchObject({
      kind: "retry_failed",
      rowKeys: ["failed-a"],
      failedRowKeys: ["failed-a"],
    });
  });

  it("keeps summary and provenance connector-neutral", () => {
    const model = projectRowsetReviewModel(
      stage(
        [
          row("a", { applyOutcome: "applied" }),
          row("b", { applyOutcome: "failed" }),
          row("c", { stance: "held" }),
        ],
        { status: "partially_applied", applyResult: "partial" },
      ),
      { title: "Customer renewal changes" },
    );

    expect(model.title).toBe("Customer renewal changes");
    expect(model.summary).toBe(
      rowsetResultSummary({ applied: 1, failed: 1, held: 1 }),
    );
    expect(model.provenance).toEqual({
      kind: "Table",
      source: "local-csv",
      operation: "update_rows",
      policy: "per-row approval",
      ledgerId: "rrun1·009",
    });
  });
});

describe("projectCanonicalRowsetReviewModel", () => {
  function review(
    overrides: Partial<RowSetEffectReview> = {},
  ): RowSetEffectReview {
    return {
      stage_id: "stg_1",
      revision: 4,
      proposal_digest: "a".repeat(64),
      target_digest: "b".repeat(64),
      title: "Customer renewal changes",
      source_connector: "linear",
      source_op: "update_issue",
      status: "partial",
      rows: [
        {
          row_key: "applied",
          title: "Applied row",
          changes: [{ field: "status", old: "open", new: "closed" }],
          sends: [
            {
              arg: "issue_id",
              origin: "carried",
              column: "issue_id",
              old: "PAR-1",
              new: "PAR-1",
            },
            {
              arg: "status",
              origin: "edited",
              column: "status",
              old: "open",
              new: "closed",
            },
          ],
          decision: "approve",
          decision_source: "user",
          hold_reason: null,
          apply_outcome: "applied",
          can_decide: false,
        },
        {
          row_key: "failed",
          title: "Failed row",
          changes: [{ field: "status", old: "open", new: "closed" }],
          sends: [
            {
              arg: "issue_id",
              origin: "carried",
              column: "issue_id",
              old: "PAR-1",
              new: "PAR-1",
            },
            {
              arg: "status",
              origin: "edited",
              column: "status",
              old: "open",
              new: "closed",
            },
          ],
          decision: "approve",
          decision_source: "default",
          hold_reason: null,
          apply_outcome: "failed",
          can_decide: false,
        },
        {
          row_key: "held",
          title: "Held row",
          changes: [{ field: "status", old: "open", new: "closed" }],
          sends: [
            {
              arg: "issue_id",
              origin: "carried",
              column: "issue_id",
              old: "PAR-1",
              new: "PAR-1",
            },
            {
              arg: "status",
              origin: "edited",
              column: "status",
              old: "open",
              new: "closed",
            },
          ],
          decision: "hold",
          decision_source: "agent",
          hold_reason: "Recent customer reply",
          apply_outcome: null,
          can_decide: false,
        },
      ],
      counts: {
        total: 3,
        approved: 2,
        held: 1,
        applied: 1,
        failed: 1,
      },
      action: {
        kind: "retry_failed",
        row_keys: ["failed"],
        basis_sequence_no: 14,
        basis_ledger_id: "rrun·014",
      },
      ledger_id: "rrun·014",
      last_sequence_no: 14,
      ...overrides,
    };
  }

  it("copies the exact server-authoritative failed scope and immutable basis", () => {
    const model = projectCanonicalRowsetReviewModel(review());

    expect(model.action).toEqual({
      kind: "retry_failed",
      stageId: "stg_1",
      revision: 4,
      proposalDigest: "a".repeat(64),
      targetDigest: "b".repeat(64),
      rowKeys: ["failed"],
      failedRowKeys: ["failed"],
      basisSequence: 14,
      basisLedgerId: "rrun·014",
      label: "Retry 1 failed →",
      message: "Some writes failed. Applied rows are safe — nothing lost.",
      accessibleLabel: "Retry exactly 1 failed rows",
      pending: false,
      disabled: false,
    });
    expect(model.action?.rowKeys).not.toContain("applied");
    expect(model.action?.rowKeys).not.toContain("held");
  });

  it("freezes the same server scope while an action request is pending", () => {
    const model = projectCanonicalRowsetReviewModel(review(), {
      actionPending: true,
    });

    expect(model.action).toMatchObject({
      rowKeys: ["failed"],
      label: "Retrying…",
      pending: true,
      disabled: true,
    });
  });

  it("projects the wire's outbound account, arg-keyed and complete", () => {
    const model = projectCanonicalRowsetReviewModel(review());

    expect(model.rows[0].diffs).toEqual([
      {
        field: "issue_id",
        before: "PAR-1",
        after: "PAR-1",
        origin: "carried",
        column: "issue_id",
      },
      {
        field: "status",
        before: "open",
        after: "closed",
        origin: "edited",
        column: "status",
      },
    ]);
    expect(model.rows.every((item) => !item.unaccounted)).toBe(true);
  });

  // All-or-nothing: a dropped entry is an argument that is still sent and no
  // longer on screen, which is worse than refusing the row outright.
  it("makes a row undecidable when one outbound argument is unreadable", () => {
    const base = review();
    const model = projectCanonicalRowsetReviewModel(
      review({
        rows: [
          {
            ...base.rows[0],
            can_decide: true,
            sends: [
              base.rows[0].sends[0],
              { ...base.rows[0].sends[1], origin: "invented" as never },
            ],
          },
          ...base.rows.slice(1),
        ],
      }),
    );

    expect(model.rows[0].unaccounted).toBe(true);
    expect(model.rows[0].canDecide).toBe(false);
    expect(model.rows[0].diffs).toEqual([]);
  });
});
