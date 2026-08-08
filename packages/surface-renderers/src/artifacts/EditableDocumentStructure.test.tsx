// Structure, driven the way a user drives it.
//
// `EditableDocument.test.tsx` covers editing a span that already exists. This
// file covers changing WHICH spans there are, and the three properties that
// make those controls safe to put in front of somebody:
//
//   1. **They batch with everything else.** One Save carries a new row, the
//      typing in it, a deleted row and an added column — because the pending
//      text edits are addressed against the working document the structural
//      changes produced, never merged into one batch with them.
//   2. **Deleting is undoable until Save.** Discard brings a deleted row back.
//      Without that, one mis-click destroys a row of a user's data and the
//      control is a trap however few clicks it saves.
//   3. **The rest of the document does not move.** Every assertion below reads
//      the saved bytes back: the lines outside the table are compared to the
//      source they came from, character for character.
//
// The controls moved after this file was written — a block's five buttons
// became a hover/focus gutter of two, and the operations they used to spell out
// live in that gutter's `+` menu. Nothing here asserts a control's POSITION
// except the tests that exist to; everything else goes through `fromBlockMenu`
// / `fromDocumentMenu`, which is what a user does, and the assertions about
// what a change DOES are untouched.

import { parseBlocks } from "@0x-copilot/chat-surface";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
import type { ArtifactRenderState } from "./model";

/** A heading, prose, a table of three rows, and a closing sentence. */
const BOARD = [
  "# Sprint board",
  "",
  "Three issues are open.",
  "",
  "| Issue | Status | Priority |",
  "| --- | --- | --- |",
  "| PAR-9 | Cool | High |",
  "| PAR-12 | Cool | Medium |",
  "| PAR-14 | Warm | Low |",
  "",
  "All three are unresolved.",
  "",
].join("\n");

/** The lines that no operation in this file is allowed to touch. */
function outsideTheTable(document: string): readonly string[] {
  const lines = document.split("\n");
  return [...lines.slice(0, 4), ...lines.slice(-3)];
}

function documentState(source: string, editor: unknown): ArtifactRenderState {
  return {
    artifactId: "art_doc",
    kind: "document",
    title: "Sprint board",
    mediaType: "text/markdown",
    filename: "board.md",
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

function renderEditable(source = BOARD): {
  readonly saveRevision: ReturnType<typeof vi.fn>;
} {
  const saveRevision = vi.fn(async (_source: string) => "saved" as const);
  render(
    <DocumentArtifactRenderer
      artifact={documentState(source, { disabled: false, saveRevision })}
    />,
  );
  return { saveRevision };
}

function editCell(testId: string, value: string): void {
  fireEvent.click(screen.getByTestId(testId));
  const field = screen.getByTestId(`${testId}-input`);
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: "Enter" });
}

function press(testId: string): void {
  fireEvent.click(screen.getByTestId(testId));
}

/**
 * Opens one block's gutter menu and presses an item in it.
 *
 * The menu's items exist only while it is open, which is the point: a control
 * that is mounted but hidden is one `getByRole` refuses to find and a screen
 * reader announces anyway. Two presses here is what one press plus a hover is
 * for a user.
 */
function fromBlockMenu(block: number, testId: string): void {
  press(`doc-block-menu-${block}`);
  press(testId);
}

/** The document's own `+` — the one control that is drawn without a hover. */
function fromDocumentMenu(testId: string): void {
  press("doc-document-menu");
  press(testId);
}

function dirty(): string | null {
  return screen.getByTestId("doc-editor").getAttribute("data-dirty");
}

async function saveAnd(
  saveRevision: ReturnType<typeof vi.fn>,
): Promise<string> {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saveRevision).toHaveBeenCalledTimes(1));
  return (saveRevision.mock.calls[0] as [string])[0];
}

function textOf(testId: string): string {
  return screen.getByTestId(testId).textContent ?? "";
}

describe("EditableDocument — structural edits", () => {
  it("adds a row, types into it, deletes a different row, edits a third, adds a column, and saves all of it at once", async () => {
    const { saveRevision } = renderEditable();

    // A staged row is a REAL row: it has cells with spans, which is what makes
    // it something to type into rather than a placeholder to save first.
    fromBlockMenu(2, "doc-add-row-2");
    expect(textOf("doc-cell-2-3-0")).toBe("");
    editCell("doc-cell-2-3-0", "PAR-20");
    editCell("doc-cell-2-3-1", "Cool");
    editCell("doc-cell-2-3-2", "High");

    // A DIFFERENT row goes. The typing above it survives and moves up one — the
    // case that produces two overlapping edits if the two kinds of change are
    // merged into one batch instead of staged.
    press("doc-delete-row-2-1");
    expect(screen.queryByTestId("doc-cell-2-3-0")).toBeNull();
    expect(textOf("doc-cell-2-2-0")).toBe("PAR-20");
    expect(textOf("doc-cell-2-1-0")).toBe("PAR-14");

    editCell("doc-cell-2-1-2", "Medium");
    fromBlockMenu(2, "doc-add-column-2");

    // Seven changes, and not one of them has left: three structural, four typed.
    expect(dirty()).toBe("7");
    expect(screen.getByTestId("doc-editor-status")).toHaveTextContent(
      "7 unsaved edits",
    );
    expect(saveRevision).not.toHaveBeenCalled();

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "# Sprint board",
        "",
        "Three issues are open.",
        "",
        "| Issue | Status | Priority |  |",
        "| --- | --- | --- | --- |",
        "| PAR-9 | Cool | High |  |",
        "| PAR-14 | Warm | Medium |  |",
        "| PAR-20 | Cool | High |  |",
        "",
        "All three are unresolved.",
        "",
      ].join("\n"),
    );

    // Said again as shape rather than as bytes: the document still parses into
    // the same four blocks, and the table gained a column the DELIMITER row
    // agrees about — a header that outgrew its delimiter is not a table at all,
    // and no diff of the string would say so.
    const blocks = parseBlocks(saved);
    expect(blocks.map((block) => block.kind)).toEqual([
      "heading",
      "paragraph",
      "table",
      "paragraph",
    ]);
    const table = blocks[2];
    if (table.kind !== "table") throw new Error("expected a table");
    expect(table.headers).toEqual(["Issue", "Status", "Priority", ""]);
    expect(table.alignments).toHaveLength(4);
    expect(table.rows.map((row) => row.length)).toEqual([4, 4, 4]);

    // And everything outside the table is the source, character for character.
    expect(outsideTheTable(saved)).toEqual(outsideTheTable(BOARD));
  });

  it("brings a deleted row back on Discard, because before Save is the whole window in which it can come back", async () => {
    const { saveRevision } = renderEditable();
    expect(textOf("doc-cell-2-1-0")).toBe("PAR-12");

    press("doc-delete-row-2-1");
    // Gone from the document on screen, and counted as one unsaved change.
    expect(textOf("doc-cell-2-1-0")).toBe("PAR-14");
    expect(screen.queryByTestId("doc-cell-2-2-0")).toBeNull();
    expect(dirty()).toBe("1");
    expect(screen.getByTestId("doc-editor-status")).toHaveTextContent(
      "1 unsaved edit",
    );

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(textOf("doc-cell-2-1-0")).toBe("PAR-12");
    expect(textOf("doc-cell-2-2-0")).toBe("PAR-14");
    expect(dirty()).toBe("0");
    expect(saveRevision).not.toHaveBeenCalled();

    // Nothing was ever sent, so there is no revision holding the deletion
    // either: Save from here writes the document exactly as it arrived.
    editCell("doc-cell-2-0-1", "Warm");
    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(BOARD.replace("| PAR-9 | Cool |", "| PAR-9 | Warm |"));
  });

  it("adds a row under a given row rather than only at the end", async () => {
    const { saveRevision } = renderEditable();

    press("doc-insert-row-2-0");
    editCell("doc-cell-2-1-0", "PAR-11");
    const saved = await saveAnd(saveRevision);

    expect(saved).toBe(
      [
        "# Sprint board",
        "",
        "Three issues are open.",
        "",
        "| Issue | Status | Priority |",
        "| --- | --- | --- |",
        "| PAR-9 | Cool | High |",
        "| PAR-11 |  |  |",
        "| PAR-12 | Cool | Medium |",
        "| PAR-14 | Warm | Low |",
        "",
        "All three are unresolved.",
        "",
      ].join("\n"),
    );
    expect(outsideTheTable(saved)).toEqual(outsideTheTable(BOARD));
  });

  it("deletes a column, header and delimiter together, and refuses to delete the last one", async () => {
    const { saveRevision } = renderEditable();

    press("doc-delete-column-2-1");
    expect(textOf("doc-header-2-1")).toBe("Priority");
    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "# Sprint board",
        "",
        "Three issues are open.",
        "",
        "| Issue | Priority |",
        "| --- | --- |",
        "| PAR-9 | High |",
        "| PAR-12 | Medium |",
        "| PAR-14 | Low |",
        "",
        "All three are unresolved.",
        "",
      ].join("\n"),
    );
    expect(parseBlocks(saved)[2].kind).toBe("table");
  });

  it("draws a ragged row's EXCESS cells nowhere, because the render shows them nowhere either", async () => {
    // remark-gfm truncates a row to the header's width: `| 6 | 7 | 8 | 9 |`
    // under three columns renders `6 7 8`, and the `9` appears nowhere a reader
    // can see. A control over it would hand somebody a value the document never
    // displays and land their typing where no render will show it.
    const source = [
      "| A | B | C |",
      "| --- | --- | --- |",
      "| 1 | 2 |",
      "| 6 | 7 | 8 | 9 |",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);

    expect(textOf("doc-cell-0-1-2")).toBe("8");
    expect(screen.queryByTestId("doc-cell-0-1-3")).toBeNull();
    // The SHORT row is the other rag and keeps its own length: the renderer pads
    // it with an empty cell, the editor cannot, because a padded cell has no
    // span to splice into.
    expect(screen.queryByTestId("doc-cell-0-0-2")).toBeNull();

    // The bytes are untouched by the rule: editing a cell that IS addressable
    // saves a document that still carries the `9`, and it still reparses as one
    // table whose rows are as ragged as they were.
    editCell("doc-cell-0-1-0", "SIX");
    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "| A | B | C |",
        "| --- | --- | --- |",
        "| 1 | 2 |",
        "| SIX | 7 | 8 | 9 |",
        "",
      ].join("\n"),
    );
    const blocks = parseBlocks(saved);
    expect(blocks.map((block) => block.kind)).toEqual(["table"]);
    const table = blocks[0];
    if (table.kind !== "table") throw new Error("expected a table");
    expect(table.rows.map((row) => row.length)).toEqual([2, 4]);
  });

  it("gives an excess cell back the moment the table has a column for it", async () => {
    // The other half of the same rule, and the reason hiding the cell does not
    // strand it: the excess is not dropped, it is out of view, and a fourth
    // column brings it into view holding exactly what the user wrote.
    const source = [
      "| A | B | C |",
      "| --- | --- | --- |",
      "| 6 | 7 | 8 | 9 |",
      "",
    ].join("\n");
    const { saveRevision } = renderEditable(source);

    expect(screen.queryByTestId("doc-cell-0-0-3")).toBeNull();
    fromBlockMenu(0, "doc-add-column-0");
    expect(textOf("doc-cell-0-0-3")).toBe("9");

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "| A | B | C |  |",
        "| --- | --- | --- | --- |",
        "| 6 | 7 | 8 | 9 |  |",
        "",
      ].join("\n"),
    );
    const blocks = parseBlocks(saved);
    expect(blocks.map((block) => block.kind)).toEqual(["table"]);
    const table = blocks[0];
    if (table.kind !== "table") throw new Error("expected a table");
    expect(table.headers).toHaveLength(4);
    expect(table.rows.map((row) => row.length)).toEqual([5]);
  });

  it("disables the last column's Delete instead of leaving a table with no columns", () => {
    renderEditable(["| Only |", "| --- |", "| a |", ""].join("\n"));
    // At the column, and disabled rather than absent: the header keeps the same
    // shape whether or not the operation it offers can be performed.
    expect(screen.getByTestId("doc-delete-column-0-0")).toBeDisabled();
    // The way to empty a one-column table is to say so.
    press("doc-block-menu-0");
    expect(
      screen.getByRole("button", { name: "Delete Table 1" }),
    ).toBeEnabled();
  });

  it("adds a paragraph and a table after a given block, and both are editable where they land", async () => {
    const { saveRevision } = renderEditable();

    fromBlockMenu(0, "doc-insert-paragraph-1");
    expect(textOf("doc-block-1")).toBe("New paragraph");
    fireEvent.click(screen.getByTestId("doc-block-1"));
    fireEvent.change(screen.getByTestId("doc-block-1-input"), {
      target: { value: "Sprint 42 starts Monday." },
    });
    fireEvent.keyDown(screen.getByTestId("doc-block-1-input"), {
      key: "Enter",
    });

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      BOARD.replace(
        "Three issues are open.",
        "Sprint 42 starts Monday.\n\nThree issues are open.",
      ),
    );
  });

  it("adds a table with a row already in it, so there is somewhere to type", async () => {
    const { saveRevision } = renderEditable("# Notes\n");

    fromBlockMenu(0, "doc-insert-table-1");
    editCell("doc-header-1-0", "Task");
    editCell("doc-cell-1-0-0", "Draft the memo");

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "# Notes",
        "",
        "| Task | Column 2 |",
        "| --- | --- |",
        "| Draft the memo |  |",
        "",
      ].join("\n"),
    );
    expect(parseBlocks(saved).map((block) => block.kind)).toEqual([
      "heading",
      "table",
    ]);
  });

  it("moves a block up and down without reflowing the blocks around it", async () => {
    const { saveRevision } = renderEditable();

    fromBlockMenu(0, "doc-move-down-0");
    expect(textOf("doc-block-0")).toBe("Three issues are open.");
    expect(textOf("doc-block-1")).toBe("Sprint board");
    fromBlockMenu(1, "doc-move-up-1");
    expect(textOf("doc-block-0")).toBe("Sprint board");

    // Two moves that cancel out still count as two changes — this is a batch of
    // what happened, not a diff of where it ended up.
    expect(dirty()).toBe("2");
    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(BOARD);
  });

  it("refuses to move a block into the blank run above it, which would swallow the document's last block", () => {
    renderEditable("\n\n# Title\n\nProse.\n");
    // Block 0 is that blank run: it renders nothing, so there is no gutter on
    // it and nothing above block 1 to trade places with.
    expect(screen.queryByTestId("doc-actions-0")).toBeNull();
    expect(screen.queryByTestId("doc-slot-0")).toBeNull();
    press("doc-block-menu-1");
    expect(screen.getByTestId("doc-move-up-1")).toBeDisabled();
    expect(screen.getByTestId("doc-move-down-1")).toBeEnabled();

    // The drag handle refuses the same move for the same reason: an arrow key
    // is the keyboard's half of the gesture, and it obeys one rule, not two.
    const handle = screen.getByTestId("doc-reorder-1");
    handle.focus();
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(dirty()).toBe("0");
    expect(textOf("doc-block-1")).toBe("Title");
  });

  it("deletes a block, and the last one leaves a document with a way back into it", async () => {
    const { saveRevision } = renderEditable("Only this.\n");

    fromBlockMenu(0, "doc-delete-block-0");
    expect(screen.queryByTestId("doc-block-0")).toBeNull();
    expect(dirty()).toBe("1");

    // An empty document still has the one control that can start it again —
    // and it is the one that is drawn without a hover, because an empty
    // document has nothing left to hover.
    fromDocumentMenu("doc-insert-paragraph-0");
    expect(textOf("doc-block-0")).toBe("New paragraph");
    expect(dirty()).toBe("2");

    expect(await saveAnd(saveRevision)).toBe("New paragraph");
  });

  it("carries a pending cell edit with the block it belongs to when that block moves", async () => {
    const { saveRevision } = renderEditable();

    editCell("doc-cell-2-0-1", "Warm");
    fromBlockMenu(2, "doc-move-up-2");

    // The table is block 1 now, and the typing followed it there rather than
    // staying at the coordinates it was typed at — still marked as the edit it
    // is, at its new address.
    expect(textOf("doc-cell-1-0-1")).toBe("Warm");
    expect(screen.getByTestId("doc-cell-1-0-1")).toHaveAttribute(
      "data-modified",
      "true",
    );
    expect(textOf("doc-block-2")).toBe("Three issues are open.");
    expect(dirty()).toBe("2");

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "# Sprint board",
        "",
        "| Issue | Status | Priority |",
        "| --- | --- | --- |",
        "| PAR-9 | Warm | High |",
        "| PAR-12 | Cool | Medium |",
        "| PAR-14 | Warm | Low |",
        "",
        "Three issues are open.",
        "",
        "All three are unresolved.",
        "",
      ].join("\n"),
    );
  });

  it("drops only the typing that was inside the row being deleted", async () => {
    const { saveRevision } = renderEditable();

    editCell("doc-cell-2-1-1", "doomed");
    editCell("doc-cell-2-2-1", "Hot");
    expect(dirty()).toBe("2");

    press("doc-delete-row-2-1");
    // The row went and took its own edit with it; the other edit is still
    // pending, on the row it was typed into.
    expect(dirty()).toBe("2");
    expect(textOf("doc-cell-2-1-1")).toBe("Hot");

    const saved = await saveAnd(saveRevision);
    expect(saved).toBe(
      [
        "# Sprint board",
        "",
        "Three issues are open.",
        "",
        "| Issue | Status | Priority |",
        "| --- | --- | --- |",
        "| PAR-9 | Cool | High |",
        "| PAR-14 | Hot | Low |",
        "",
        "All three are unresolved.",
        "",
      ].join("\n"),
    );
    expect(saved.includes("doomed")).toBe(false);
  });

  it("refuses a change it cannot carry the pending edits across, and changes nothing", () => {
    renderEditable("\n\n# Title\n\nProse.\n");

    fireEvent.click(screen.getByTestId("doc-block-1"));
    fireEvent.change(screen.getByTestId("doc-block-1-input"), {
      target: { value: "Retitled" },
    });
    fireEvent.keyDown(screen.getByTestId("doc-block-1-input"), {
      key: "Enter",
    });

    // Inserting above a leading run of blank lines is the one arrangement whose
    // reparse the index arithmetic gets wrong: the blank run is absorbed into
    // the new paragraph, so the blocks below do not shift the way it assumed.
    // The check on the far side catches it and the whole change is refused —
    // the alternative is a typed value spliced into a span nobody typed into.
    fromDocumentMenu("doc-insert-paragraph-0");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not add that change",
    );
    expect(dirty()).toBe("1");
    expect(textOf("doc-block-1")).toBe("Retitled");
    expect(screen.queryByTestId("doc-block-3")).toBeNull();
  });

  it("puts every control in the keyboard's reach, named for what it acts on", () => {
    renderEditable();

    function realButton(name: string): HTMLElement {
      const button = screen.getByRole("button", { name });
      expect(button.tagName).toBe("BUTTON");
      expect(button).toHaveAttribute("type", "button");
      return button;
    }

    // The chrome that is in the tab order before any pointer moves. `getByRole`
    // refuses a node the accessibility tree does not carry, so finding these
    // IS the assertion: the gutter and the row and column controls are QUIET
    // (opacity), never `display: none` and never mounted on a hover.
    for (const name of [
      "Reorder Table 3",
      "Insert or delete around Table 3",
      "Add row below row 2 of Table 3",
      "Delete row 2 of Table 3",
      "Delete column Status of Table 3",
      "Add a block at the top of the document",
    ]) {
      realButton(name);
    }

    // Everything else is one press away, in that block's own menu — moved, not
    // removed. The trigger says whether it is open and the panel is named for
    // the block, so the plain verbs inside it are announced in context.
    const trigger = screen.getByTestId("doc-block-menu-2");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", "doc-block-menu-2-panel");
    press("doc-block-menu-2");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("doc-block-menu-2-panel")).toHaveAttribute(
      "aria-label",
      "Change Table 3",
    );
    for (const name of [
      "Add row to Table 3",
      "Add column to Table 3",
      "Add paragraph after Table 3",
      "Add table after Table 3",
      "Move up: Table 3",
      "Move down: Table 3",
      "Delete Table 3",
    ]) {
      realButton(name);
    }
    // Move up and Move down survive the loss of their always-visible buttons
    // for one reason: a drag is not something a keyboard can do, and every
    // operation a pointer can reach a keyboard has to be able to reach too.
    expect(screen.getByTestId("doc-move-up-2")).toBeEnabled();

    // A move with nowhere to go is disabled rather than absent, so the menu
    // does not change shape as blocks are reordered.
    press("doc-block-menu-0");
    expect(screen.getByTestId("doc-move-up-0")).toBeDisabled();
    press("doc-block-menu-3");
    expect(screen.getByTestId("doc-move-down-3")).toBeDisabled();
    // Opening one menu closes the last: only ever one panel over the document.
    expect(screen.queryByTestId("doc-move-up-0")).toBeNull();

    // The document's own menu keeps its two items and their whole-sentence
    // names — this is the boundary a block's menu cannot offer, and an empty
    // document's only way back in.
    press("doc-document-menu");
    realButton("Add paragraph at the top of the document");
    realButton("Add table at the top of the document");
  });

  it("keeps the chrome quiet until the block, row or column is asked for", () => {
    renderEditable();

    // The default state of a document is the document. Every control is
    // present, named and focusable; none of them is drawn.
    for (const testId of [
      "doc-reorder-2",
      "doc-block-menu-2",
      "doc-insert-row-2-0",
      "doc-delete-row-2-0",
      "doc-delete-column-2-1",
    ]) {
      expect(screen.getByTestId(testId)).not.toBeVisible();
    }
    // One exception, and the empty document is the whole reason for it: delete
    // the last block and there is nothing left to hover, so the way back has to
    // be visible from the start.
    expect(screen.getByTestId("doc-document-menu")).toBeVisible();

    // A pointer over a cell reveals that block's gutter and that ROW's own
    // controls — not the other rows', and not the column's.
    fireEvent.mouseOver(screen.getByTestId("doc-cell-2-0-0"));
    expect(screen.getByTestId("doc-reorder-2")).toBeVisible();
    expect(screen.getByTestId("doc-delete-row-2-0")).toBeVisible();
    expect(screen.getByTestId("doc-delete-row-2-1")).not.toBeVisible();
    expect(screen.getByTestId("doc-delete-column-2-1")).not.toBeVisible();
    fireEvent.mouseOut(screen.getByTestId("doc-cell-2-0-0"));
    expect(screen.getByTestId("doc-delete-row-2-0")).not.toBeVisible();

    // A column's control answers to its own header, and to nothing else.
    fireEvent.mouseOver(screen.getByTestId("doc-header-2-1"));
    expect(screen.getByTestId("doc-delete-column-2-1")).toBeVisible();
    expect(screen.getByTestId("doc-delete-column-2-0")).not.toBeVisible();

    // FOCUS reveals exactly what hover reveals. This is the half that makes
    // "on hover" something other than a regression: tab to the handle and it
    // appears, with no pointer anywhere near the document.
    expect(screen.getByTestId("doc-reorder-3")).not.toBeVisible();
    // A REAL focus, the one Tab performs — not a synthetic focus event. `act`
    // is only there because a native focus outside an event handler leaves
    // React's re-render unflushed.
    act(() => {
      screen.getByTestId("doc-reorder-3").focus();
    });
    expect(screen.getByTestId("doc-reorder-3")).toHaveFocus();
    expect(screen.getByTestId("doc-reorder-3")).toBeVisible();
    expect(screen.getByTestId("doc-reorder-2")).not.toBeVisible();
  });

  it("reorders by dragging the handle, in one batch that Discard takes back whole", () => {
    renderEditable();

    // Three places is three adjacent swaps, and they arrive as one refusable
    // batch — three unsaved edits, one document, nothing sent.
    fireEvent.dragStart(screen.getByTestId("doc-reorder-3"));
    fireEvent.dragOver(screen.getByTestId("doc-slot-0"));
    fireEvent.drop(screen.getByTestId("doc-slot-0"));

    expect(textOf("doc-block-0")).toBe("All three are unresolved.");
    expect(textOf("doc-block-1")).toBe("Sprint board");
    expect(textOf("doc-block-2")).toBe("Three issues are open.");
    expect(screen.getByTestId("doc-table-3")).toBeInTheDocument();
    expect(dirty()).toBe("3");

    // And the whole drag comes back, because a structural change is staged in
    // the same batch a keystroke joins.
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(textOf("doc-block-0")).toBe("Sprint board");
    expect(dirty()).toBe("0");
  });

  it("reorders from the keyboard, and the focus follows the block rather than the slot", () => {
    renderEditable();

    const handle = screen.getByTestId("doc-reorder-0");
    handle.focus();
    fireEvent.keyDown(handle, { key: "ArrowDown" });
    expect(textOf("doc-block-0")).toBe("Three issues are open.");
    expect(textOf("doc-block-1")).toBe("Sprint board");

    // The focus travelled WITH the heading. Without that, a second press moves
    // whatever slid into the slot the first press vacated — a keyboard reorder
    // that reorders something else is worse than none.
    expect(screen.getByTestId("doc-reorder-1")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("doc-reorder-1"), {
      key: "ArrowDown",
    });
    expect(textOf("doc-block-2")).toBe("Sprint board");
    expect(dirty()).toBe("2");
  });

  it("closes a menu on Escape and hands focus back to the control that opened it", () => {
    renderEditable();

    const trigger = screen.getByTestId("doc-block-menu-2");
    press("doc-block-menu-2");
    const item = screen.getByTestId("doc-delete-block-2");
    item.focus();
    fireEvent.keyDown(item, { key: "Escape" });

    expect(screen.queryByTestId("doc-delete-block-2")).toBeNull();
    expect(trigger).toHaveFocus();
    // Escape out of a menu changes the document in no way at all.
    expect(dirty()).toBe("0");
  });

  it("keeps its controls out of the cells they act on", () => {
    renderEditable();

    // The row's controls live in a cell of their own at the end of the row, not
    // over the content: the cell holding Delete holds no editable span, and the
    // cell holding a span holds no button.
    const control = screen.getByTestId("doc-delete-row-2-0").closest("td");
    const cell = screen.getByTestId("doc-cell-2-0-0").closest("td");
    expect(control).not.toBe(cell);
    expect(control?.querySelector("[data-testid^='doc-cell-']")).toBeNull();
    expect(cell?.querySelector("button")).toBeNull();

    // And pressing one never opens the editor for the cell beside it.
    press("doc-delete-row-2-0");
    expect(screen.queryByTestId("doc-cell-2-0-0-input")).toBeNull();
  });

  it("draws no structural control at all when the host disabled writing", () => {
    render(
      <DocumentArtifactRenderer
        artifact={documentState(BOARD, {
          disabled: true,
          saveRevision: vi.fn(async () => "saved" as const),
        })}
      />,
    );
    for (const testId of [
      "doc-add-row-2",
      "doc-delete-row-2-0",
      "doc-insert-row-2-0",
      "doc-add-column-2",
      "doc-delete-column-2-0",
      "doc-actions-2",
      "doc-reorder-2",
      "doc-block-menu-2",
      "doc-document-actions",
      "doc-document-menu",
    ]) {
      expect(screen.queryByTestId(testId)).toBeNull();
    }
    expect(screen.queryAllByRole("button")).toHaveLength(2);
  });
});
