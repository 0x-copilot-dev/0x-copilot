import { parseBlocks } from "@0x-copilot/chat-surface";
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

/**
 * The shape that had NO editable span at all: a checklist and nothing else.
 *
 * It parses to exactly one `raw` block, so before a raw block was editable the
 * toolbar said "click any cell or paragraph" over a document containing neither
 * — zero controls, and a Save that could never enable. It is also the second
 * most common artifact shape after a table.
 */
const CHECKLIST = ["- [ ] Draft the memo", "- [ ] Send it to Ana", ""].join(
  "\n",
);

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

/**
 * Opens a raw block's own field and types into it, closing with ⌘Enter.
 *
 * Enter deliberately does NOT commit there — in a list or a fence the lines are
 * the construct — so this helper spells out the way out that does.
 */
function editRaw(testId: string, value: string): void {
  fireEvent.click(screen.getByTestId(testId));
  const field = screen.getByTestId(`${testId}-input`);
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: "Enter", metaKey: true });
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

  it("edits a checklist-only document, the shape with no cell and no paragraph to click", async () => {
    const { saveRevision } = renderEditable(CHECKLIST);

    // One block, and it IS the document. Nothing else is on screen to edit.
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByTestId("doc-block-0")).toBeNull();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByTestId("doc-raw-0"));
    const field = screen.getByTestId("doc-raw-0-input");
    // The field holds this block's own text and no byte of anything else — the
    // difference between editing in place and the whole-document textarea that
    // was deleted.
    expect(field).toHaveValue("- [ ] Draft the memo\n- [ ] Send it to Ana");
    expect(field).toHaveAccessibleName("List 1");

    fireEvent.change(field, {
      target: { value: "- [x] Draft the memo\n- [ ] Send it to Ana" },
    });
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
    expect(screen.getByTestId("doc-editor-status")).toHaveTextContent(
      "1 unsaved edit",
    );
    expect(saveRevision).not.toHaveBeenCalled();

    // The Save that could never enable, enabled.
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(saved).toBe(
      ["- [x] Draft the memo", "- [ ] Send it to Ana", ""].join("\n"),
    );
    // One line moved, one box got ticked, and the document's final newline is
    // still exactly where it was.
    expect(changedLines(CHECKLIST, saved)).toEqual([
      [0, "- [ ] Draft the memo", "- [x] Draft the memo"],
    ]);
  });

  it("keeps the blank line an edited list absorbed, so the list is not welded to the block below", async () => {
    const source = [
      "- one",
      "- two",
      "",
      "Prose under the list.",
      "",
      "```js",
      "const a = 1;",
      "```",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);

    // Adding an item is a legal edit of the list's own text. The block's
    // FOOTPRINT includes the blank line beneath it; its editable span does not,
    // and that difference is the whole test.
    editRaw("doc-raw-0", "- one\n- two\n- three");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(saved).toBe(
      [
        "- one",
        "- two",
        "- three",
        "",
        "Prose under the list.",
        "",
        "```js",
        "const a = 1;",
        "```",
        "",
      ].join("\n"),
    );
    // Stated structurally as well as by bytes: the document still parses into
    // the same three blocks. Splicing the footprint would have produced
    // `- three` immediately followed by the prose — one block, and a paragraph
    // swallowed into a list.
    expect(
      parseBlocks(saved).map((block) =>
        block.kind === "raw" ? `raw:${block.reason}` : block.kind,
      ),
    ).toEqual(["raw:list", "paragraph", "raw:fenced-code"]);
  });

  it("batches a quote, a fence and a table cell into one revision, then discards them together", async () => {
    const source = [
      "> Ana asked for this by Friday.",
      "",
      "| Task | Owner |",
      "| --- | --- |",
      "| Memo | Ana |",
      "",
      "```sh",
      "make report",
      "```",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);

    editRaw("doc-raw-0", "> Ana asked for this by Thursday.");
    editCell("doc-cell-1-0-1", "Bo");
    editRaw("doc-raw-2", "```sh\nmake report --fast\n```");
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "3");
    expect(saveRevision).not.toHaveBeenCalled();

    // Discard drops raw blocks with the rest of the batch, and the rendered
    // markdown goes back to what the document says.
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");
    expect(screen.getByTestId("doc-raw-0")).toHaveTextContent(
      "Ana asked for this by Friday.",
    );

    editRaw("doc-raw-0", "> Ana asked for this by Thursday.");
    editCell("doc-cell-1-0-1", "Bo");
    editRaw("doc-raw-2", "```sh\nmake report --fast\n```");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));

    const [saved] = saveRevision.mock.calls[0] as [string];
    expect(changedLines(source, saved)).toEqual([
      [
        0,
        "> Ana asked for this by Friday.",
        "> Ana asked for this by Thursday.",
      ],
      [4, "| Memo | Ana |", "| Memo | Bo |"],
      [7, "make report", "make report --fast"],
    ]);
  });

  it("adds a line on Enter in a raw block, where lines are the construct, and reverts on Escape", () => {
    renderEditable(CHECKLIST);

    fireEvent.click(screen.getByTestId("doc-raw-0"));
    const field = screen.getByTestId("doc-raw-0-input");
    fireEvent.change(field, {
      target: { value: "- [ ] Draft the memo\n- [ ] Send it to Ana\n- [ ] " },
    });
    // Enter belongs to the textarea here: the session stays open so the browser
    // can insert the newline the user asked for.
    fireEvent.keyDown(field, { key: "Enter" });
    expect(screen.getByTestId("doc-raw-0-input")).toBeInTheDocument();
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");

    // Escape reverts the whole session, exactly as it does for a cell.
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByTestId("doc-raw-0-input")).toBeNull();
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "0");
    expect(screen.getByTestId("doc-raw-0")).toHaveTextContent("Draft the memo");

    // Tab is the other way out, and it keeps what was typed: a raw block has no
    // next cell to move to, so the session simply closes.
    fireEvent.click(screen.getByTestId("doc-raw-0"));
    fireEvent.change(screen.getByTestId("doc-raw-0-input"), {
      target: { value: "- [x] Draft the memo\n- [ ] Send it to Ana" },
    });
    fireEvent.keyDown(screen.getByTestId("doc-raw-0-input"), { key: "Tab" });
    expect(screen.queryByTestId("doc-raw-0-input")).toBeNull();
    expect(screen.getByTestId("doc-editor")).toHaveAttribute("data-dirty", "1");
    expect(screen.getByTestId("doc-raw-0")).toHaveAttribute(
      "data-modified",
      "true",
    );
  });

  it("draws no control over a blank run, which is the whitespace between two blocks", () => {
    renderEditable("\n\n# Title\n\n- only item\n");
    // Block 0 is that leading blank run. It renders nothing at all, so there is
    // no affordance to click and no empty field to tab into.
    expect(screen.queryByTestId("doc-raw-0")).toBeNull();
    expect(screen.getByTestId("doc-block-1")).toHaveTextContent("Title");
    expect(screen.getByTestId("doc-raw-2")).toHaveTextContent("only item");
  });

  it("grants no edit affordance at all when the host disabled writing", () => {
    const { unmount } = render(
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

    // A raw block is no exception: it renders as read-only markdown with no
    // affordance to find, rather than one that refuses on click.
    unmount();
    render(
      <DocumentArtifactRenderer
        artifact={documentState(CHECKLIST, {
          disabled: true,
          saveRevision: vi.fn(async () => "saved" as const),
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("doc-raw-0"));
    expect(screen.queryByTestId("doc-raw-0-input")).toBeNull();
    expect(screen.getByTestId("doc-raw-0")).not.toHaveAttribute("tabindex");
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
