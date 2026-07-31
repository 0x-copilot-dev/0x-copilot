#!/usr/bin/env node
/* design-parity · narrow-Studio-rail screenshots + dropdown geometry
 * =========================================================================
 * Opens the rendered narrow-rail surfaces in headless chromium and saves PNGs.
 *
 * The dropdown states are the reason this is a browser driver and not more
 * jsdom: the design-system `Menu` writes FIXED viewport coordinates from
 * `anchorRef.getBoundingClientRect()`, and jsdom reports every rect as 0. Only
 * a real layout exercises the placement + viewport clamp, so the pills are
 * clicked here, in `window-*.html`, where the rail sits hard against the
 * window's right edge exactly as `ThreadCanvas` grids it.
 *
 * For each shot it also reports whether the open panel escaped the viewport,
 * so "the dropdown is fine" is a measurement rather than an impression.
 *
 * Usage: node lib/screenshot-run-rail-narrow.mjs [--out <dir>]
 * ========================================================================= */
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const HERE = (p) => fileURLToPath(new URL(p, import.meta.url));
const LIVE = (p) => HERE("../surfaces/run-rail-narrow/live/" + p);

const outArg = process.argv.indexOf("--out");
const OUT_DIR = outArg === -1 ? LIVE("shots") : process.argv[outArg + 1];
mkdirSync(OUT_DIR, { recursive: true });

const MODEL_PILL = ".atlas-model-pill";
const TOOLS_PILL = '[data-testid="first-run-tools-button"]';

/** Panel geometry vs the viewport — negative overflow means fully on-screen. */
async function panelGeometry(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      left: Math.round(r.left),
      right: Math.round(r.right),
      top: Math.round(r.top),
      bottom: Math.round(r.bottom),
      width: Math.round(r.width),
      height: Math.round(r.height),
      overflowRight: Math.round(r.right - window.innerWidth),
      overflowLeft: Math.round(0 - r.left),
      overflowTop: Math.round(0 - r.top),
      overflowBottom: Math.round(r.bottom - window.innerHeight),
      viewport: { w: window.innerWidth, h: window.innerHeight },
    };
  }, selector);
}

const findings = [];

const browser = await chromium.launch();
try {
  // ── Transcript + composer, closed state (the two reported bugs) ──────────
  for (const variant of ["repro-desktop", "repro-desktop-before"]) {
    const page = await browser.newPage({
      viewport: { width: 700, height: 560 },
      deviceScaleFactor: 2,
      colorScheme: "dark",
    });
    await page.goto(`file://${LIVE(variant + ".html")}`);
    await page.evaluate(() => {
      const frame = document.getElementById("frame");
      for (const col of [...frame.children]) {
        const box = col.querySelector(".railbox");
        if (!box || box.dataset.case !== "300") col.remove();
      }
      frame.querySelector(".railbox").style.height = "420px";
    });
    await page.waitForTimeout(150);
    await page.screenshot({ path: `${OUT_DIR}/${variant}-300.png` });
    await page.close();
  }

  // ── Dropdowns, in a real app window, at two rail widths ──────────────────
  // The open state is baked into `window-<w>-<which>.html` by the render
  // harness (the popovers are click-driven state a serialized render cannot
  // reach). The panel's inline coordinates were written by the shipping
  // `Menu.computePosition()` from the rect measured in this same 1280x800
  // window, so what is screenshotted here is where the panel really lands.
  // 760 is MAX_RAIL_WIDTH — nothing overflows there, so it is the no-op
  // control: the clamp must leave those panels exactly at the anchor's edge.
  for (const width of [300, 360, 760]) {
    for (const [name, panel] of [
      ["model", ".atlas-model-pill__menu"],
      ["tools", '[data-testid="composer-tools-popover"]'],
    ]) {
      const page = await browser.newPage({
        viewport: { width: 1280, height: 800 },
        deviceScaleFactor: 2,
        colorScheme: "dark",
      });
      await page.goto(`file://${LIVE(`window-${width}-${name}.html`)}`);
      await page.waitForTimeout(150);

      const geom = await panelGeometry(page, panel);
      findings.push({ width, dropdown: name, geom });
      await page.screenshot({
        path: `${OUT_DIR}/dropdown-${name}-${width}.png`,
      });
      await page.close();
    }
  }
} finally {
  await browser.close();
}

writeFileSync(
  `${OUT_DIR}/dropdown-geometry.json`,
  JSON.stringify(findings, null, 2),
);
for (const f of findings) {
  const g = f.geom;
  if (g === null) {
    console.log(`rail ${f.width} · ${f.dropdown}: PANEL NOT FOUND`);
    continue;
  }
  const escapes = ["Right", "Left", "Top", "Bottom"]
    .filter((side) => g[`overflow${side}`] > 0)
    .map((side) => `${side.toLowerCase()} by ${g[`overflow${side}`]}px`);
  console.log(
    `rail ${f.width} · ${f.dropdown}: ${g.width}x${g.height} at [${g.left},${g.top}] ` +
      `— ${escapes.length === 0 ? "fully on-screen" : "ESCAPES " + escapes.join(", ")}`,
  );
}
console.log(`\nshots → ${OUT_DIR}`);
