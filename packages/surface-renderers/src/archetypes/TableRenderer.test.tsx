import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceSpec, SurfaceState } from "../_shared/specTypes";
import { GITHUB_TABLE_STATE } from "./fixtures";
import { ROW_RENDER_CAP, tableAdapter } from "./TableRenderer";

describe("tableAdapter contract", () => {
  it("registers scheme 'table' with first-party metadata", () => {
    expect(tableAdapter.scheme).toBe("table");
    expect(tableAdapter.metadata.origin).toBe("first-party");
    expect(tableAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only table:// uris", () => {
    expect(tableAdapter.matches("table://github/issues")).toBe(true);
    expect(tableAdapter.matches("record://x")).toBe(false);
  });
});

// AC1 — golden render for github_list_issues.
describe("tableAdapter.renderCurrent (golden: github_list_issues)", () => {
  it("renders the repository title and column headers", () => {
    render(tableAdapter.renderCurrent(GITHUB_TABLE_STATE));
    expect(screen.getByTestId("surface-title")).toHaveTextContent("acme/web");
    const grid = screen.getByTestId("table-grid");
    for (const header of ["Number", "Title", "State", "Assignee", "Updated"]) {
      expect(grid).toHaveTextContent(header);
    }
  });

  it("renders a row per item with resolved cell values", () => {
    render(tableAdapter.renderCurrent(GITHUB_TABLE_STATE));
    expect(screen.getByTestId("table-cell-0-0")).toHaveTextContent("128");
    expect(screen.getByTestId("table-cell-0-1")).toHaveTextContent(
      "Composer drops focus on send",
    );
    expect(screen.getByTestId("table-cell-0-2")).toHaveTextContent("open");
    expect(screen.getByTestId("table-cell-0-3")).toHaveTextContent("jdoe");
    expect(screen.getByTestId("table-cell-1-0")).toHaveTextContent("131");
  });

  it("renders exactly the two data rows", () => {
    render(tableAdapter.renderCurrent(GITHUB_TABLE_STATE));
    expect(screen.getByTestId("table-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("table-row-1")).toBeInTheDocument();
    expect(screen.queryByTestId("table-row-2")).not.toBeInTheDocument();
  });
});

// ≥50-column windowing via the shared sheet/_columns helper.
describe("tableAdapter column windowing (>=50 columns)", () => {
  it("windows to the default 50 columns and shows a column cap", () => {
    const columns = Array.from({ length: 60 }, (_, i) => ({
      label: `Col ${i}`,
      path: `c${i}`,
    }));
    const spec: SurfaceSpec = {
      spec_version: 1,
      archetype: "table",
      source: { server: "s", tool: "t" },
      title_path: "name",
      items_path: "rows",
      columns,
    };
    const data = {
      name: "Wide",
      rows: [Object.fromEntries(columns.map((c) => [c.path, c.path]))],
    };
    render(tableAdapter.renderCurrent({ spec, data }));
    expect(screen.getByTestId("table-header-0")).toBeInTheDocument();
    expect(screen.getByTestId("table-header-49")).toBeInTheDocument();
    expect(screen.queryByTestId("table-header-50")).not.toBeInTheDocument();
    expect(screen.getByTestId("table-column-cap")).toHaveTextContent(
      "Showing 50 of 60 columns.",
    );
  });
});

// >200-row render budget cap.
describe("tableAdapter row cap (>200 rows)", () => {
  it("paints at most 200 rows and shows a 'showing 200 of N' cap", () => {
    const spec: SurfaceSpec = {
      spec_version: 1,
      archetype: "table",
      source: { server: "s", tool: "t" },
      title_path: "name",
      items_path: "rows",
      columns: [{ label: "Id", path: "id", format: "number" }],
    };
    const data = {
      name: "Big",
      rows: Array.from({ length: 250 }, (_, i) => ({ id: i })),
    };
    render(tableAdapter.renderCurrent({ spec, data }));
    expect(
      screen.getByTestId(`table-row-${ROW_RENDER_CAP - 1}`),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId(`table-row-${ROW_RENDER_CAP}`),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("table-row-cap")).toHaveTextContent(
      "Showing 200 of 250 rows.",
    );
  });
});

// Spec-less fallback + empties.
describe("tableAdapter defensive rendering", () => {
  it("renders the fallback body when the spec is absent", () => {
    const state: SurfaceState = { data: [{ id: 1 }] };
    expect(() => render(tableAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-preparing-hint")).toBeInTheDocument();
  });

  it("shows an empty message when items_path resolves to nothing", () => {
    const spec: SurfaceSpec = {
      spec_version: 1,
      archetype: "table",
      source: { server: "s", tool: "t" },
      title_path: "name",
      items_path: "missing",
      columns: [{ label: "Id", path: "id" }],
    };
    render(tableAdapter.renderCurrent({ spec, data: { name: "X" } }));
    expect(screen.getByTestId("surface-empty")).toHaveTextContent(
      "No rows to display.",
    );
  });
});
