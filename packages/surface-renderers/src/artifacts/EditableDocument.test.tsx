import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
import type { ArtifactRenderState, ArtifactRevisionSaveOutcome } from "./model";

/**
 * The document from the complaint, in miniature: a heading, prose, a table
 * whose first column is a markdown LINK, and a closing sentence. Every property
 * this file asserts is about what survives an edit to three of those spans.
 */
const SOURCE = [
  "# My Assigned Linear Issues",
  "",
  "Here are the five issues currently assigned to you.",
  "",
  "| Issue | Status | Priority |",
  "| --- | --- | --- |",
  "| [PAR-9 – Rent roll import errors](https://linear.app/acme/issue/PAR-9) | Cool | High |",
  "| [PAR-12 – Stale webhook retries](https://linear.app/acme/issue/PAR-12) | Cool | Medium |",
  "",
  "All five issues are currently in Cool status.",
  "",
].join("\n");

function documentState(source: string, editor: unknown): ArtifactRenderState {
  return {
    artifactId: "art_doc",
    kind: "document",
    title: "My Assigned Linear Issues",
    mediaType: "text/markdown",
    filename: "issues.md",
    revision: 3,
    digest: "d".repeat(64),
    byteSize: source.length,
    author: "model",
    createdAt: "2026-08-06T00:00:00Z",
    preview: "ready",
    text: source,
    ...(editor === undefined ? {} : { documentEditor: editor }),
  } as ArtifactRenderState;
}

function editableDocument(
  source = SOURCE,
  outcome: ArtifactRevisionSaveOutcome = "saved",
): {
  readonly saveRevision: ReturnType<typeof vi.fn>;
  readonly state: ArtifactRenderState;
} {
  const saveRevision = vi.fn(async (_source: string) => outcome);
  return {
    saveRevision,
    state: documentState(source, { disabled: false, saveRevision }),
  };
}

function editCell(testId: string, value: string): void {
  fireEvent.click(screen.getByTestId(testId));
  const field = screen.getByTestId(`${testId}-input`);
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: "Enter" });
}

/** Which LINES differ, so "only these spans changed" is stated, not implied. */
function changedLines(
  before: string,
  after: string,
): readonly (readonly [number, string, string])[] {
  const from = before.split("\n");
  const to = after.split("\n");
  const changes: (readonly [number, string, string])[] = [];
  for (let index = 0; index < Math.max(from.length, to.length); index += 1) {
    if (from[index] !== to[index])
      changes.push([index, from[index] ?? "", to[index] ?? ""]);
  }
  return changes;
}

describe("EditableDocument", () => {
  it("edits two cells and a paragraph in place, sends nothing until Save, and then changes only those three spans", async () => {
    const { saveRevision } = renderEditable();

    // The table is a real table of real cells, and the link is a real link —
    // the thing the one-string markdown render could never give a user to
    // click into.
    expect(screen.getByTestId("doc-cell-2-0-1")).toHaveTextContent("Cool");
    expect(
      within(screen.getByTestId("doc-cell-2-0-0")).getByRole("link", {
        name: /PAR-9/,
      }),
    ).toHaveAttribute("href", "https://linear.app/acme/issue/PAR-9");

    editCell("doc-cell-2-0-1", "Warm");
    editCell("doc-cell-2-1-2", "Low");

    fireEvent.click(screen.getByTestId("doc-block-3"));
    const prose = screen.getByTestId("doc-block-3-input");
    fireEvent.change(prose, {
      target: { value: "Two of the five issues have moved off Cool." },
    });
    fireEvent.keyDown(prose, { key: "Enter" });

    // Three spans differ, and NOTHING has been sent.
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "3");
    expect(screen.getByTestId("doc-editor-status")).toHaveTextContent(
      "3 unsaved edits",
    );
    expect(saveRevision).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(saved).toBe(
      [
        "# My Assigned Linear Issues",
        "",
        "Here are the five issues currently assigned to you.",
        "",
        "| Issue | Status | Priority |",
        "| --- | --- | --- |",
        "| [PAR-9 – Rent roll import errors](https://linear.app/acme/issue/PAR-9) | Warm | High |",
        "| [PAR-12 – Stale webhook retries](https://linear.app/acme/issue/PAR-12) | Cool | Low |",
        "",
        "Two of the five issues have moved off Cool.",
        "",
      ].join("\n"),
    );
    // Stated independently of the literal above: three lines moved, each in
    // exactly the cell or sentence that was edited. The heading, the delimiter
    // row, the prose above the table, both issue links and every blank line are
    // byte-identical — which is the splice, not a diff that happens to be small.
    expect(changedLines(SOURCE, saved)).toEqual([
      [
        6,
        "| [PAR-9 – Rent roll import errors](https://linear.app/acme/issue/PAR-9) | Cool | High |",
        "| [PAR-9 – Rent roll import errors](https://linear.app/acme/issue/PAR-9) | Warm | High |",
      ],
      [
        7,
        "| [PAR-12 – Stale webhook retries](https://linear.app/acme/issue/PAR-12) | Cool | Medium |",
        "| [PAR-12 – Stale webhook retries](https://linear.app/acme/issue/PAR-12) | Cool | Low |",
      ],
      [
        9,
        "All five issues are currently in Cool status.",
        "Two of the five issues have moved off Cool.",
      ],
    ]);
  });

  it("commits with Enter, reverts with Escape, and walks the table with Tab", () => {
    renderEditable();

    // Escape puts the cell back and leaves nothing pending.
    fireEvent.click(screen.getByTestId("doc-cell-2-0-2"));
    fireEvent.change(screen.getByTestId("doc-cell-2-0-2-input"), {
      target: { value: "Urgent" },
    });
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
    fireEvent.keyDown(screen.getByTestId("doc-cell-2-0-2-input"), {
      key: "Escape",
    });
    expect(screen.getByTestId("doc-cell-2-0-2")).toHaveTextContent("High");
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");

    // Tab commits and steps to the next cell of the same table; the last cell
    // of a row wraps into the first cell of the next.
    fireEvent.click(screen.getByTestId("doc-cell-2-0-1"));
    fireEvent.change(screen.getByTestId("doc-cell-2-0-1-input"), {
      target: { value: "Warm" },
    });
    fireEvent.keyDown(screen.getByTestId("doc-cell-2-0-1-input"), {
      key: "Tab",
    });
    expect(screen.getByTestId("doc-cell-2-0-2-input")).toHaveValue("High");
    fireEvent.keyDown(screen.getByTestId("doc-cell-2-0-2-input"), {
      key: "Tab",
    });
    expect(screen.getByTestId("doc-cell-2-1-0-input")).toHaveValue(
      "[PAR-12 – Stale webhook retries](https://linear.app/acme/issue/PAR-12)",
    );
    // Shift+Tab walks back the same sequence.
    fireEvent.keyDown(screen.getByTestId("doc-cell-2-1-0-input"), {
      key: "Tab",
      shiftKey: true,
    });
    expect(screen.getByTestId("doc-cell-2-0-2-input")).toBeInTheDocument();
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
  });

  it("keeps the header row editable and escapes a typed pipe so a cell cannot open a column", async () => {
    const { saveRevision } = renderEditable();

    editCell("doc-header-2-1", "State");
    editCell("doc-cell-2-0-2", "High | urgent");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(saved).toContain("| Issue | State | Priority |");
    expect(saved).toContain("| Cool | High \\| urgent |");
    // The delimiter row still declares three columns, because the typed pipe
    // never became one.
    expect(saved).toContain("| --- | --- | --- |");
  });

  it("discards every pending edit without sending anything", () => {
    const { saveRevision } = renderEditable();

    editCell("doc-cell-2-0-1", "Warm");
    editCell("doc-cell-2-1-1", "Warm");
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "2");

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");
    expect(screen.getByTestId("doc-cell-2-0-1")).toHaveTextContent("Cool");
    expect(saveRevision).not.toHaveBeenCalled();
  });

  it("retains the local batch after a 409-style conflict and never auto-merges", async () => {
    renderEditable(SOURCE, "conflict");

    editCell("doc-cell-2-0-1", "Warm");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "local edits are preserved",
      ),
    );
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
    expect(screen.getByTestId("doc-cell-2-0-1")).toHaveTextContent("Warm");
  });

  it("typing the original value back clears the edit rather than saving a no-op", () => {
    renderEditable();

    editCell("doc-cell-2-0-1", "Warm");
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
    editCell("doc-cell-2-0-1", "Cool");
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("round-trips a document whose constructs the block model does not model", async () => {
    const source = [
      "> quoted line",
      "",
      "- a list item",
      "- another",
      "",
      "```js",
      "const table = '| not | a | table |';",
      "```",
      "",
      "| A | B |",
      "| --- | --- |",
      "| one | two |",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);

    // The fenced pipes were never mistaken for a table: exactly one table
    // exists, and it is the real one.
    expect(screen.getAllByRole("table")).toHaveLength(1);
    editCell("doc-cell-3-0-1", "three");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));
    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(saved).toBe(source.replace("| one | two |", "| one | three |"));
  });

  it("grants no edit affordance at all when the host disabled writing", () => {
    render(
      <DocumentArtifactRenderer
        artifact={documentState(SOURCE, {
          disabled: true,
          saveRevision: vi.fn(async () => "saved" as const),
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("doc-cell-2-0-1"));
    expect(screen.queryByTestId("doc-cell-2-0-1-input")).toBeNull();
    expect(
      screen.getByTestId("doc-cell-2-0-1").parentElement,
    ).not.toHaveAttribute("tabindex");
  });
});

describe("DocumentArtifactRenderer", () => {
  it("renders read-only markdown, and no editor, when the host granted nothing", () => {
    const { container } = render(
      <DocumentArtifactRenderer artifact={documentState(SOURCE, undefined)} />,
    );
    expect(
      screen.getByTestId("artifact-document-renderer"),
    ).not.toHaveAttribute("data-editable");
    expect(screen.queryByTestId("doc-editor")).toBeNull();
    expect(container.querySelector("textarea")).toBeNull();
    // Still a rendered document — the read-only path is streamdown's own
    // markdown, link affordance and all. It is simply not clickable INTO.
    expect(
      screen.getByText("PAR-9 – Rent roll import errors"),
    ).toBeInTheDocument();
  });

  it("refuses a malformed grant instead of treating it as permission", () => {
    render(
      <DocumentArtifactRenderer
        artifact={documentState(SOURCE, { disabled: false })}
      />,
    );
    expect(screen.queryByTestId("doc-editor")).toBeNull();
  });

  it("never executes embedded HTML on the editable path either", () => {
    const { container } = render(
      <DocumentArtifactRenderer
        artifact={
          editableDocument("# Notes\n\n<script>window.pwned = true</script>\n")
            .state
        }
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container).not.toHaveTextContent("window.pwned");
  });

  it("scopes its field names when the host mounts two documents at once", () => {
    render(
      <DocumentArtifactRenderer
        artifact={documentState(SOURCE, {
          disabled: false,
          saveRevision: vi.fn(async () => "saved" as const),
          idPrefix: "inline-art_doc",
        })}
      />,
    );
    expect(screen.getByTestId("inline-art_doc-cell-2-0-1")).toHaveTextContent(
      "Cool",
    );
    expect(screen.queryByTestId("doc-cell-2-0-1")).toBeNull();
  });
});

function renderEditable(
  source = SOURCE,
  outcome: ArtifactRevisionSaveOutcome = "saved",
): { readonly saveRevision: ReturnType<typeof vi.fn> } {
  const { saveRevision, state } = editableDocument(source, outcome);
  render(<DocumentArtifactRenderer artifact={state} />);
  return { saveRevision };
}
