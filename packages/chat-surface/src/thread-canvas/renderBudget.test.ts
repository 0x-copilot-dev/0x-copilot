import { describe, expect, it } from "vitest";

import {
  applyRenderBudget,
  renderedWeight,
  DEFAULT_RENDER_BUDGET,
  DEFAULT_RENDER_BUDGET_SLACK,
  type BudgetedEntry,
} from "./renderBudget";

interface Row {
  readonly id: number;
  /** `step` is process (elidable); `note` is product (never withheld). */
  readonly kind: "step" | "note";
  /** Rows this one entry mounts; the reasoning-with-absorbed-cards case. */
  readonly rows?: number;
}

const step = (id: number, rows?: number): Row =>
  rows === undefined ? { id, kind: "step" } : { id, kind: "step", rows };
const note = (id: number): Row => ({ id, kind: "note" });

const steps = (n: number, from = 0): Row[] =>
  Array.from({ length: n }, (_, i) => step(from + i));

const OPTIONS = {
  budget: 10,
  slack: 4,
  isElidable: (row: Row) => row.kind === "step",
  weightOf: (row: Row) => row.rows ?? 1,
};

const fold = (rows: readonly Row[], over: Partial<typeof OPTIONS> = {}) =>
  applyRenderBudget(rows, { ...OPTIONS, ...over });

const rendered = (entries: readonly BudgetedEntry<Row>[]): Row[] =>
  entries.flatMap((e) => (e.kind === "rendered" ? [e.item] : []));

const withheld = (entries: readonly BudgetedEntry<Row>[]): Row[] =>
  entries.flatMap((e) => (e.kind === "elided" ? [...e.items] : []));

describe("applyRenderBudget — the identity case", () => {
  it("renders every item when the transcript fits the budget", () => {
    const rows = steps(10);
    const entries = fold(rows);
    expect(entries.every((e) => e.kind === "rendered")).toBe(true);
    expect(rendered(entries)).toEqual(rows);
  });

  it("renders every item inside the hysteresis band", () => {
    // budget 10 + slack 4: nothing is withheld until the 14th row, because
    // withholding one row per arrival is the churn this band exists to stop.
    for (let n = 11; n < 14; n += 1) {
      const entries = fold(steps(n));
      expect(withheld(entries)).toHaveLength(0);
    }
  });

  it("renders everything at an infinite budget", () => {
    // How the expanded state is expressed at the call site — one code path,
    // not a second branch that could drift from this one.
    const rows = steps(500);
    const entries = fold(rows, { budget: Number.POSITIVE_INFINITY });
    expect(rendered(entries)).toEqual(rows);
  });

  it("returns nothing for an empty transcript", () => {
    expect(fold([])).toEqual([]);
  });
});

describe("applyRenderBudget — the bound", () => {
  it("withholds the head once the band is crossed", () => {
    const entries = fold(steps(14));
    expect(withheld(entries).map((r) => r.id)).toEqual([0, 1, 2, 3]);
    expect(rendered(entries)).toHaveLength(10);
  });

  it("keeps the mounted tail inside budget + slack however long the run gets", () => {
    for (const n of [14, 40, 137, 1000]) {
      const entries = fold(steps(n));
      expect(renderedWeight(entries, OPTIONS.weightOf)).toBeLessThanOrEqual(
        OPTIONS.budget + OPTIONS.slack,
      );
    }
  });

  it("accounts for every item exactly once", () => {
    // The property that makes the summary line honest: what is not mounted is
    // counted, and nothing falls between the two.
    const rows = [...steps(30), note(99), ...steps(30, 100)];
    const entries = fold(rows);
    expect([...withheld(entries), ...rendered(entries)]).toHaveLength(
      rows.length,
    );
    const summed = entries.reduce(
      (n, e) => n + (e.kind === "elided" ? e.weight : 0),
      0,
    );
    expect(summed).toBe(withheld(entries).length);
  });
});

describe("applyRenderBudget — the boundary does not move between snaps", () => {
  it("withholds the same head as the transcript grows inside a band", () => {
    // The whole reason `keepWeight` is not simply `budget`. A boundary at
    // `total - budget` advances on every streamed token, tearing one row out
    // from above the reader per frame — more layout work than mounting the lot.
    const at14 = withheld(fold(steps(14))).map((r) => r.id);
    for (let n = 15; n < 18; n += 1) {
      expect(withheld(fold(steps(n))).map((r) => r.id)).toEqual(at14);
    }
    // …and then snaps forward by exactly one slack, once.
    expect(withheld(fold(steps(18))).map((r) => r.id)).toEqual([
      ...at14,
      4,
      5,
      6,
      7,
    ]);
  });
});

describe("applyRenderBudget — what it refuses to withhold", () => {
  it("never withholds a non-elidable item, however deep in the head it sits", () => {
    const rows = [...steps(20), note(999), ...steps(20, 100)];
    const entries = fold(rows);
    expect(withheld(entries).some((r) => r.kind === "note")).toBe(false);
    expect(rendered(entries)).toContainEqual(note(999));
  });

  it("withholds nothing at all when nothing is elidable", () => {
    // A conversation that is pure prose is never truncated, however long. The
    // bound this fold buys is O(non-elidable) + budget, deliberately not O(1).
    const rows = Array.from({ length: 200 }, (_, i) => note(i));
    expect(withheld(fold(rows))).toHaveLength(0);
  });

  it("breaks the withheld run in place rather than hoisting the survivor", () => {
    // Reading order is the invariant: the item that survives is drawn where it
    // was, with the work around it summarised — not lifted to the top.
    const rows = [...steps(30), note(50), ...steps(30, 100)];
    const entries = fold(rows);
    const shape = entries.map((e) =>
      e.kind === "elided" ? `elided(${e.weight})` : `row(${e.item.id})`,
    );
    const noteAt = shape.indexOf("row(50)");
    expect(noteAt).toBeGreaterThan(0);
    expect(shape[noteAt - 1]).toMatch(/^elided\(/);
  });

  it("renders the newest item even when it alone outweighs the allowance", () => {
    // A reasoning span with 100 cards absorbed into it is ONE entry worth 101
    // rows. Without the clamp, the newest thing in the transcript would be the
    // first thing withheld.
    const rows = [...steps(20), step(999, 100)];
    const entries = fold(rows);
    expect(rendered(entries)).toContainEqual(step(999, 100));
  });
});

describe("applyRenderBudget — weight is rows, not entries", () => {
  it("counts an entry that mounts many rows as many", () => {
    // Six entries, but 30 rows: a budget counting entries would score this as
    // comfortably inside 10 and withhold nothing.
    const rows = Array.from({ length: 6 }, (_, i) => step(i, 5));
    const entries = fold(rows);
    expect(withheld(entries).length).toBeGreaterThan(0);
    expect(renderedWeight(entries, OPTIONS.weightOf)).toBeLessThanOrEqual(
      OPTIONS.budget + OPTIONS.slack,
    );
  });

  it("defaults an unweighted item to one row", () => {
    const entries = applyRenderBudget(steps(20), {
      budget: OPTIONS.budget,
      slack: OPTIONS.slack,
      isElidable: OPTIONS.isElidable,
    });
    expect(renderedWeight(entries)).toBeLessThanOrEqual(
      OPTIONS.budget + OPTIONS.slack,
    );
  });
});

describe("applyRenderBudget — the shipped defaults", () => {
  it("bounds a 300-step run to a small multiple of a screen", () => {
    const entries = applyRenderBudget(steps(300), {
      budget: DEFAULT_RENDER_BUDGET,
      isElidable: () => true,
    });
    expect(renderedWeight(entries)).toBeLessThanOrEqual(
      DEFAULT_RENDER_BUDGET + DEFAULT_RENDER_BUDGET_SLACK,
    );
    expect(renderedWeight(entries)).toBeGreaterThanOrEqual(
      DEFAULT_RENDER_BUDGET,
    );
  });
});
