import { describe, expect, it } from "vitest";

import { computeLineDiff } from "./lineDiff";

const CSV_BEFORE = [
  "id,color,animal,score,is_valid",
  "1,blue,otter,42,true",
  "2,green,falcon,87,false",
  "3,red,badger,15,true",
  "4,purple,heron,63,true",
  "5,orange,lynx,29,false",
].join("\n");

describe("computeLineDiff", () => {
  it("reports no hunks when the sides are identical", () => {
    const diff = computeLineDiff(CSV_BEFORE, CSV_BEFORE);
    expect(diff.hunks).toHaveLength(0);
    expect(diff.additions).toBe(0);
    expect(diff.deletions).toBe(0);
  });

  it("diffs the real edit_file replacement to one changed line", () => {
    // The exact strings the captured run passed as old_string / new_string.
    const diff = computeLineDiff(
      "5,orange,lynx,29,false",
      "5,orange,lynx,30,false",
    );
    expect(diff.additions).toBe(1);
    expect(diff.deletions).toBe(1);
    expect(diff.hunks).toHaveLength(1);
    expect(diff.hunks[0].lines.map((l) => l.kind)).toEqual(["remove", "add"]);
  });

  it("keeps a changed line in context and numbers both sides", () => {
    const after = CSV_BEFORE.replace(
      "3,red,badger,15,true",
      "3,red,badger,16,true",
    );
    const diff = computeLineDiff(CSV_BEFORE, after, { context: 1 });
    expect(diff.hunks).toHaveLength(1);
    const hunk = diff.hunks[0];
    expect(hunk.lines.map((l) => l.kind)).toEqual([
      "context",
      "remove",
      "add",
      "context",
    ]);
    // Context rows carry both numbers; changed rows carry only their own side.
    expect(hunk.lines[0]).toMatchObject({ oldLine: 3, newLine: 3 });
    expect(hunk.lines[1]).toMatchObject({ oldLine: 4, newLine: null });
    expect(hunk.lines[2]).toMatchObject({ oldLine: null, newLine: 4 });
  });

  it("treats a pure insertion as additions only", () => {
    const diff = computeLineDiff("a\nb", "a\nnew\nb");
    expect(diff.additions).toBe(1);
    expect(diff.deletions).toBe(0);
  });

  it("treats writing a new file as all additions", () => {
    const diff = computeLineDiff("", "line one\nline two");
    expect(diff.additions).toBe(2);
    expect(diff.deletions).toBe(0);
  });

  it("merges two nearby edits into a single hunk", () => {
    const before = ["a", "b", "c", "d", "e"].join("\n");
    const after = ["a", "B", "c", "D", "e"].join("\n");
    expect(computeLineDiff(before, after, { context: 1 }).hunks).toHaveLength(
      1,
    );
  });

  it("splits two distant edits into separate hunks", () => {
    const before = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
    const after = before
      .replace("line 1\n", "CHANGED 1\n")
      .replace("line 25", "CHANGED 25");
    expect(computeLineDiff(before, after, { context: 2 }).hunks).toHaveLength(
      2,
    );
  });

  it("drops a trailing newline rather than showing a phantom empty line", () => {
    const diff = computeLineDiff("a\n", "a");
    expect(diff.hunks).toHaveLength(0);
  });

  it("falls back to a whole-block replacement past maxLines, and says so", () => {
    const big = Array.from({ length: 40 }, (_, i) => `l${i}`).join("\n");
    const diff = computeLineDiff(big, `${big}\nextra`, { maxLines: 10 });
    expect(diff.approximate).toBe(true);
    expect(diff.deletions).toBe(40);
    expect(diff.additions).toBe(41);
  });

  it("stays exact below maxLines", () => {
    const big = Array.from({ length: 40 }, (_, i) => `l${i}`).join("\n");
    const diff = computeLineDiff(big, `${big}\nextra`, { maxLines: 100 });
    expect(diff.approximate).toBe(false);
    expect(diff.additions).toBe(1);
    expect(diff.deletions).toBe(0);
  });
});
