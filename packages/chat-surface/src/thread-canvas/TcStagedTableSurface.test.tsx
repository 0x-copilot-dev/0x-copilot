import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";
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

// ── THE REVIEW NEVER CLIPS WHAT IT IS DISCLOSING ─────────────────────────────
//
// The tests above assert that every outbound argument reaches the DOM. That is
// exactly the thing the CSS then undid, and the suite could not tell: jsdom
// runs no layout, so `toHaveTextContent(...)` is green over a value the browser
// paints two characters of.
//
// Measured against the real stylesheet in Chromium, on the worked-exploit row,
// before this fix: the Sending cell was 83px wide at 760px viewport and 155px
// at 1200px, 6% and 16% of its own values were on screen, and the origin note
// was 0px visible at both. Nothing carried an ellipsis, because
// `text-overflow` applies to a block container and never to a flex container's
// items — the cut was silent, so `dana@acme.example, jor` read as a complete
// address list and `sending unchanged` read as the string `SEN`. The clip is
// from the END and recipients are comma-joined, so an injected address landed
// precisely where the renderer cut.
//
// jsdom cannot measure that, so — following `TcWriteGateRow.test.tsx`'s
// "the header never clips its decision" precedent — these read the shipped CSS
// through the real surface's DOM and pin the CONTRACT that decides the outcome:
// everything wraps, nothing hides, nothing is shortened.
describe("TcStagedTableSurface — the Sending cell's disclosure contract", () => {
  const here =
    typeof import.meta.dirname === "string"
      ? import.meta.dirname
      : dirname(fileURLToPath(import.meta.url));

  let sheet: HTMLStyleElement | null = null;

  // A row whose values have no break opportunities of their own: addresses,
  // an id, and an unbroken 380-character url. Nothing here can be relied on to
  // contain a space — every one of these strings comes off the wire.
  const INJECTED =
    "dana@acme.example, jordan@acme.example, mallory@evil.example";
  const UNBREAKABLE = `https://cdn.example/${"a".repeat(380)}.pdf`;

  function renderWithRealCss(): void {
    sheet = document.createElement("style");
    sheet.textContent = readFileSync(
      resolve(here, "review-surfaces.css"),
      "utf-8",
    );
    document.head.appendChild(sheet);
    render(
      <TcStagedTableSurface
        model={projectRowsetReviewModel(
          stage([
            row("r1", {
              sends: [
                {
                  arg: "to",
                  origin: "edited",
                  column: "to",
                  old: "dana@acme.example",
                  new: INJECTED,
                },
                {
                  arg: "message_id",
                  origin: "carried",
                  column: "message_id",
                  old: "m-1041",
                  new: "m-1041",
                },
                {
                  arg: "attachment_url",
                  origin: "proposed",
                  column: "attachment_url",
                  old: "",
                  new: UNBREAKABLE,
                },
              ],
            }),
          ]),
        )}
        onRowDecision={noop}
        onApply={noop}
      />,
    );
  }

  function cellNodes(): readonly Element[] {
    const cell = screen.getByTestId("tc-table-row-change");
    return [cell, ...cell.querySelectorAll("*")];
  }

  afterEach(() => {
    sheet?.remove();
    sheet = null;
  });

  it("hides nothing anywhere in the cell — no element owns a clip", () => {
    renderWithRealCss();
    const nodes = cellNodes();
    // Asserted over EVERY descendant rather than a named list, because the rule
    // is per-container: the next element added to this cell inherits the
    // guarantee instead of having to remember it.
    expect(nodes.length).toBeGreaterThan(6);
    for (const node of nodes) {
      const style = globalThis.getComputedStyle(node);
      // jsdom reports "" for a property no rule declares.
      expect(["", "visible"]).toContain(style.overflow);
      // A silent height bound hides a tail exactly as a width clip does. If one
      // is ever added here it has to announce itself as a bound; until then the
      // surface's one scroll owner (`.tc-review-table__viewport`) carries the
      // height, so there is no second, invisible scroller to be caught in.
      expect(style.maxHeight).toBe("none");
    }
  });

  it("shortens nothing — an ellipsis here would need an affordance it has not got", () => {
    renderWithRealCss();
    for (const node of cellNodes()) {
      // `text-overflow: ellipsis` was declared on the entry and painted nothing
      // (wrong box type), which is how a truncated value came to be
      // indistinguishable from a short one. Making it paint would not fix the
      // review either: an elided outbound argument is still an argument the
      // reviewer did not read. Shortening may only return here paired with a
      // real way to read the whole value, and this assertion is where that
      // decision has to be made deliberately.
      expect(globalThis.getComputedStyle(node).textOverflow).not.toBe(
        "ellipsis",
      );
    }
  });

  it("wraps every entry rather than running it off the end", () => {
    renderWithRealCss();
    const entries = screen.getAllByTestId("tc-table-row-send");
    expect(entries.length).toBe(3);
    for (const entry of entries) {
      const style = globalThis.getComputedStyle(entry);
      expect(style.flexWrap).toBe("wrap");
      expect(style.whiteSpace).toBe("normal");
      expect(style.minWidth).toBe("0px");
    }
  });

  it("lets each value wrap inside its own track, unbroken token or not", () => {
    renderWithRealCss();
    const values = screen.getAllByTestId("tc-table-row-change-value");
    expect(values.map((node) => node.textContent)).toEqual([
      INJECTED,
      "m-1041",
      UNBREAKABLE,
    ]);
    for (const value of values) {
      const style = globalThis.getComputedStyle(value);
      expect(style.whiteSpace).toBe("pre-wrap");
      // `anywhere` and not `break-word`: only `anywhere` also lowers the
      // min-content contribution, so the 380-character url wraps inside the
      // column instead of widening the grid track and spilling the row.
      expect(style.overflowWrap).toBe("anywhere");
      expect(style.minWidth).toBe("0px");
    }
  });

  it("keeps the origin note in flow, never pushed out by an auto margin", () => {
    renderWithRealCss();
    const notes = screen.getAllByTestId("tc-table-row-send-origin");
    expect(notes.map((node) => node.textContent)).toEqual([
      "you edited",
      "sending unchanged",
      "agent wrote",
    ]);
    for (const note of notes) {
      const style = globalThis.getComputedStyle(note);
      // `margin-left: auto` resolves to 0 under NEGATIVE free space, so the one
      // annotation distinguishing "you typed this" from "nobody read this" was
      // laid out beyond the clip on exactly the rows that needed it — the
      // proposed values are the long ones.
      expect(style.marginLeft).toBe("0px");
      // Fixed short copy, so it costs the line nothing to keep it whole.
      expect(style.flexShrink).toBe("0");
    }
  });

  it("gives the disclosure the widest column on the row", () => {
    renderWithRealCss();
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-table-row"),
    );
    const weights = [...style.gridTemplateColumns.matchAll(/([\d.]+)fr/g)].map(
      (match) => Number(match[1]),
    );
    // Decide and Status are fixed px; the four flexible tracks are
    // Item · Currently · Sending · Review note, in that order.
    expect(weights.length).toBe(4);
    const sending = weights[2];
    // Sending is the row's complete outbound payload — the thing the approval
    // is about. It used to be the NARROWEST flexible track (0.72fr) while
    // Review note, which carries a fixed short status phrase, took 1.62fr.
    expect(sending).toBe(Math.max(...weights));
    expect(sending).toBeGreaterThan(2 * weights[3]);
  });

  it("wraps the BEFORE side too, so the comparison is not half-read", () => {
    renderWithRealCss();
    const style = globalThis.getComputedStyle(
      screen.getByTestId("tc-table-row-old"),
    );
    // Currently already wrapped, but only at spaces — and its values are
    // addresses, ids and urls, which have none. It spilled its track for the
    // same reason the after side clipped.
    expect(style.overflowWrap).toBe("anywhere");
    expect(style.minWidth).toBe("0px");
  });

  // The desktop CSS-shadowing trap: a host sheet re-declaring a package-owned
  // class name wins the cascade and would silently restore the clip, with every
  // assertion above still green.
  it("owns these rules itself — no host stylesheet re-declares them", () => {
    const root = join(here, "..", "..", "..", "..");
    for (const hostSheet of [
      join(root, "apps", "frontend", "src", "styles.css"),
      join(root, "apps", "desktop", "renderer", "desktop.css"),
    ]) {
      let css = "";
      try {
        css = readFileSync(hostSheet, "utf8");
      } catch {
        continue; // sheet absent in this checkout — nothing to shadow
      }
      expect(
        css.includes("tc-review-table"),
        `${hostSheet} must not own the review table's class names`,
      ).toBe(false);
    }
  });
});
