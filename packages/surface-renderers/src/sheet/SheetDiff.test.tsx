import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { sheetAdapter } from "./SheetRenderer";
import { SheetDiff as SheetDiffView } from "./SheetDiff";
import type {
  SheetCellChange,
  SheetCellValue,
  SheetDiff as SheetDiffData,
  SheetRegion,
} from "./SheetRenderer";

function cell(
  value: string | number | null,
  extras: Partial<Omit<SheetCellValue, "value">> = {},
): SheetCellValue {
  return { value, ...extras };
}

function makeRegion(overrides: Partial<SheetRegion> = {}): SheetRegion {
  return {
    sheetId: "sheet-1",
    regionId: "region-1",
    headers: ["Account", "Q1", "Q2", "Q3", "Q4"],
    rows: [
      [
        cell("Acme Co"),
        cell(100),
        cell(120),
        cell(140),
        cell(160, { formula: "=SUM(B2:D2) * RENEWAL_UPLIFT" }),
      ],
      [cell("Globex"), cell(80), cell(85), cell(95), cell(110)],
    ],
    rowAnchors: ["A2", "A3"],
    ...overrides,
  };
}

function makeDiff(
  changes: readonly SheetCellChange[],
  overrides: Partial<SheetDiffData> = {},
): SheetDiffData {
  return {
    diffId: "diff-1",
    provenance: "DRAFTED FROM SALESFORCE + Q4 SHEET",
    title: "Q4 renewal uplift applied",
    description: "Approve to write changes to the source sheet.",
    region: makeRegion(),
    changes,
    ...overrides,
  };
}

function makeWideRegion(columns: number): SheetRegion {
  const headers = Array.from({ length: columns }, (_, i) => `Col${i}`);
  const rows = [
    Array.from({ length: columns }, (_, i) => cell(i)),
    Array.from({ length: columns }, (_, i) => cell(i * 2)),
  ];
  return {
    sheetId: "wide-sheet",
    regionId: "wide-region",
    headers,
    rows,
  };
}

describe("SheetDiff", () => {
  it("renders the provenance pill via TcInlineDiff without host-owned actions", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
    ]);
    render(<SheetDiffView diff={diff} />);
    const pill = screen.getByTestId("tc-inline-diff-pill");
    expect(pill).toHaveTextContent("STREAMING");
    expect(
      screen.getByText("DRAFTED FROM SALESFORCE + Q4 SHEET"),
    ).toBeInTheDocument();
    expect(screen.getByText("Q4 renewal uplift applied")).toBeInTheDocument();
  });

  it("FR-3.20: shows a `streaming · N%` chip while the snapshot streams, as a diff table (not chat text)", () => {
    const diff = makeDiff(
      [{ row: 0, column: 4, before: cell(160), after: cell(176) }],
      { streamProgress: 42 },
    );
    render(<SheetDiffView diff={diff} />);
    // Progress rides the existing streaming pill: `STREAMING · 42%`.
    expect(screen.getByTestId("tc-inline-diff-pill")).toHaveTextContent(
      /streaming · 42%/i,
    );
    // The tabular surface renders as a structured diff table, not a chat
    // markdown table.
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-diff")).toBeInTheDocument();
  });

  it("rounds the streaming progress percentage on the pill", () => {
    const diff = makeDiff([], { streamProgress: 66.6 });
    render(<SheetDiffView diff={diff} />);
    expect(screen.getByTestId("tc-inline-diff-pill")).toHaveTextContent(
      /streaming · 67%/i,
    );
  });

  it("shows the bare STREAMING pill when no progress is supplied", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
    ]);
    render(<SheetDiffView diff={diff} />);
    const pill = screen.getByTestId("tc-inline-diff-pill");
    expect(pill).toHaveTextContent("STREAMING");
    expect(pill.textContent ?? "").not.toContain("·");
  });

  it("does not render host-owned Approve / Reject / Suggest buttons (D28)", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
    ]);
    render(<SheetDiffView diff={diff} />);
    expect(
      screen.queryByTestId("tc-inline-diff-approve"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-reject"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-suggest"),
    ).not.toBeInTheDocument();
  });

  it("highlights changed cells with data-changed=true and the before/after pair", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
      { row: 1, column: 4, before: cell(110), after: cell(121) },
    ]);
    render(<SheetDiffView diff={diff} />);

    const changedTop = screen.getByTestId("sheet-diff-cell-0-4");
    expect(changedTop).toHaveAttribute("data-changed", "true");
    expect(screen.getByTestId("sheet-diff-before-0-4")).toHaveTextContent(
      "160",
    );
    expect(screen.getByTestId("sheet-diff-after-0-4")).toHaveTextContent("176");
    expect(changedTop).toHaveAttribute("aria-label", "Q4: 160 → 176");

    const changedBottom = screen.getByTestId("sheet-diff-cell-1-4");
    expect(changedBottom).toHaveAttribute("data-changed", "true");
    expect(screen.getByTestId("sheet-diff-before-1-4")).toHaveTextContent(
      "110",
    );
    expect(screen.getByTestId("sheet-diff-after-1-4")).toHaveTextContent("121");
  });

  it("leaves unchanged cells with data-changed=false and no before/after split", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
    ]);
    render(<SheetDiffView diff={diff} />);
    const unchanged = screen.getByTestId("sheet-diff-cell-0-0");
    expect(unchanged).toHaveAttribute("data-changed", "false");
    expect(unchanged).toHaveTextContent("Acme Co");
    expect(
      screen.queryByTestId("sheet-diff-before-0-0"),
    ).not.toBeInTheDocument();
  });

  it("renders an empty placeholder when the region has no columns", () => {
    const diff: SheetDiffData = makeDiff([], {
      region: {
        sheetId: "s",
        regionId: "r",
        headers: [],
        rows: [],
      },
    });
    render(<SheetDiffView diff={diff} />);
    expect(screen.getByTestId("sheet-diff-empty")).toBeInTheDocument();
  });

  it("exposes diff metadata on the wrapper for testability", () => {
    const diff = makeDiff([
      { row: 0, column: 1, before: cell(100), after: cell(110) },
      { row: 0, column: 2, before: cell(120), after: cell(125) },
    ]);
    render(<SheetDiffView diff={diff} />);
    const wrapper = screen.getByTestId("sheet-diff");
    expect(wrapper).toHaveAttribute("data-diff-id", "diff-1");
    expect(wrapper).toHaveAttribute("data-changes", "2");
  });
});

describe("SheetDiff (column virtualization)", () => {
  it("virtualizes the diff table for wide sheets (>= 50 columns)", () => {
    const region = makeWideRegion(120);
    const diff = makeDiff(
      [{ row: 0, column: 3, before: cell(3), after: cell(99) }],
      { region },
    );
    render(<SheetDiffView diff={diff} />);
    const wrapper = screen.getByTestId("sheet-diff");
    expect(wrapper).toHaveAttribute("data-virtualized", "true");
    expect(wrapper).toHaveAttribute("data-visible-columns", "50");
    expect(wrapper).toHaveAttribute("data-total-columns", "120");
    expect(
      screen.queryByTestId("sheet-diff-header-50"),
    ).not.toBeInTheDocument();
  });

  it("hides changed cells outside the viewport window", () => {
    const region: SheetRegion = {
      ...makeWideRegion(120),
      viewport: { startColumn: 0, endColumn: 30 },
    };
    const diff = makeDiff(
      [
        { row: 0, column: 5, before: cell(5), after: cell(55) },
        { row: 0, column: 90, before: cell(90), after: cell(900) },
      ],
      { region },
    );
    render(<SheetDiffView diff={diff} />);
    expect(screen.getByTestId("sheet-diff-cell-0-5")).toHaveAttribute(
      "data-changed",
      "true",
    );
    expect(
      screen.queryByTestId("sheet-diff-cell-0-90"),
    ).not.toBeInTheDocument();
  });

  it("uses the supplied viewport even when narrower than 50 columns is requested", () => {
    const region: SheetRegion = {
      ...makeWideRegion(100),
      viewport: { startColumn: 60, endColumn: 80 },
    };
    const diff = makeDiff([], { region });
    render(<SheetDiffView diff={diff} />);
    const wrapper = screen.getByTestId("sheet-diff");
    expect(wrapper).toHaveAttribute("data-visible-columns", "20");
    expect(screen.getByTestId("sheet-diff-header-60")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-diff-header-79")).toBeInTheDocument();
    expect(
      screen.queryByTestId("sheet-diff-header-80"),
    ).not.toBeInTheDocument();
  });
});

describe("sheetAdapter.renderDiff", () => {
  it("returns a ReactElement equivalent to the SheetDiff component", () => {
    const diff = makeDiff([
      { row: 0, column: 4, before: cell(160), after: cell(176) },
    ]);
    render(sheetAdapter.renderDiff(diff));
    expect(screen.getByTestId("sheet-diff")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-diff-cell-0-4")).toHaveAttribute(
      "data-changed",
      "true",
    );
  });
});
