import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DatasetArtifactRenderer } from "./DatasetArtifactRenderer";
import type { DatasetRevisionChange } from "./DatasetRevisionDiff";
import type { ArtifactRenderState } from "./model";

// PRD-03 D4 — a revised dataset must show WHICH CELLS moved. The word-level
// text diff is close to unreadable for tabular source, and it was a CSV that
// produced the original report, so cells are the common path and the text diff
// is the fallback for content that is not a grid on both sides.

const BASE = "name,amount\r\nAda,12\r\n";

function datasetArtifact(
  text: string,
  change: DatasetRevisionChange | unknown,
  mediaType = "text/csv",
): ArtifactRenderState {
  return {
    artifactId: "artifact_test",
    kind: "dataset",
    title: "Ledger",
    mediaType,
    filename: "ledger.csv",
    revision: 2,
    digest: "a".repeat(64),
    byteSize: text.length,
    author: "model",
    createdAt: "2026-01-01T00:00:00Z",
    preview: "ready",
    text,
    datasetRevisionChange: change,
  } as unknown as ArtifactRenderState;
}

/** The payload `ArtifactSurface` attaches: base source plus the bounded text pair. */
function change(
  baseText: string,
  textBefore = "",
  textAfter = "",
): DatasetRevisionChange {
  return { baseRevision: 1, baseText, textBefore, textAfter };
}

describe("dataset revision diff (PRD-03 D4)", () => {
  it("shows a changed cell as before → after, leaving the untouched cells of that row plain", () => {
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact("name,amount\r\nAda,15\r\n", change(BASE))}
      />,
    );

    const panel = screen.getByTestId("dataset-revision-diff");
    expect(panel).toHaveAttribute("data-shape", "cells");
    expect(panel).toHaveTextContent("What changed: r1 → r2");
    expect(panel).toHaveTextContent(
      "1 changed cell; 0 added rows; 0 removed rows.",
    );
    // Row 2 of the landed revision — the numbering the cell editor uses, header
    // included — with the old and the new value both on screen.
    const row = screen.getByTestId("dataset-diff-row-changed-2");
    expect(row).toHaveTextContent("changed · row 2");
    expect(row.querySelector("del")).toHaveTextContent("12");
    expect(row.querySelector("ins")).toHaveTextContent("15");
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveAttribute("data-changed", "false");
    expect(cells[0]).toHaveTextContent("Ada");
    expect(cells[1]).toHaveAttribute("data-changed", "true");
    // The grid itself still renders below the change.
    expect(screen.getByTestId("artifact-dataset-renderer")).toBeInTheDocument();
  });

  it("shows an added row as an insertion at its landed row number", () => {
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact(
          "name,amount\r\nAda,12\r\nGrace,20\r\n",
          change(BASE),
        )}
      />,
    );

    const panel = screen.getByTestId("dataset-revision-diff");
    expect(panel).toHaveTextContent(
      "0 changed cells; 1 added row; 0 removed rows.",
    );
    const row = screen.getByTestId("dataset-diff-row-added-3");
    expect(row).toHaveTextContent("added · row 3");
    expect(
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["Grace", "20"]);
    expect(row.querySelectorAll("ins")).toHaveLength(2);
    expect(screen.queryByTestId("diff-text")).toBeNull();
  });

  it("shows a removed row as a deletion at its base row number", () => {
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact(
          "name,amount\r\n",
          change("name,amount\r\nAda,12\r\n"),
        )}
      />,
    );

    expect(screen.getByTestId("dataset-revision-diff")).toHaveTextContent(
      "0 changed cells; 0 added rows; 1 removed row.",
    );
    const row = screen.getByTestId("dataset-diff-row-removed-2");
    expect(row).toHaveTextContent("removed · row 2");
    expect(row.querySelectorAll("del")).toHaveLength(2);
  });

  it("falls back to the text diff when the content does not read as a grid", () => {
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact(
          "# new heading\n",
          change("# old heading\n", "# old heading", "# new heading"),
          "text/markdown",
        )}
      />,
    );

    const panel = screen.getByTestId("dataset-revision-diff");
    expect(panel).toHaveAttribute("data-shape", "text");
    expect(panel).toHaveTextContent("do not read as a grid on both sides");
    const details = screen.getByLabelText("Revision change details");
    expect(
      details.querySelector("[data-testid='diff-delete']"),
    ).toHaveTextContent("old");
    expect(
      details.querySelector("[data-testid='diff-insert']"),
    ).toHaveTextContent("new");
    // The format still cannot be previewed as a table, and says so.
    expect(screen.getByTestId("artifact-dataset-fallback")).toBeInTheDocument();
  });

  it("falls back to the text diff when a revision moved no cell value", () => {
    // Quoting, delimiters and whitespace live in the bytes, not in a cell
    // value, so a cell diff would report an empty change for a real one.
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact(
          "name,note\r\nAda,hello\r\n",
          change('name,note\r\nAda,"hello"\r\n', 'Ada,"hello"', "Ada,hello"),
        )}
      />,
    );

    expect(screen.getByTestId("dataset-revision-diff")).toHaveAttribute(
      "data-shape",
      "text",
    );
    expect(screen.getByTestId("diff-text")).toBeInTheDocument();
  });

  it("ignores a change payload that is not the host-created shape", () => {
    render(
      <DatasetArtifactRenderer
        artifact={datasetArtifact("name,amount\r\nAda,15\r\n", {
          baseRevision: "1",
          baseText: BASE,
        })}
      />,
    );

    expect(screen.queryByTestId("dataset-revision-diff")).toBeNull();
    expect(screen.getByTestId("artifact-dataset-renderer")).toBeInTheDocument();
  });
});
