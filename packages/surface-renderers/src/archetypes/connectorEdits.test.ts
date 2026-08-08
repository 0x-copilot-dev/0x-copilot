import { describe, expect, it } from "vitest";

import type { SurfaceColumn } from "../_shared/specTypes";
import {
  buildConnectorRowEdits,
  cellKey,
  editableCellText,
  isEditableCell,
  rowKeyFor,
  rowTitleFor,
  rowsOf,
} from "./connectorEdits";

const COLUMNS: readonly SurfaceColumn[] = [
  { label: "Title", path: "title" },
  { label: "State", path: "state", format: "badge" },
  { label: "Assignee", path: "assignee.login", format: "user" },
  { label: "Labels", path: "labels" },
];

const ROWS = [
  {
    id: "ENG-4",
    title: "Composer drops focus",
    state: "Todo",
    assignee: { login: "jdoe" },
    labels: ["bug", "ui"],
  },
  {
    id: "ENG-9",
    title: "Dark theme contrast",
    state: "Done",
    assignee: { login: "mkim" },
    labels: [],
  },
];

describe("rowKeyFor", () => {
  it("prefers the row's own identifier over its position", () => {
    expect(rowKeyFor(ROWS[0], 0)).toBe("ENG-4");
    expect(rowKeyFor({ number: 128 }, 3)).toBe("128");
    expect(rowKeyFor({ key: "K1" }, 3)).toBe("K1");
  });

  // The weak case, and it is honest about being weak: a payload with no id at
  // all cannot be followed across a re-read that reorders. The row AS READ still
  // travels with the batch, so the server stages against what the user saw.
  it("falls back to the position when the row carries no identity", () => {
    expect(rowKeyFor({ title: "x" }, 2)).toBe("row-2");
    expect(rowKeyFor("scalar", 5)).toBe("row-5");
    expect(rowKeyFor(null, 0)).toBe("row-0");
  });

  it("ignores an identifier that is blank or not a scalar", () => {
    expect(rowKeyFor({ id: "  " }, 1)).toBe("row-1");
    expect(rowKeyFor({ id: { nested: true } }, 1)).toBe("row-1");
    expect(rowKeyFor({ id: Number.NaN }, 1)).toBe("row-1");
  });
});

describe("editableCellText", () => {
  it("offers an empty cell as an empty string — an absent value is authorable", () => {
    expect(editableCellText(null)).toBe("");
    expect(editableCellText(undefined)).toBe("");
  });

  it("carries scalars as their own text", () => {
    expect(editableCellText("open")).toBe("open");
    expect(editableCellText(128)).toBe("128");
    expect(editableCellText(true)).toBe("true");
  });

  // Typing JSON into a cell would send a STRING that merely looks like the list
  // it replaced, and the server's provenance audit is exact about lists for the
  // same reason. A value with no honest text form is simply not offered.
  it("declines a structured value rather than inventing a text form for it", () => {
    expect(editableCellText(["bug", "ui"])).toBeNull();
    expect(editableCellText({ login: "jdoe" })).toBeNull();
    expect(isEditableCell(ROWS[0], COLUMNS[3])).toBe(false);
    expect(isEditableCell(ROWS[0], COLUMNS[2])).toBe(true);
  });
});

describe("rowsOf / rowTitleFor", () => {
  it("resolves items_path and tolerates a payload that is not a list", () => {
    const spec = {
      spec_version: 1,
      archetype: "table",
      source: { server: "s", tool: "t" },
      title_path: "x",
      items_path: "issues",
    } as const;
    expect(rowsOf(spec, { issues: ROWS })).toHaveLength(2);
    expect(rowsOf(spec, { issues: "nope" })).toEqual([]);
    expect(
      rowsOf({ ...spec, items_path: undefined }, { issues: ROWS }),
    ).toEqual([]);
  });

  it("names a row by the first column that renders something", () => {
    expect(rowTitleFor(COLUMNS, ROWS[0], "ENG-4")).toBe("Composer drops focus");
    expect(rowTitleFor(COLUMNS, { id: "ENG-7" }, "ENG-7")).toBe("ENG-7");
  });
});

describe("buildConnectorRowEdits", () => {
  it("groups a batch by row and carries old, new, and the row as read", () => {
    const result = buildConnectorRowEdits(COLUMNS, ROWS, {
      [cellKey("ENG-4", "state")]: "In Progress",
      [cellKey("ENG-4", "assignee.login")]: "rlee",
      [cellKey("ENG-9", "title")]: "Badge contrast",
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.edits).toHaveLength(2);

    const first = result.edits[0];
    expect(first.row_key).toBe("ENG-4");
    expect(first.title).toBe("Composer drops focus");
    expect(first.row).toBe(ROWS[0]);
    expect(first.changes).toEqual([
      { field: "state", old: "Todo", new: "In Progress" },
      { field: "assignee.login", old: "jdoe", new: "rlee" },
    ]);
  });

  it("reports an empty batch rather than sending one", () => {
    expect(buildConnectorRowEdits(COLUMNS, ROWS, {})).toEqual({
      ok: false,
      reason: "empty",
    });
  });

  // Fail WHOLE. Dropping the unresolvable row would save the user's other edits
  // while silently discarding one still on their screen — the exact shape of a
  // save the user believes happened.
  it("fails the whole batch when a row key no longer resolves", () => {
    const result = buildConnectorRowEdits(COLUMNS, ROWS, {
      [cellKey("ENG-4", "state")]: "In Progress",
      [cellKey("ENG-404", "state")]: "Done",
    });
    expect(result).toEqual({ ok: false, reason: "unresolved" });
  });

  it("fails the whole batch when a column is no longer in the spec", () => {
    const result = buildConnectorRowEdits(COLUMNS, ROWS, {
      [cellKey("ENG-4", "priority")]: "High",
    });
    expect(result).toEqual({ ok: false, reason: "unresolved" });
  });

  it("fails when the row has no object form to prove a binding against", () => {
    const result = buildConnectorRowEdits(COLUMNS, ["scalar"], {
      [cellKey("row-0", "state")]: "Done",
    });
    expect(result).toEqual({ ok: false, reason: "unresolved" });
  });

  // A connector id and a spec path are both arbitrary text; a printable joiner
  // would let two different cells share one key, which is one row's typed value
  // landing on another row's column.
  it("keys cells unambiguously even when ids and paths contain separators", () => {
    expect(cellKey("AB C", "x")).not.toBe(cellKey("AB", "C x"));
  });
});
