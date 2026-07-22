// Drives step 3 of the design-parity skill headlessly: for each PRD-P8 state,
// load the DESIGN page (?state=) and the LIVE fixture, run the SAME
// lib/extract-computed.js in each page context against that state's anchors,
// and write out/{design,live}-ollama-<state>.json.
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const ROOT =
  "/Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/app-icon-sizing-136bc3/tools/design-parity";
const BASE = "http://127.0.0.1:8099";
const SURFACE = `${ROOT}/surfaces/first-run`;

const extractor = readFileSync(`${ROOT}/lib/extract-computed.js`, "utf8");
const anchors = JSON.parse(
  readFileSync(`${SURFACE}/anchors-ollama.json`, "utf8"),
);

// anchors are labelled "<state>.<part>"; group them per state.
const byState = new Map();
for (const el of anchors.elements) {
  const state = el.label.split(".")[0];
  if (!byState.has(state)) byState.set(state, []);
  byState.get(state).push(el);
}

const browser = await chromium.launch({ channel: "chrome" });
// A real, fixed viewport: the mock's widths are percentage-derived, and a
// zero-width context collapses them (both pages pin their own geometry, but a
// sane viewport keeps anything unpinned honest).
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

const summary = [];
for (const [state, els] of byState) {
  for (const side of ["design", "live"]) {
    // `detected` is not a ?state= mode in the mock — it is the ① card AFTER
    // clicking "Get Ollama ↗" (the mock flips its local `rt` to "found").
    // Reach it the way a user does, rather than inventing a mode the design
    // does not have.
    const designState = state === "detected" ? "not-installed" : state;
    const url =
      side === "design"
        ? `${BASE}/surfaces/first-run/design/ollama.html?state=${designState}`
        : `${BASE}/surfaces/first-run/live/ollama-${state}.html`;
    await page.goto(url, { waitUntil: "networkidle" });
    // The design side is Babel-compiled in-page; give React a beat to mount.
    if (side === "design") {
      await page.waitForSelector(".fr-gcard");
      if (state === "detected") {
        await page.click(".fr-gcard .fr-dep .acts .gbtn--pri");
        await page.waitForSelector(".fr-gcard .fr-dep .ok");
      }
    }

    const spec = {
      elements: els.map((e) => ({ label: e.label, selector: e[side] })),
    };
    await page.evaluate(extractor);
    const out = await page.evaluate((s) => globalThis.__extractParity(s), spec);

    const path = `${SURFACE}/out/${side}-ollama-${state}.json`;
    writeFileSync(path, JSON.stringify(out, null, 2) + "\n");
    const found = Object.values(out).filter(
      (v) => v && v.styles && Object.keys(v.styles).length > 0,
    ).length;
    summary.push(
      `${state.padEnd(14)} ${side.padEnd(6)} ${found}/${els.length} resolved`,
    );
  }
}
await browser.close();
console.log(summary.join("\n"));
