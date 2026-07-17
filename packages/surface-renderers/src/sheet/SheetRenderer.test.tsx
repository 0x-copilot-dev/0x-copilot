import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { clearRegistry, resolveAdapter } from "@0x-copilot/chat-surface";

import {
  SheetRenderer,
  registerSheetAdapter,
  sheetAdapter,
  type SheetCellValue,
  type SheetRegion,
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
        cell(100, { format: "currency" }),
        cell(120, { format: "currency" }),
        cell(140, { format: "currency" }),
        cell(160, {
          format: "currency",
          formula: "=SUM(B2:D2) * RENEWAL_UPLIFT",
        }),
      ],
      [
        cell("Globex"),
        cell(80, { format: "currency" }),
        cell(85, { format: "currency" }),
        cell(95, { format: "currency" }),
        cell(110, { format: "currency" }),
      ],
    ],
    rowAnchors: ["A2", "A3"],
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

describe("sheetAdapter (contract conformance)", () => {
  it("declares scheme 'sheet-row'", () => {
    expect(sheetAdapter.scheme).toBe("sheet-row");
  });

  it("matches sheet-row:// URIs and rejects others", () => {
    expect(sheetAdapter.matches("sheet-row://abc/row-1")).toBe(true);
    expect(sheetAdapter.matches("sheet-row://")).toBe(true);
    expect(sheetAdapter.matches("email://draft-1")).toBe(false);
    expect(sheetAdapter.matches("sf-opp://x")).toBe(false);
    expect(sheetAdapter.matches("sheet-row")).toBe(false);
  });

  it("exposes first-party metadata at schemaVersion 1", () => {
    expect(sheetAdapter.metadata.origin).toBe("first-party");
    expect(sheetAdapter.metadata.schemaVersion).toBe(1);
  });

  it("renderCurrent and renderDiff are functions of state", () => {
    expect(typeof sheetAdapter.renderCurrent).toBe("function");
    expect(typeof sheetAdapter.renderDiff).toBe("function");
  });
});

describe("registerSheetAdapter", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("registers the adapter against the SurfaceRegistry", () => {
    registerSheetAdapter();
    const resolved = resolveAdapter("sheet-row://sheet-1/region-1");
    expect(resolved).toBe(sheetAdapter);
  });

  it("is idempotent (same {scheme, version} hot-swaps in place)", () => {
    registerSheetAdapter();
    registerSheetAdapter();
    const resolved = resolveAdapter("sheet-row://sheet-1/region-1");
    expect(resolved).toBe(sheetAdapter);
  });
});

describe("SheetRenderer (renderCurrent)", () => {
  it("renders the table with all headers and rows", () => {
    const region = makeRegion();
    render(SheetRenderer(region));
    expect(screen.getByTestId("sheet-renderer")).toBeInTheDocument();
    for (let i = 0; i < region.headers.length; i += 1) {
      expect(screen.getByTestId(`sheet-header-${i}`)).toHaveTextContent(
        region.headers[i],
      );
    }
    expect(screen.getByTestId("sheet-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-row-1")).toBeInTheDocument();
  });

  it("renders cell values including numeric values as text", () => {
    const region = makeRegion();
    render(SheetRenderer(region));
    expect(screen.getByTestId("sheet-cell-0-0")).toHaveTextContent("Acme Co");
    expect(screen.getByTestId("sheet-cell-0-1")).toHaveTextContent("100");
    expect(screen.getByTestId("sheet-cell-1-4")).toHaveTextContent("110");
  });

  it("renders the read-only formula bar when cell.formula is present", () => {
    const region = makeRegion();
    render(SheetRenderer(region));
    const formula = screen.getByTestId("sheet-formula-0-4");
    expect(formula).toBeInTheDocument();
    expect(formula).toHaveTextContent("A2 =SUM(B2:D2) * RENEWAL_UPLIFT");
    expect(formula).toHaveAttribute("aria-readonly", "true");
  });

  it("does not render a formula bar for cells without a formula", () => {
    const region = makeRegion();
    render(SheetRenderer(region));
    expect(screen.queryByTestId("sheet-formula-0-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sheet-formula-1-1")).not.toBeInTheDocument();
  });

  it("falls back to the bare formula when no row anchor is supplied", () => {
    const region = makeRegion({
      rowAnchors: undefined,
      rows: [
        [
          cell("Acme Co"),
          cell(100),
          cell(120),
          cell(140),
          cell(160, { formula: "=SUM(B2:D2)" }),
        ],
      ],
    });
    render(SheetRenderer(region));
    expect(screen.getByTestId("sheet-formula-0-4")).toHaveTextContent(
      "=SUM(B2:D2)",
    );
  });

  it("renders an empty-region placeholder when headers is empty", () => {
    const region: SheetRegion = {
      sheetId: "sheet-empty",
      regionId: "region-empty",
      headers: [],
      rows: [],
    };
    render(SheetRenderer(region));
    const empty = screen.getByTestId("sheet-renderer");
    expect(empty).toHaveAttribute("data-empty", "true");
    expect(empty).toHaveAttribute("aria-label", "Empty sheet region");
  });

  it("renders null cell values as empty text without crashing", () => {
    const region: SheetRegion = {
      sheetId: "sheet-1",
      regionId: "region-1",
      headers: ["A", "B"],
      rows: [[cell(null), cell("x")]],
    };
    render(SheetRenderer(region));
    expect(screen.getByTestId("sheet-cell-0-0").textContent).toBe("");
    expect(screen.getByTestId("sheet-cell-0-1")).toHaveTextContent("x");
  });
});

describe("SheetRenderer (column virtualization)", () => {
  it("renders all columns when the region has fewer than 50 columns", () => {
    const region = makeWideRegion(20);
    render(SheetRenderer(region));
    const container = screen.getByTestId("sheet-renderer");
    expect(container).toHaveAttribute("data-virtualized", "false");
    expect(container).toHaveAttribute("data-visible-columns", "20");
    expect(container).toHaveAttribute("data-total-columns", "20");
    expect(screen.getByTestId("sheet-header-19")).toBeInTheDocument();
  });

  it("virtualizes wide sheets to [0, 50) when no viewport is supplied", () => {
    const region = makeWideRegion(120);
    render(SheetRenderer(region));
    const container = screen.getByTestId("sheet-renderer");
    expect(container).toHaveAttribute("data-virtualized", "true");
    expect(container).toHaveAttribute("data-visible-columns", "50");
    expect(container).toHaveAttribute("data-total-columns", "120");
    expect(screen.getByTestId("sheet-header-0")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-header-49")).toBeInTheDocument();
    expect(screen.queryByTestId("sheet-header-50")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sheet-header-119")).not.toBeInTheDocument();
  });

  it("renders the supplied viewport window for wide sheets", () => {
    const region: SheetRegion = {
      ...makeWideRegion(120),
      viewport: { startColumn: 10, endColumn: 40 },
    };
    render(SheetRenderer(region));
    const container = screen.getByTestId("sheet-renderer");
    expect(container).toHaveAttribute("data-virtualized", "true");
    expect(container).toHaveAttribute("data-visible-columns", "30");
    expect(screen.queryByTestId("sheet-header-9")).not.toBeInTheDocument();
    expect(screen.getByTestId("sheet-header-10")).toBeInTheDocument();
    expect(screen.getByTestId("sheet-header-39")).toBeInTheDocument();
    expect(screen.queryByTestId("sheet-header-40")).not.toBeInTheDocument();
  });

  it("clamps a viewport that extends past the total column count", () => {
    const region: SheetRegion = {
      ...makeWideRegion(60),
      viewport: { startColumn: 50, endColumn: 999 },
    };
    render(SheetRenderer(region));
    const container = screen.getByTestId("sheet-renderer");
    expect(container).toHaveAttribute("data-visible-columns", "10");
  });

  it("keeps every row body length matched to the visible header count", () => {
    const region: SheetRegion = {
      ...makeWideRegion(200),
      viewport: { startColumn: 0, endColumn: 30 },
    };
    const { container } = render(SheetRenderer(region));
    const headerCells = container.querySelectorAll("thead th");
    expect(headerCells.length).toBe(30);
    const firstRowCells = container.querySelectorAll(
      "[data-testid='sheet-row-0'] td",
    );
    expect(firstRowCells.length).toBe(30);
  });
});
