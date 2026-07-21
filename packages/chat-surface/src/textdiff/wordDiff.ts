// Dependency-free word-level text diff (PRD-06). Produces the VSCode/Cursor-style
// red/green inline diff structure for *text-shaped* surfaces (email / prose
// bodies). The output is a flat, ordered list of `DiffHunk`s that a presentational
// component (`DiffText`) renders as <del>/<ins>/plain runs, and that PRD-09 will
// use for per-hunk accept via the stable `id`.
//
// Algorithm: tokenize both inputs on whitespace boundaries (keeping the
// separators so the diff is whitespace-preserving), trim the common prefix/suffix,
// then compute the shortest edit script of the remaining tokens with the greedy
// Myers O(ND) diff (trace + backtrack — Myers 1986). Adjacent same-kind token ops
// are coalesced into one hunk. Two budget guards keep the mount cheap: inputs
// longer than the char cap fall back to a single delete+insert pair, and a middle
// whose edit distance exceeds the edit cap falls back to delete-all+insert-all
// (bounds the O(D²) trace memory against adversarial, highly-divergent input).

export type DiffHunkKind = "equal" | "insert" | "delete";

/** One contiguous run of the diff. `id` is a deterministic, index-based handle
 * (stable across renders for the same input pair) that PRD-09 keys per-hunk
 * accept off of. `text` is the exact source substring (whitespace preserved). */
export interface DiffHunk {
  readonly id: string;
  readonly kind: DiffHunkKind;
  readonly text: string;
}

/** Hard cap (per input) beyond which `wordDiff` returns the 2-hunk fallback
 * instead of a full token diff — the render-budget guard (PRD-06 §Performance). */
export const WORD_DIFF_CHAR_CAP = 20_000;

/**
 * Word-level diff of `before` → `after`. Whitespace-preserving: e.g.
 * `"Hi Jordan," → "Hi Maya,"` yields `equal("Hi ")`, `delete("Jordan,")`,
 * `insert("Maya,")`. Identical inputs yield a single `equal` hunk; fully-different
 * inputs (no shared token) yield `delete` + `insert`. Never throws.
 */
export function wordDiff(before: string, after: string): DiffHunk[] {
  if (before.length > WORD_DIFF_CHAR_CAP || after.length > WORD_DIFF_CHAR_CAP) {
    return capFallback(before, after);
  }
  const a = tokenize(before);
  const b = tokenize(after);
  return coalesce(diffOps(a, b));
}

/**
 * Split a string into maximal runs of whitespace and non-whitespace, preserving
 * every character. `"Hi  world "` → `["Hi", "  ", "world", " "]`; `""` → `[]`.
 * Exported for tests (not part of the package barrel).
 */
export function tokenize(input: string): string[] {
  const tokens: string[] = [];
  const pattern = /\s+|\S+/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(input)) !== null) {
    tokens.push(match[0]);
  }
  return tokens;
}

interface Op {
  readonly kind: DiffHunkKind;
  readonly text: string;
}

/** The 2-hunk budget fallback: the whole `before` deleted, the whole `after`
 * inserted. Empty sides are omitted so an empty↔nonempty pair stays 1 hunk. */
function capFallback(before: string, after: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  if (before.length > 0) {
    hunks.push({ id: `h${hunks.length}`, kind: "delete", text: before });
  }
  if (after.length > 0) {
    hunks.push({ id: `h${hunks.length}`, kind: "insert", text: after });
  }
  return hunks;
}

/** Merge adjacent same-kind ops into hunks with stable, position-based ids. */
function coalesce(ops: readonly Op[]): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  for (const op of ops) {
    const last = hunks[hunks.length - 1];
    if (last && last.kind === op.kind) {
      hunks[hunks.length - 1] = { ...last, text: last.text + op.text };
    } else {
      hunks.push({ id: `h${hunks.length}`, kind: op.kind, text: op.text });
    }
  }
  return hunks;
}

// ---- Greedy Myers O(ND) diff over the token arrays --------------------------
//
// Follows Myers' 1986 "An O(ND) Difference Algorithm" (the greedy trace +
// backtrack, per James Coglan's derivation): after trimming the common prefix and
// suffix, `myersMiddle` walks the edit graph diagonal by diagonal, snapshotting
// the k-frontier per edit-distance `d`, then backtracks the recorded trace into an
// ordered op list. Beyond `WORD_DIFF_EDIT_CAP` the middle degrades to a coarse
// delete-all+insert-all so the O(D²) trace can't be driven unbounded.

/** Edit-distance beyond which a diff *segment* degrades to the coarse fallback —
 * the memory guard on the greedy trace. Far above any realistic prose edit, so it
 * never fires for real email/doc bodies; it only bites adversarial divergence. */
export const WORD_DIFF_EDIT_CAP = 4096;

function diffOps(a: readonly string[], b: readonly string[]): Op[] {
  const n = a.length;
  const m = b.length;
  let start = 0;
  while (start < n && start < m && a[start] === b[start]) {
    start += 1;
  }
  let endA = n;
  let endB = m;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const ops: Op[] = [];
  for (let i = 0; i < start; i++) {
    ops.push({ kind: "equal", text: a[i] });
  }
  myersMiddle(a, b, start, endA, start, endB, ops);
  for (let i = endA; i < n; i++) {
    ops.push({ kind: "equal", text: a[i] });
  }
  return ops;
}

/** Append the ops diffing `a[aLo, aHi)` against `b[bLo, bHi)` (no shared prefix or
 * suffix) to `ops`, in source order. */
function myersMiddle(
  a: readonly string[],
  b: readonly string[],
  aLo: number,
  aHi: number,
  bLo: number,
  bHi: number,
  ops: Op[],
): void {
  const n = aHi - aLo;
  const m = bHi - bLo;
  if (n === 0) {
    for (let j = bLo; j < bHi; j++) {
      ops.push({ kind: "insert", text: b[j] });
    }
    return;
  }
  if (m === 0) {
    for (let i = aLo; i < aHi; i++) {
      ops.push({ kind: "delete", text: a[i] });
    }
    return;
  }
  const max = n + m;
  const cap = Math.min(max, WORD_DIFF_EDIT_CAP);
  const offset = max;
  const v = new Int32Array(2 * max + 1);
  const trace: Int32Array[] = [];
  let found = -1;
  for (let d = 0; d <= cap; d++) {
    // Snapshot the live k-frontier before advancing this edit-distance. Copy the
    // whole [-d, d] band (both parities): the backtrack reads the parity-(d-1)
    // diagonals written by the previous iteration, not just this iteration's.
    const snap = new Int32Array(2 * d + 1);
    for (let k = -d; k <= d; k++) {
      snap[k + d] = v[offset + k];
    }
    trace.push(snap);
    let done = false;
    for (let k = -d; k <= d; k += 2) {
      let x: number;
      if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) {
        x = v[offset + k + 1];
      } else {
        x = v[offset + k - 1] + 1;
      }
      let y = x - k;
      while (x < n && y < m && a[aLo + x] === b[bLo + y]) {
        x += 1;
        y += 1;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) {
        found = d;
        done = true;
        break;
      }
    }
    if (done) {
      break;
    }
  }
  if (found < 0) {
    // Edit distance exceeded the cap — coarse fallback for this segment.
    for (let i = aLo; i < aHi; i++) {
      ops.push({ kind: "delete", text: a[i] });
    }
    for (let j = bLo; j < bHi; j++) {
      ops.push({ kind: "insert", text: b[j] });
    }
    return;
  }
  const reversed: Op[] = [];
  let x = n;
  let y = m;
  for (let d = found; d >= 0; d--) {
    const snap = trace[d];
    const k = x - y;
    let prevK: number;
    if (k === -d || (k !== d && snap[k - 1 + d] < snap[k + 1 + d])) {
      prevK = k + 1;
    } else {
      prevK = k - 1;
    }
    const prevX = snap[prevK + d];
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      reversed.push({ kind: "equal", text: a[aLo + x - 1] });
      x -= 1;
      y -= 1;
    }
    if (d > 0) {
      if (x === prevX) {
        reversed.push({ kind: "insert", text: b[bLo + prevY] });
      } else {
        reversed.push({ kind: "delete", text: a[aLo + prevX] });
      }
    }
    x = prevX;
    y = prevY;
  }
  for (let i = reversed.length - 1; i >= 0; i--) {
    ops.push(reversed[i]);
  }
}
