/* design-parity · TOKEN CONTRACT gate (vitest, no DOM render)
 * =========================================================================
 * The design system claimed a stylelint `strict-value` gate that does not
 * exist anywhere in this repo. This file is the gate that does: it PARSES
 * `packages/design-system/src/styles.css` — the declared single token source of
 * truth — and pins the numbers and the structural rules that the surface
 * harnesses can only observe indirectly, three orders of magnitude faster than
 * booting a browser.
 *
 * It lives in `lib/` and is named `render-live-tokens.test.tsx` so it matches
 * the config's existing `include: ["lib/render-live*.test.tsx"]` glob — that
 * file is a merge point between parallel per-surface parity runs, so a new gate
 * must not edit it. (It renders nothing; the name buys the glob.)
 *
 * Run:
 *   node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
 *     lib/render-live-tokens.test.tsx
 * ========================================================================= */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// NOTE the indirection through a variable: Vite statically rewrites a LITERAL
// `new URL("…", import.meta.url)` into a served asset URL (http://localhost/@fs/…),
// which `fileURLToPath` then rejects. Passing the path as a variable — exactly
// what the sibling render-live-* harnesses do — keeps it a real file: URL.
const HERE = (p: string): string => fileURLToPath(new URL(p, import.meta.url));
const STYLES = "../../../packages/design-system/src/styles.css";

let cached: string | undefined;
/** The shipping token source of truth, read once. */
function css(): string {
  cached ??= readFileSync(HERE(STYLES), "utf8");
  return cached;
}

let strippedCache: string | undefined;
/** The same sheet with /* … *\/ comments blanked out (newlines preserved, so
 *  offsets and line structure survive). Structural assertions run against this:
 *  this file's comments deliberately quote CSS — including braces — so a naive
 *  brace scan over the raw text mis-reads every rule that documents itself. */
function code(): string {
  strippedCache ??= css().replace(/\/\*[\s\S]*?\*\//g, (m) =>
    m.replace(/[^\n]/g, " "),
  );
  return strippedCache;
}

/** All declared values of a custom property, in source order. */
function declarations(name: string): readonly string[] {
  const re = new RegExp(`^\\s*${name}\\s*:\\s*([^;]+);`, "gm");
  return [...code().matchAll(re)].map((m) => m[1].trim());
}

/** The single declared value of a custom property (fails if 0 or >1). */
function soleDeclaration(name: string): string {
  const all = declarations(name);
  expect(all, `${name} must be declared exactly once`).toHaveLength(1);
  return all[0];
}

/** rem token -> px, against the UA 16px rem anchor this file deliberately keeps. */
function remToPx(value: string): number {
  const m = /^([\d.]+)rem$/.exec(value);
  expect(m, `expected a rem value, got "${value}"`).not.toBeNull();
  return Number.parseFloat((m as RegExpExecArray)[1]) * 16;
}

/** The body of the first rule whose selector list matches `selector`. */
function ruleBody(selector: string): string {
  const idx = code().indexOf(selector);
  expect(idx, `selector "${selector}" not found in styles.css`).toBeGreaterThan(
    -1,
  );
  const open = code().indexOf("{", idx);
  const close = code().indexOf("}", open);
  return code().slice(open + 1, close);
}

// ---------------------------------------------------------------------------
// DoD 1 — the base body size is the design's literal 13px.
// ---------------------------------------------------------------------------
describe("base type size (DoD 1)", () => {
  it("declares --font-size-sm: 0.8125rem, which is exactly 13px", () => {
    const value = soleDeclaration("--font-size-sm");
    expect(value).toBe("0.8125rem");
    expect(0.8125 * 16).toBe(13);
    expect(remToPx(value)).toBe(13);
  });

  it("keeps body on the token rather than a literal, and the rem anchor at 16px", () => {
    // The fix is a token retune, NOT `:root{font-size:13px}` — that would scale
    // every rem token in the product (the whole size ladder, --space-*,
    // --radius-*) by 0.8125x.
    expect(ruleBody("body {")).toContain("font-size: var(--font-size-sm);");
    expect(/^\s*(html|:root)\s*\{[^}]*font-size:\s*13px/ms.test(code())).toBe(
      false,
    );
  });
});

// ---------------------------------------------------------------------------
// DoD 2 — the mono micro-ladder, and the sans rungs it does NOT disturb.
// ---------------------------------------------------------------------------
describe("mono micro-ladder (DoD 2)", () => {
  const cases: ReadonlyArray<readonly [string, string, number]> = [
    ["--font-size-mono-8-5", "0.53125rem", 8.5],
    ["--font-size-mono-9-5", "0.59375rem", 9.5],
    ["--font-size-mono-10", "0.625rem", 10],
    ["--font-size-mono-10-5", "0.65625rem", 10.5],
  ];

  for (const [token, rem, px] of cases) {
    it(`declares ${token}: ${rem} (${px}px)`, () => {
      const value = soleDeclaration(token);
      expect(value).toBe(rem);
      expect(remToPx(value)).toBeCloseTo(px, 10);
    });
  }

  it("leaves the neighbouring sans rungs untouched", () => {
    expect(soleDeclaration("--font-size-3xs")).toBe("0.5625rem"); // 9px
    expect(remToPx(soleDeclaration("--font-size-3xs"))).toBe(9);
    expect(soleDeclaration("--font-size-2xs")).toBe("0.7rem"); // 11.2px
    expect(remToPx(soleDeclaration("--font-size-2xs"))).toBeCloseTo(11.2, 10);
  });

  it("points the two mono recipes at the ladder, not at a sans rung", () => {
    // `.ui-mono-caps` is the design's `.sect-h` role; `.ui-badge` is `.chip`.
    expect(ruleBody(".ui-mono-caps {")).toContain(
      "font-size: var(--font-size-mono-9-5);",
    );
    expect(ruleBody(".ui-badge {")).toContain(
      "font-size: var(--font-size-mono-10-5);",
    );
    // The design's `.chip` sets no weight, so it inherits 500.
    expect(ruleBody(".ui-badge {")).toContain(
      "font-weight: var(--font-weight-medium);",
    );
  });
});

// ---------------------------------------------------------------------------
// DoD 3 — the scrim is one token, and it is ground-independent.
// ---------------------------------------------------------------------------
describe("scrim tokens (DoD 3)", () => {
  it("declares --color-scrim and --blur-scrim with the design's values", () => {
    expect(soleDeclaration("--color-scrim")).toBe("rgba(4, 4, 6, 0.66)");
    expect(soleDeclaration("--blur-scrim")).toBe("2px");
  });

  it("never theme-scopes the scrim (a scrim darkens what is BEHIND it)", () => {
    const themeBlocks = [
      ...code().matchAll(/^:root\[data-theme[^{]*\{([\s\S]*?)^\}/gm),
    ].map((m) => m[1]);
    expect(themeBlocks.length).toBeGreaterThanOrEqual(2);
    for (const block of themeBlocks) {
      expect(block).not.toContain("color-scrim");
      expect(block).not.toContain("blur-scrim");
    }
  });
});

// ---------------------------------------------------------------------------
// DoD 4 — one writer per accent variable. THE regression guard for the bug.
// ---------------------------------------------------------------------------
describe("accent cascade: seed tier vs derived tier (DoD 4)", () => {
  /** Split the sheet into top-level rules: [selectorList, body]. */
  function topLevelRules(): ReadonlyArray<readonly [string, string]> {
    const out: Array<readonly [string, string]> = [];
    let i = 0;
    while (i < code().length) {
      const open = code().indexOf("{", i);
      if (open === -1) break;
      let depth = 1;
      let j = open + 1;
      while (j < code().length && depth > 0) {
        if (code()[j] === "{") depth += 1;
        else if (code()[j] === "}") depth -= 1;
        j += 1;
      }
      out.push([code().slice(i, open).trim(), code().slice(open + 1, j - 1)]);
      i = j;
    }
    return out;
  }

  const PUBLIC_ACCENT = /^\s*--color-accent(-strong|-contrast)?\s*:/m;

  it("no [data-accent] block writes the public --color-accent* tier", () => {
    const offenders = topLevelRules()
      .filter(([sel]) => sel.includes("[data-accent="))
      .filter(([, body]) => PUBLIC_ACCENT.test(body))
      .map(([sel]) => sel);
    expect(offenders).toEqual([]);
  });

  it("every --color-accent* writer is a [data-theme] block", () => {
    const writers = topLevelRules().filter(([, body]) =>
      PUBLIC_ACCENT.test(body),
    );
    expect(writers.length).toBe(3); // dark (+ bare :root), light, slate
    for (const [sel] of writers) {
      expect(sel).toMatch(/\[data-theme=/);
    }
  });

  it("every accent swatch declares the full private seed triple", () => {
    const swatches = topLevelRules().filter(([sel]) =>
      sel.includes("[data-accent="),
    );
    expect(swatches.length).toBe(9);
    for (const [sel, body] of swatches) {
      for (const seed of [
        "--accent-seed:",
        "--accent-seed-strong:",
        "--accent-seed-ink:",
      ]) {
        expect(body, `${sel} must declare ${seed}`).toContain(seed);
      }
    }
  });

  it("the dark and slate derivations are the identity on the seed", () => {
    for (const sel of [
      ':root[data-theme="dark"] {',
      ':root[data-theme="slate"] {',
    ]) {
      const body = ruleBody(sel);
      expect(body).toContain("--color-accent: var(--accent-seed);");
      expect(body).toContain(
        "--color-accent-strong: var(--accent-seed-strong);",
      );
      expect(body).toContain(
        "--color-accent-contrast: var(--accent-seed-ink);",
      );
    }
  });

  it("the light derivation darkens the seed and flips the ink near-white", () => {
    const body = ruleBody(':root[data-theme="light"] {');
    expect(body).toMatch(
      /--color-accent:\s*color-mix\(in oklab, var\(--accent-seed\) \d+%, #0a0a0e\);/,
    );
    expect(body).toMatch(
      /--color-accent-strong:\s*color-mix\(in oklab, var\(--accent-seed\) \d+%, #0a0a0e\);/,
    );
    // copilot.css:82 — the design's light block's ONLY accent write.
    expect(body).toContain("--color-accent-contrast: #f4faff;");
  });
});

// ---------------------------------------------------------------------------
// DoD 16 — tone beats size tier on .ui-button.
// ---------------------------------------------------------------------------
describe("button weight precedence (DoD 16)", () => {
  it(".ui-button--primary re-asserts semibold, after the size tier", () => {
    const body = ruleBody(".ui-button--primary,\n.ui-link-button {");
    expect(body).toContain("font-weight: var(--font-weight-semibold);");
    expect(soleDeclaration("--font-weight-semibold")).toBe("600");
    // Source order matters at equal specificity: --primary must come AFTER --sm.
    expect(code().indexOf(".ui-button--primary,")).toBeGreaterThan(
      code().indexOf(".ui-button--sm {"),
    );
  });
});

// ---------------------------------------------------------------------------
// DoD 17 — --color-bg is deliberately the design's --ink, not the mock's stage.
// ---------------------------------------------------------------------------
describe("app ground (DoD 17)", () => {
  it("pins --color-bg to #09090b with the reasoning in a preceding comment", () => {
    const idx = css().indexOf("--color-bg: #09090b;");
    expect(idx).toBeGreaterThan(-1);
    expect(css().indexOf("--color-bg: #09090b;", idx + 1)).toBe(-1);
    // The comment immediately above must cite the design line, so the next
    // reader does not "fix" this to the mock's stage colour #050506.
    const preceding = css().slice(Math.max(0, idx - 900), idx);
    expect(preceding).toContain("copilot.css:8");
    expect(preceding).toContain("#050506");
  });
});

// ---------------------------------------------------------------------------
// DoD 18 — the section-head block rhythm.
// ---------------------------------------------------------------------------
describe("section-head block rhythm (DoD 18)", () => {
  it("declares .ui-section-head with the design's 22px/10px margins", () => {
    // copilot.css:1569-1573 — `.sect-h{margin:22px 0 10px}`.
    const body = ruleBody(".ui-section-head {");
    expect(body).toContain("margin: 22px 0 10px;");
    expect(body).toContain("display: flex;");
    expect(body).toContain("align-items: center;");
    expect(body).toContain("gap: var(--space-sm);");
  });

  it("zeroes the top margin on the first section head", () => {
    // copilot.css:1571-1573 — `.sect-h:first-child{margin-top:0}`. This is the
    // reason it is a class at all: a selector-dependent margin cannot be
    // expressed as an inline style.
    expect(ruleBody(".ui-section-head:first-child {")).toContain(
      "margin-top: 0;",
    );
  });
});
