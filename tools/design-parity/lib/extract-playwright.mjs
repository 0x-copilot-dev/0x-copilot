#!/usr/bin/env node
/* design-parity · headless computed-style extractor (Playwright driver)
 * =========================================================================
 * Renders ONE side of a parity pair in headless chromium, injects
 * `lib/extract-computed.js`, runs `__extractParity` against the anchor list,
 * and writes the `{label -> {tag, classes, text, styles}}` profile that
 * `lib/compare.mjs` consumes.
 *
 * This exists so a parity run is a repeatable command instead of a manual
 * browser session: the same driver reads BOTH sides (the only difference is
 * `--side`, which selects the `design` or `live` selector from anchors.json),
 * which is what makes the two profiles genuinely comparable. It also lets
 * several surfaces be measured concurrently — each on its own port — where a
 * single interactive browser would serialize them.
 *
 * Usage:
 *   node lib/extract-playwright.mjs \
 *     --url  http://127.0.0.1:8099/surfaces/<name>/design/index.html?state=list \
 *     --anchors surfaces/<name>/anchors.json \
 *     --side design \
 *     --out  surfaces/<name>/out/design-list.json
 *
 * Options:
 *   --wait-for <sel>  block until the selector appears (default: first anchor)
 *   --viewport WxH    default 1440x900
 *   --delay <ms>      extra settle time after load (default 400; the design
 *                     harnesses compile JSX with in-browser Babel, so they
 *                     need a beat after `load` before the tree exists)
 *   --fail-on-missing exit 1 if any anchor did not match (default: warn only,
 *                     because "absent in live" is itself a HIGH finding that
 *                     compare.mjs is designed to report)
 * =========================================================================
 */
import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  readdirSync,
  existsSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * Resolve a chromium binary WITHOUT requiring `npx playwright install`.
 *
 * The pinned playwright version usually wants a newer chromium revision than
 * whatever is already in the shared ms-playwright cache, and a parity run has
 * no business downloading a browser. Any recent chromium computes the same
 * styles, so prefer the newest headless shell already on disk and fall back to
 * playwright's own default only if the cache is empty.
 * Override explicitly with `PARITY_CHROMIUM`.
 */
function resolveChromiumExecutable() {
  if (process.env.PARITY_CHROMIUM) return process.env.PARITY_CHROMIUM;
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const rev = (d) => Number.parseInt(d.split("-").pop() ?? "0", 10) || 0;
  const candidates = readdirSync(cache)
    .filter(
      (d) =>
        d.startsWith("chromium_headless_shell-") || d.startsWith("chromium-"),
    )
    // headless shell first at equal revision: no window server needed.
    .sort((a, b) => rev(b) - rev(a) || (a.includes("headless") ? -1 : 1));
  for (const dir of candidates) {
    for (const rel of [
      "chrome-headless-shell-mac-arm64/chrome-headless-shell",
      "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
      "chrome-linux/chrome",
    ]) {
      const p = join(cache, dir, rel);
      if (existsSync(p)) return p;
    }
  }
  return undefined;
}

const argv = process.argv.slice(2);
const flag = (name, fallback = null) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : fallback;
};
const has = (name) => argv.includes(`--${name}`);

const url = flag("url");
const anchorsPath = flag("anchors");
const side = flag("side");
const outPath = flag("out");

if (!url || !anchorsPath || !side || !outPath) {
  console.error(
    "usage: extract-playwright.mjs --url <url> --anchors <anchors.json> --side design|live --out <out.json>",
  );
  process.exit(2);
}
if (side !== "design" && side !== "live") {
  console.error(`--side must be "design" or "live" (got ${side})`);
  process.exit(2);
}

const anchors = JSON.parse(readFileSync(resolve(anchorsPath), "utf8"));
// One anchor row describes BOTH sides; pick this side's selector. Rows without
// a selector for this side are intentionally skipped (e.g. a live-only extra).
const elements = (anchors.elements || [])
  .filter((e) => typeof e[side] === "string" && e[side].length > 0)
  .map((e) => ({ label: e.label, selector: e[side] }));

if (elements.length === 0) {
  console.error(`no anchors with a "${side}" selector in ${anchorsPath}`);
  process.exit(2);
}

const [vw, vh] = (flag("viewport", "1440x900") || "1440x900")
  .split("x")
  .map((n) => Number.parseInt(n, 10));
const delay = Number.parseInt(flag("delay", "400"), 10);
const waitFor = flag("wait-for", elements[0].selector);

const extractorSource = readFileSync(
  resolve(HERE, "extract-computed.js"),
  "utf8",
);

const browser = await chromium.launch({
  executablePath: resolveChromiumExecutable(),
});
const page = await browser.newPage({ viewport: { width: vw, height: vh } });

const consoleErrors = [];
page.on("pageerror", (err) => consoleErrors.push(String(err)));
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

await page.goto(url, { waitUntil: "load", timeout: 30_000 });

try {
  await page.waitForSelector(waitFor, { timeout: 15_000, state: "attached" });
} catch {
  // Non-fatal: an anchor that never appears is a finding, not a driver crash.
  console.warn(`[warn] wait-for selector never appeared: ${waitFor}`);
}
// The design harnesses transpile JSX in-browser; give React a paint after mount.
await page.waitForTimeout(delay);

await page.addScriptTag({ content: extractorSource });
const profile = await page.evaluate(
  (spec) => globalThis.__extractParity(spec),
  { elements },
);

await browser.close();

const missing = Object.entries(profile)
  .filter(([, v]) => v.matched === false)
  .map(([k]) => k);

mkdirSync(dirname(resolve(outPath)), { recursive: true });
writeFileSync(resolve(outPath), `${JSON.stringify(profile, null, 2)}\n`);

console.log(
  `[${side}] ${Object.keys(profile).length - missing.length}/${Object.keys(profile).length} anchors matched → ${outPath}`,
);
if (missing.length > 0)
  console.warn(`[${side}] unmatched: ${missing.join(", ")}`);
if (consoleErrors.length > 0) {
  console.warn(
    `[${side}] page errors:\n  ${consoleErrors.slice(0, 5).join("\n  ")}`,
  );
}
if (has("fail-on-missing") && missing.length > 0) process.exit(1);
