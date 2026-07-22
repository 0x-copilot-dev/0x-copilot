/* design-parity · rail-badge geometry probe
 * Computed styles alone cannot show WHERE the rail's children land: the
 * design pins the foot with `margin-top:auto` on `.rail-foot` while the live
 * rail pins it with `flex:1` on the items wrapper, and the design spaces the
 * brand with `margin-bottom:10px` + the rail's own `gap:2px` while the live
 * rail uses a single `margin-top:10px` on the wrapper. Both differences are
 * invisible property-for-property but land as real pixel offsets, so this
 * probe reads bounding boxes for the same landmarks on both sides.
 *
 *   node surfaces/rail-badge/geom.mjs
 */
import { readdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

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
    .sort((a, b) => rev(b) - rev(a));
  for (const c of candidates) {
    for (const rel of [
      "chrome-mac/headless_shell",
      "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    ]) {
      const p = join(cache, c, rel);
      if (existsSync(p)) return p;
    }
  }
  return undefined;
}

const SIDES = [
  {
    name: "design",
    url: "http://127.0.0.1:8111/design-kit/app-v3/index.html?dest=chats",
    wait: "nav.rail",
    delay: 1200,
    sel: {
      rail: "nav.rail",
      brand: ".rail > .rail-brand",
      firstItem: ".rail > .rail-item:nth-of-type(2)",
      lastItem: ".rail > .rail-item:nth-of-type(7)",
      foot: ".rail-foot",
      settings: ".rail-foot .rail-item",
      me: ".rail-me",
      badge: ".rail .rbadge",
    },
  },
  {
    name: "live",
    url: "http://127.0.0.1:8111/surfaces/rail-badge/live/badge.html",
    wait: "nav[data-component='app-rail']",
    delay: 500,
    sel: {
      rail: "nav[data-component='app-rail']",
      brand: "[data-rail-brand]",
      firstItem: "nav[data-component='app-rail'] [data-destination='run']",
      lastItem: "nav[data-component='app-rail'] [data-destination='tools']",
      foot: "nav[data-component='app-rail'] > div:last-of-type",
      settings: "[data-rail-action='settings']",
      me: "[data-rail-me]",
      badge: "[data-rail-badge]",
    },
  },
];

const out = {};
const browser = await chromium.launch({
  executablePath: resolveChromiumExecutable(),
});
for (const side of SIDES) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  await page.goto(side.url, { waitUntil: "load" });
  await page.waitForSelector(side.wait, { timeout: 15000 });
  await page.waitForTimeout(side.delay);
  out[side.name] = await page.evaluate((sel) => {
    const box = (s) => {
      const n = document.querySelector(s);
      if (!n) return null;
      const r = n.getBoundingClientRect();
      return {
        x: +r.x.toFixed(1),
        y: +r.y.toFixed(1),
        w: +r.width.toFixed(1),
        h: +r.height.toFixed(1),
      };
    };
    const b = {};
    for (const [k, s] of Object.entries(sel)) b[k] = box(s);
    // derived: the spacings the two sides express differently
    b._brandToFirstItem =
      b.brand && b.firstItem
        ? +(b.firstItem.y - (b.brand.y + b.brand.h)).toFixed(1)
        : null;
    b._lastItemToSettings =
      b.lastItem && b.settings
        ? +(b.settings.y - (b.lastItem.y + b.lastItem.h)).toFixed(1)
        : null;
    b._settingsToMe =
      b.settings && b.me
        ? +(b.me.y - (b.settings.y + b.settings.h)).toFixed(1)
        : null;
    b._railBottomToMe =
      b.rail && b.me
        ? +(b.rail.y + b.rail.h - (b.me.y + b.me.h)).toFixed(1)
        : null;
    b._badgeInsetTop =
      b.badge && b.firstItem ? +(b.badge.y - b.firstItem.y).toFixed(1) : null;
    b._badgeInsetRight =
      b.badge && b.firstItem
        ? +(b.firstItem.x + b.firstItem.w - (b.badge.x + b.badge.w)).toFixed(1)
        : null;
    return b;
  }, side.sel);
  await page.close();
}
await browser.close();
console.log(JSON.stringify(out, null, 2));
