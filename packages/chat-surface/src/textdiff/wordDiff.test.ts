import { describe, expect, it } from "vitest";

import {
  WORD_DIFF_CHAR_CAP,
  tokenize,
  wordDiff,
  type DiffHunk,
} from "./wordDiff";

// Compact view of a hunk for golden assertions.
function shape(hunks: readonly DiffHunk[]): Array<[DiffHunk["kind"], string]> {
  return hunks.map((h) => [h.kind, h.text]);
}

function reconstructBefore(hunks: readonly DiffHunk[]): string {
  return hunks
    .filter((h) => h.kind !== "insert")
    .map((h) => h.text)
    .join("");
}

function reconstructAfter(hunks: readonly DiffHunk[]): string {
  return hunks
    .filter((h) => h.kind !== "delete")
    .map((h) => h.text)
    .join("");
}

describe("wordDiff — golden fixtures (AC1)", () => {
  it("single word swap: keeps the shared prefix, swaps the last token", () => {
    expect(shape(wordDiff("Hi Jordan,", "Hi Maya,"))).toEqual([
      ["equal", "Hi "],
      ["delete", "Jordan,"],
      ["insert", "Maya,"],
    ]);
  });

  it("sentence insert: one insert hunk between two equal runs", () => {
    expect(
      shape(wordDiff("Thanks. Bye.", "Thanks. See you soon. Bye.")),
    ).toEqual([
      ["equal", "Thanks. "],
      ["insert", "See you soon. "],
      ["equal", "Bye."],
    ]);
  });

  it("paragraph delete: trailing paragraph removed as one delete hunk", () => {
    expect(
      shape(wordDiff("Keep this line.\n\nDrop that line.", "Keep this line.")),
    ).toEqual([
      ["equal", "Keep this line."],
      ["delete", "\n\nDrop that line."],
    ]);
  });

  it("trailing-whitespace-only change ⇒ equal-dominant", () => {
    const hunks = wordDiff("Hello world", "Hello world  ");
    expect(shape(hunks)).toEqual([
      ["equal", "Hello world"],
      ["insert", "  "],
    ]);
    // Equal run dominates the changed run.
    const equalLen = hunks
      .filter((h) => h.kind === "equal")
      .reduce((n, h) => n + h.text.length, 0);
    const changedLen = hunks
      .filter((h) => h.kind !== "equal")
      .reduce((n, h) => n + h.text.length, 0);
    expect(equalLen).toBeGreaterThan(changedLen);
  });

  it("fully-different ⇒ a single delete + insert pair", () => {
    expect(shape(wordDiff("alpha", "omega"))).toEqual([
      ["delete", "alpha"],
      ["insert", "omega"],
    ]);
  });

  it("identical ⇒ a single equal hunk", () => {
    expect(shape(wordDiff("Same content here.", "Same content here."))).toEqual(
      [["equal", "Same content here."]],
    );
  });
});

describe("wordDiff — whitespace preservation", () => {
  it("preserves internal multi-space and newline runs verbatim", () => {
    const before = "one  two\n\nthree";
    const after = "one  two\n\nthree four";
    const hunks = wordDiff(before, after);
    expect(reconstructBefore(hunks)).toBe(before);
    expect(reconstructAfter(hunks)).toBe(after);
  });

  it("tokenize covers every character (whitespace + non-whitespace runs)", () => {
    expect(tokenize("Hi  world \n!")).toEqual([
      "Hi",
      "  ",
      "world",
      " \n",
      "!",
    ]);
    expect(tokenize("")).toEqual([]);
    expect(tokenize("solid").join("")).toBe("solid");
  });
});

describe("wordDiff — stable, index-based ids", () => {
  it("assigns sequential h-prefixed ids in output order", () => {
    const hunks = wordDiff("Hi Jordan,", "Hi Maya,");
    expect(hunks.map((h) => h.id)).toEqual(["h0", "h1", "h2"]);
  });

  it("is deterministic — same pair yields byte-identical hunks across calls", () => {
    const a = wordDiff("the quick brown fox", "the slow brown fox jumps");
    const b = wordDiff("the quick brown fox", "the slow brown fox jumps");
    expect(a).toEqual(b);
  });
});

describe("wordDiff — cap fallback (AC3)", () => {
  it("returns exactly the 2-hunk fallback for 25k-char inputs", () => {
    const before = "a".repeat(25_000);
    const after = "b".repeat(25_000);
    const hunks = wordDiff(before, after);
    expect(hunks).toHaveLength(2);
    expect(shape(hunks)).toEqual([
      ["delete", before],
      ["insert", after],
    ]);
  });

  it("still runs the real token diff at exactly the cap length", () => {
    const before = "x ".repeat(WORD_DIFF_CHAR_CAP / 2).trimEnd(); // ≤ cap
    expect(before.length).toBeLessThanOrEqual(WORD_DIFF_CHAR_CAP);
    const hunks = wordDiff(before, before);
    // Identical, under cap ⇒ single equal hunk, not the delete+insert fallback.
    expect(hunks).toHaveLength(1);
    expect(hunks[0].kind).toBe("equal");
  });

  it("crosses to the fallback one character over the cap", () => {
    const before = "z".repeat(WORD_DIFF_CHAR_CAP + 1);
    const hunks = wordDiff(before, "");
    // after is empty ⇒ only the delete side of the pair.
    expect(shape(hunks)).toEqual([["delete", before]]);
  });
});

// ---- Property + minimality oracle (AC2) ------------------------------------

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const WORDS = ["the", "quick", "brown", "fox", "lazy", "dog", "runs", "fast"];

function randomText(rand: () => number): string {
  const count = Math.floor(rand() * 12);
  const parts: string[] = [];
  for (let i = 0; i < count; i++) {
    parts.push(WORDS[Math.floor(rand() * WORDS.length)]);
  }
  return parts.join(" ");
}

/** Independent LCS length of two token arrays (DP oracle). */
function lcsLength(a: readonly string[], b: readonly string[]): number {
  const m = a.length;
  const n = b.length;
  const dp = new Int32Array((m + 1) * (n + 1));
  const w = n + 1;
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i * w + j] =
        a[i] === b[j]
          ? dp[(i + 1) * w + (j + 1)] + 1
          : Math.max(dp[(i + 1) * w + j], dp[i * w + (j + 1)]);
    }
  }
  return dp[0];
}

describe("wordDiff — property + minimality (AC2)", () => {
  it("reconstructs both sides for random token sequences", () => {
    const rand = mulberry32(0xc0ffee);
    for (let i = 0; i < 500; i++) {
      const before = randomText(rand);
      const after = randomText(rand);
      const hunks = wordDiff(before, after);
      expect(reconstructBefore(hunks)).toBe(before);
      expect(reconstructAfter(hunks)).toBe(after);
    }
  });

  it("produces a minimal script — equal tokens equal the LCS length", () => {
    const rand = mulberry32(0x1234abcd);
    for (let i = 0; i < 300; i++) {
      const before = randomText(rand);
      const after = randomText(rand);
      const hunks = wordDiff(before, after);
      const equalTokens = hunks
        .filter((h) => h.kind === "equal")
        .reduce((n, h) => n + tokenize(h.text).length, 0);
      expect(equalTokens).toBe(lcsLength(tokenize(before), tokenize(after)));
    }
  });

  it("never emits an empty or zero-length hunk", () => {
    const rand = mulberry32(0x99);
    for (let i = 0; i < 200; i++) {
      const hunks = wordDiff(randomText(rand), randomText(rand));
      for (const h of hunks) {
        expect(h.text.length).toBeGreaterThan(0);
      }
      // No two adjacent hunks share a kind (fully coalesced).
      for (let k = 1; k < hunks.length; k++) {
        expect(hunks[k].kind).not.toBe(hunks[k - 1].kind);
      }
    }
  });
});

describe("wordDiff — performance (AC §Performance)", () => {
  it("diffs a 5k-word body with a small edit well under the 50ms CI bound", () => {
    const words: string[] = [];
    for (let i = 0; i < 5000; i++) {
      words.push(`word${i % 97}`);
    }
    const before = words.join(" ");
    const after = `${before} and one more clause`;
    const start = performance.now();
    const hunks = wordDiff(before, after);
    const elapsed = performance.now() - start;
    expect(reconstructBefore(hunks)).toBe(before);
    expect(reconstructAfter(hunks)).toBe(after);
    expect(elapsed).toBeLessThan(50);
  });
});
