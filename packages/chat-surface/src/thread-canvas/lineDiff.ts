// A line-level diff, computed in the client from the arguments a file-editing
// tool already carries.
//
// WHY COMPUTED HERE — the runtime emits no structured diff. A real `edit_file`
// result is one prose sentence ("Successfully replaced 1 instance(s) of the
// string in '<path>'"), and the before/after text only exists as the call's own
// `old_string` / `new_string` arguments. So the diff is derived at render time
// from facts the card already holds; nothing new is requested from the backend.
//
// WHY NOT A DEPENDENCY — `diff@8.0.4` is present in the lockfile transitively,
// but this repository's lockfile does not survive regeneration (peer conflict +
// website hoisting mean no `npm install` reproduces its committed shape), so a
// new direct dependency edge is a CI hazard out of proportion to ~90 lines of
// LCS. If syntax highlighting is adopted later, that calculus changes and this
// module should be revisited alongside it.

/** One rendered row of a diff. */
export interface DiffLine {
  readonly kind: "context" | "add" | "remove";
  readonly text: string;
  /** 1-based line number on the before side; null on an addition. */
  readonly oldLine: number | null;
  /** 1-based line number on the after side; null on a removal. */
  readonly newLine: number | null;
}

/** A contiguous run of changed lines plus its surrounding context. */
export interface DiffHunk {
  readonly oldStart: number;
  readonly newStart: number;
  readonly lines: readonly DiffLine[];
}

export interface FileDiff {
  readonly hunks: readonly DiffHunk[];
  readonly additions: number;
  readonly deletions: number;
  /**
   * The inputs were too large to diff exactly, so the result is a whole-block
   * replacement rather than a minimal edit script. Surfaced so the card can say
   * so instead of implying a precise diff.
   */
  readonly approximate: boolean;
}

export interface LineDiffOptions {
  /** Context lines kept either side of a change. Standard unified default. */
  readonly context?: number;
  /**
   * Above this many lines on either side the exact O(n·m) table is skipped and
   * the whole block is reported as replaced. Guards the transcript against a
   * `write_file` of a large file pinning the main thread — the same reason the
   * transcript is the wrong place to render an entire source file.
   */
  readonly maxLines?: number;
}

const DEFAULT_CONTEXT = 3;
const DEFAULT_MAX_LINES = 1500;

/**
 * Split into lines for diffing.
 *
 * A trailing newline would otherwise produce a phantom empty final line that
 * renders as a spurious changed row, so it is dropped — the common case where
 * `old_string` ends with "\n" and `new_string` does not is a content change,
 * not an extra line.
 */
function toLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * Longest common subsequence over lines, as a backtrack-able table.
 *
 * Plain dynamic programming rather than Myers: the inputs this renders are a
 * string replacement or a single written file, both well under `maxLines`, and
 * a table that can be read straight back is easier to keep correct than a
 * middle-snake implementation nobody will revisit.
 */
function lcsOps(
  before: readonly string[],
  after: readonly string[],
): DiffLine[] {
  const n = before.length;
  const m = after.length;
  const table: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i][j] =
        before[i] === after[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) {
      out.push({
        kind: "context",
        text: before[i],
        oldLine: i + 1,
        newLine: j + 1,
      });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({
        kind: "remove",
        text: before[i],
        oldLine: i + 1,
        newLine: null,
      });
      i += 1;
    } else {
      out.push({ kind: "add", text: after[j], oldLine: null, newLine: j + 1 });
      j += 1;
    }
  }
  while (i < n) {
    out.push({
      kind: "remove",
      text: before[i],
      oldLine: i + 1,
      newLine: null,
    });
    i += 1;
  }
  while (j < m) {
    out.push({ kind: "add", text: after[j], oldLine: null, newLine: j + 1 });
    j += 1;
  }
  return out;
}

/** Every line replaced — the fallback when the inputs are too large to diff. */
function wholeBlockOps(
  before: readonly string[],
  after: readonly string[],
): DiffLine[] {
  return [
    ...before.map(
      (text, index): DiffLine => ({
        kind: "remove",
        text,
        oldLine: index + 1,
        newLine: null,
      }),
    ),
    ...after.map(
      (text, index): DiffLine => ({
        kind: "add",
        text,
        oldLine: null,
        newLine: index + 1,
      }),
    ),
  ];
}

/** Group the edit script into hunks, keeping `context` lines around changes. */
function toHunks(ops: readonly DiffLine[], context: number): DiffHunk[] {
  const changed = ops
    .map((line, index) => (line.kind === "context" ? -1 : index))
    .filter((index) => index >= 0);
  if (changed.length === 0) return [];

  const ranges: Array<[number, number]> = [];
  for (const index of changed) {
    const start = Math.max(0, index - context);
    const end = Math.min(ops.length - 1, index + context);
    const last = ranges[ranges.length - 1];
    // Merge when the windows touch or overlap, so two nearby edits read as one
    // hunk instead of repeating their shared context.
    if (last !== undefined && start <= last[1] + 1)
      last[1] = Math.max(last[1], end);
    else ranges.push([start, end]);
  }

  return ranges.map(([start, end]) => {
    const lines = ops.slice(start, end + 1);
    const firstOld = lines.find((line) => line.oldLine !== null)?.oldLine ?? 1;
    const firstNew = lines.find((line) => line.newLine !== null)?.newLine ?? 1;
    return { oldStart: firstOld, newStart: firstNew, lines };
  });
}

/**
 * Diff `before` against `after`, line by line.
 *
 * Returns zero hunks when the two are identical, which callers should treat as
 * "nothing to show" rather than as an empty diff view.
 */
export function computeLineDiff(
  before: string,
  after: string,
  options: LineDiffOptions = {},
): FileDiff {
  const context = options.context ?? DEFAULT_CONTEXT;
  const maxLines = options.maxLines ?? DEFAULT_MAX_LINES;

  const beforeLines = toLines(before);
  const afterLines = toLines(after);

  const approximate =
    beforeLines.length > maxLines || afterLines.length > maxLines;
  const ops = approximate
    ? wholeBlockOps(beforeLines, afterLines)
    : lcsOps(beforeLines, afterLines);

  let additions = 0;
  let deletions = 0;
  for (const line of ops) {
    if (line.kind === "add") additions += 1;
    else if (line.kind === "remove") deletions += 1;
  }

  return { hunks: toHunks(ops, context), additions, deletions, approximate };
}
