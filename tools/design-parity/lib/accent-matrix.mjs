#!/usr/bin/env node
/* ACCENT x THEME MATRIX GATE
 * ===========================================================================
 * The accent picker offers nine swatches. Before the seed/derived token split,
 * `[data-accent="…"]` and `[data-theme="…"]` both wrote `--color-accent` at
 * identical specificity — so source order decided the winner, the theme blocks
 * came later, and nine swatches collapsed to ONE colour in light and ONE in
 * slate. The stored preference was honoured all the way to `data-accent` on
 * <html>; the stylesheet threw it away.
 *
 * That bug was invisible to every test in the repo because it only exists at
 * CASCADE RESOLUTION time. So this gate resolves it for real: it loads the
 * shipping `packages/design-system/src/styles.css` into Chromium, stamps all
 * 9 accents x 3 themes on <html>, and reads the COMPUTED colour of probe
 * elements (not the custom property text — an unregistered custom property
 * reports its unevaluated `color-mix(…)` source, which would prove nothing).
 *
 * It asserts, for all 27 cells:
 *   (a) COLLAPSE      — the 9 accents resolve to 9 DISTINCT colours within each
 *                       theme. (27 distinct overall is NOT required: dark and
 *                       slate are both dark grounds and legitimately coincide.)
 *   (b) UI CONTRAST   — contrast(--color-accent, --color-bg) >= 3.0
 *                       (WCAG 1.4.11, non-text UI components.)
 *   (c) TEXT CONTRAST — contrast(--color-accent-contrast, --color-accent) >= 4.5
 *                       (WCAG 1.4.3, text drawn ON the accent fill.)
 *   (d) DRIFT         — the resolved matrix still matches the checked-in
 *                       out/accent-matrix.expected.json.
 *
 * This is a token-tier concern, not a rail concern, which is why it lives in
 * lib/ and generalises surfaces/rail-badge/probe4-accent-theme.mjs (whose
 * chromium resolver it reuses verbatim).
 *
 * Usage:
 *   node tools/design-parity/lib/accent-matrix.mjs --check   # gate; exit 0/1
 *   node tools/design-parity/lib/accent-matrix.mjs --write   # regenerate the
 *                                                            # expected matrix
 *   node tools/design-parity/lib/accent-matrix.mjs           # print the table
 * No HTTP server needed — the stylesheet is inlined into an about:blank page.
 * ======================================================================== */
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const STYLES = join(HERE, "../../../packages/design-system/src/styles.css");
const EXPECTED = join(HERE, "../out/accent-matrix.expected.json");

// Kept byte-identical to surfaces/rail-badge/probe4-accent-theme.mjs — the same
// machine, the same browser, the same answer.
function resolveChromiumExecutable() {
  if (process.env.PARITY_CHROMIUM) return process.env.PARITY_CHROMIUM;
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const rev = (d) => Number.parseInt(d.split("-").pop() ?? "0", 10) || 0;
  const cands = readdirSync(cache)
    .filter(
      (d) =>
        d.startsWith("chromium_headless_shell-") || d.startsWith("chromium-"),
    )
    .sort((a, b) => rev(b) - rev(a) || (a.includes("headless") ? -1 : 1));
  for (const c of cands) {
    for (const rel of [
      "chrome-mac/headless_shell",
      "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
      "chrome-linux/headless_shell",
      "chrome-linux/chrome",
    ]) {
      const p = join(cache, c, rel);
      if (existsSync(p)) return p;
    }
  }
  return undefined;
}

// The nine swatches ACCENT_SCHEMES ships (packages/design-system/src/index.tsx)
// and the three grounds the appearance write path can stamp.
const ACCENTS = [
  "sky",
  "atlas-orange",
  "gold",
  "amber",
  "red",
  "lime",
  "teal",
  "blue",
  "violet",
];
const THEMES = ["dark", "light", "slate"];

const MIN_UI_CONTRAST = 3.0; // WCAG 1.4.11 — non-text UI component
const MIN_TEXT_CONTRAST = 4.5; // WCAG 1.4.3 — text on the accent fill

// ---------------------------------------------------------------------------
// Colour maths (pure node — the browser gives us rgb(), we do the rest here so
// the thresholds are reviewable in one place).
// ---------------------------------------------------------------------------
function parseRgb(value) {
  const m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(value);
  if (m === null) throw new Error(`cannot parse colour: ${value}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const la = relativeLuminance(parseRgb(a));
  const lb = relativeLuminance(parseRgb(b));
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
}

// ---------------------------------------------------------------------------
// Resolve the matrix in a real browser.
// ---------------------------------------------------------------------------
async function resolveMatrix() {
  const css = readFileSync(STYLES, "utf8");
  const browser = await chromium.launch({
    executablePath: resolveChromiumExecutable(),
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 400, height: 300 },
    });
    // NOTE: probe ELEMENTS, not `getPropertyValue("--color-accent")`. An
    // unregistered custom property computes to its substituted TEXT, so the
    // light theme would report the literal `color-mix(in oklab, #5fb2ec 62%,
    // #0a0a0e)` and prove nothing. Painting it onto `color` forces the engine
    // to actually evaluate it.
    await page.setContent(
      `<style>${css}</style>
       <div id="accent" style="color:var(--color-accent)"></div>
       <div id="accent-strong" style="color:var(--color-accent-strong)"></div>
       <div id="accent-contrast" style="color:var(--color-accent-contrast)"></div>
       <div id="bg" style="color:var(--color-bg)"></div>`,
      { waitUntil: "load" },
    );

    return await page.evaluate(
      ({ themes, accents }) => {
        const root = document.documentElement;
        // `color-mix(in oklab, …)` computes to an `oklab(…)` colour, so read it
        // back through a 1x1 canvas: that is the engine's own conversion to the
        // sRGB the user actually sees, rather than a re-implementation of the
        // oklab->sRGB transform in this script.
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const toSrgb = (value) => {
          ctx.clearRect(0, 0, 1, 1);
          ctx.fillStyle = "#000000";
          ctx.fillStyle = value;
          if (
            ctx.fillStyle === "#000000" &&
            !/^(#000000|rgb\(0, 0, 0\))$/.test(value)
          ) {
            throw new Error(`canvas rejected colour: ${value}`);
          }
          ctx.fillRect(0, 0, 1, 1);
          const d = ctx.getImageData(0, 0, 1, 1).data;
          return `rgb(${d[0]}, ${d[1]}, ${d[2]})`;
        };
        const read = (id) =>
          toSrgb(getComputedStyle(document.getElementById(id)).color);
        const out = [];
        for (const theme of themes) {
          for (const accent of accents) {
            root.setAttribute("data-theme", theme);
            root.setAttribute("data-accent", accent);
            out.push({
              theme,
              accent,
              colorAccent: read("accent"),
              colorAccentStrong: read("accent-strong"),
              colorAccentContrast: read("accent-contrast"),
              colorBg: read("bg"),
            });
          }
        }
        return out;
      },
      { themes: THEMES, accents: ACCENTS },
    );
  } finally {
    await browser.close();
  }
}

/** Decorate raw cells with the two contrast ratios the gate asserts. */
function withRatios(cells) {
  return cells.map((c) => ({
    ...c,
    contrastAccentOnBg: contrast(c.colorAccent, c.colorBg),
    contrastInkOnAccent: contrast(c.colorAccentContrast, c.colorAccent),
  }));
}

// ---------------------------------------------------------------------------
// Assertions.
// ---------------------------------------------------------------------------
function assertMatrix(cells) {
  const failures = [];

  if (cells.length !== THEMES.length * ACCENTS.length) {
    failures.push(
      `expected ${THEMES.length * ACCENTS.length} cells, resolved ${cells.length}`,
    );
  }

  // (a) COLLAPSE — the bug this gate exists for.
  for (const theme of THEMES) {
    const inTheme = cells.filter((c) => c.theme === theme);
    const distinct = new Set(inTheme.map((c) => c.colorAccent));
    if (distinct.size !== ACCENTS.length) {
      failures.push(
        `[a] accent COLLAPSE under data-theme="${theme}": ${distinct.size} distinct ` +
          `--color-accent across ${inTheme.length} swatches (expected ${ACCENTS.length}). ` +
          `Resolved: ${[...distinct].join(", ")}`,
      );
    }
  }

  // (b)/(c) CONTRAST floors.
  for (const c of cells) {
    if (c.contrastAccentOnBg < MIN_UI_CONTRAST) {
      failures.push(
        `[b] theme=${c.theme} accent=${c.accent}: accent ${c.colorAccent} on bg ` +
          `${c.colorBg} is ${c.contrastAccentOnBg}:1 (floor ${MIN_UI_CONTRAST}:1)`,
      );
    }
    if (c.contrastInkOnAccent < MIN_TEXT_CONTRAST) {
      failures.push(
        `[c] theme=${c.theme} accent=${c.accent}: ink ${c.colorAccentContrast} on accent ` +
          `${c.colorAccent} is ${c.contrastInkOnAccent}:1 (floor ${MIN_TEXT_CONTRAST}:1)`,
      );
    }
  }

  return failures;
}

function assertNoDrift(cells) {
  if (!existsSync(EXPECTED)) {
    return [
      `[d] expected matrix missing: ${EXPECTED}. Regenerate with --write.`,
    ];
  }
  const expected = JSON.parse(readFileSync(EXPECTED, "utf8")).cells;
  const key = (c) => `${c.theme}/${c.accent}`;
  const byKey = new Map(expected.map((c) => [key(c), c]));
  const failures = [];
  for (const c of cells) {
    const want = byKey.get(key(c));
    if (want === undefined) {
      failures.push(`[d] unexpected cell ${key(c)}`);
      continue;
    }
    for (const field of [
      "colorAccent",
      "colorAccentStrong",
      "colorAccentContrast",
      "colorBg",
    ]) {
      if (c[field] !== want[field]) {
        failures.push(
          `[d] drift at ${key(c)}.${field}: expected ${want[field]}, got ${c[field]}`,
        );
      }
    }
  }
  if (expected.length !== cells.length) {
    failures.push(
      `[d] expected matrix has ${expected.length} cells, resolved ${cells.length}`,
    );
  }
  return failures;
}

// ---------------------------------------------------------------------------
// CLI.
// ---------------------------------------------------------------------------
const mode = process.argv.includes("--write")
  ? "write"
  : process.argv.includes("--check")
    ? "check"
    : "print";

const cells = withRatios(await resolveMatrix());

if (mode === "write") {
  mkdirSync(dirname(EXPECTED), { recursive: true });
  writeFileSync(
    EXPECTED,
    `${JSON.stringify(
      {
        note:
          "Resolved --color-accent* per data-accent x data-theme cell, read from a real " +
          "Chromium against packages/design-system/src/styles.css. Regenerate: " +
          "node tools/design-parity/lib/accent-matrix.mjs --write",
        floors: {
          contrastAccentOnBg: MIN_UI_CONTRAST,
          contrastInkOnAccent: MIN_TEXT_CONTRAST,
        },
        cells,
      },
      null,
      2,
    )}\n`,
  );
  console.log(`wrote ${EXPECTED} (${cells.length} cells)`);
}

if (mode === "print" || mode === "check") {
  for (const theme of THEMES) {
    const inTheme = cells.filter((c) => c.theme === theme);
    const distinct = new Set(inTheme.map((c) => c.colorAccent));
    console.log(
      `\n== data-theme="${theme}" — ${distinct.size} distinct --color-accent across ${inTheme.length} swatches ==`,
    );
    for (const c of inTheme) {
      console.log(
        `  ${c.accent.padEnd(13)} accent=${c.colorAccent.padEnd(22)} ` +
          `on-bg=${String(c.contrastAccentOnBg).padStart(5)}:1  ` +
          `ink-on-accent=${String(c.contrastInkOnAccent).padStart(5)}:1`,
      );
    }
  }
}

if (mode === "check") {
  const failures = [...assertMatrix(cells), ...assertNoDrift(cells)];
  if (failures.length > 0) {
    console.error(`\nACCENT MATRIX FAILED — ${failures.length} assertion(s):`);
    for (const f of failures) console.error(`  - ${f}`);
    process.exit(1);
  }
  console.log(
    `\nACCENT MATRIX OK — ${cells.length} cells, ${ACCENTS.length} distinct accents in each of ${THEMES.length} themes.`,
  );
}
