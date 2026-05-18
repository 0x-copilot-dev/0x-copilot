// Tests for <DatasetPreview /> (P7-B2).

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  DatasetPreview,
  type DatasetColumnSpec,
  type DatasetRow,
} from "./DatasetPreview";

const SCHEMA: ReadonlyArray<DatasetColumnSpec> = [
  { name: "region", type: "string", nullable: false },
  { name: "amount", type: "float", nullable: true },
  { name: "approved", type: "boolean", nullable: false },
];

describe("<DatasetPreview>", () => {
  it("renders column headers from the schema", () => {
    render(
      <DatasetPreview
        schema={SCHEMA}
        state={{ kind: "ready", rows: [], totalRows: 0 }}
      />,
    );
    expect(screen.getByText("region")).toBeTruthy();
    expect(screen.getByText("amount")).toBeTruthy();
    expect(screen.getByText("approved")).toBeTruthy();
  });

  it("renders rows for state=ready", () => {
    const rows: ReadonlyArray<DatasetRow> = [
      { region: "US", amount: 100.5, approved: true },
      { region: "EU", amount: null, approved: false },
    ];
    render(
      <DatasetPreview
        schema={SCHEMA}
        state={{ kind: "ready", rows, totalRows: 200 }}
      />,
    );
    expect(screen.getAllByTestId("library-dataset-preview-row").length).toBe(2);
    expect(screen.getByText("US")).toBeTruthy();
    expect(screen.getByText("100.5")).toBeTruthy();
    expect(screen.getByText("true")).toBeTruthy();
    // null renders as em-dash placeholder.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    // header "Showing N of M".
    expect(screen.getByText(/Showing 2 of 200 rows/)).toBeTruthy();
  });

  it("caps rendered rows at maxRows (default 100)", () => {
    const rows: ReadonlyArray<DatasetRow> = Array.from(
      { length: 150 },
      (_, i) => ({ region: `R${i}`, amount: i, approved: i % 2 === 0 }),
    );
    render(
      <DatasetPreview
        schema={SCHEMA}
        state={{ kind: "ready", rows, totalRows: 150 }}
      />,
    );
    expect(screen.getAllByTestId("library-dataset-preview-row").length).toBe(
      100,
    );
  });

  it("renders skeleton in idle/loading state", () => {
    render(<DatasetPreview schema={SCHEMA} state={{ kind: "loading" }} />);
    expect(
      screen.getByTestId("library-dataset-preview").getAttribute("data-state"),
    ).toBe("loading");
  });

  it("renders error state with retry callback", () => {
    const onRetry = vi.fn();
    render(
      <DatasetPreview
        schema={SCHEMA}
        state={{ kind: "error", message: "preview endpoint 503" }}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("preview endpoint 503")).toBeTruthy();
    fireEvent.click(screen.getByTestId("library-dataset-preview-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders 'no schema declared' when schema is empty", () => {
    render(
      <DatasetPreview
        schema={[]}
        state={{ kind: "ready", rows: [], totalRows: 0 }}
      />,
    );
    expect(
      screen.getByTestId("library-dataset-preview").getAttribute("data-state"),
    ).toBe("no-schema");
    expect(screen.getByText("No schema declared")).toBeTruthy();
  });
});
