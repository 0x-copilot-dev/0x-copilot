#!/usr/bin/env node
/* Throwaway probe: confirm the LIVE tools HTML actually renders (tokens
 * resolve, elements have boxes) before the selector-mapping phase.
 * Run: node surfaces/tools/live-probe.mjs <state>
 */
import { homedir } from "node:os";
import { join } from "node:path";
import { existsSync, readdirSync } from "node:fs";
import { chromium } from "playwright";

function resolveChromiumExecutable() {
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const rev = (d) => Number.parseInt(d.split("-").pop() ?? "0", 10) || 0;
  const candidates = readdirSync(cache)
    .filter(
      (d) =>
        d.startsWith("chromium_headless_shell-") || d.startsWith("chromium-"),
    )
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

const state = process.argv[2] || "default";
const browser = await chromium.launch({
  executablePath: resolveChromiumExecutable(),
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => console.error("[console]", m.type(), m.text()));
await page.goto(`http://127.0.0.1:8114/surfaces/tools/live/${state}.html`, {
  waitUntil: "load",
});
await page.waitForTimeout(400);

const out = await page.evaluate(() => {
  const probe = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      n: document.querySelectorAll(sel).length,
      box: [Math.round(r.width), Math.round(r.height)],
      font: `${cs.fontSize}/${cs.fontWeight} ${cs.fontFamily.split(",")[0]}`,
      color: cs.color,
      bg: cs.backgroundColor,
      pad: cs.padding,
      radius: cs.borderRadius,
      border: cs.borderTopWidth + " " + cs.borderTopColor,
      text: (el.textContent || "").trim().slice(0, 48),
    };
  };
  const sels = [
    "#frame",
    '[data-testid="connectors-route"]',
    '[data-testid="connectors-route-panel"]',
    '[data-testid="page-header-title"]',
    '[data-testid="page-header-subtitle"]',
    '[data-testid="page-header-primary-action"]',
    '[data-testid="tools-policy-note"]',
    '[data-testid="tools-policy-note-link"]',
    '[data-testid="filter-tabs"]',
    '[data-testid="card-grid"]',
    '[data-testid="connector-card"]',
    '[data-testid="connector-card-name"]',
    '[data-testid="connector-card-description"]',
    '[data-testid="status-pill"]',
    '[data-testid="access-mode-segment"]',
    '[data-testid="access-mode-option-read_act"]',
    '[data-testid="access-mode-option-read"]',
    '[data-testid="connector-card-last-sync"]',
    '[data-testid="settings-modal-scrim"]',
    '[data-testid="settings-modal"]',
    '[data-testid="connect-catalog-list"]',
    '[data-testid="connect-catalog-option"]',
    '[data-testid="connect-catalog-custom"]',
    '[data-testid="step-dots"]',
  ];
  const res = {};
  for (const s of sels) res[s] = probe(s);
  const rootBg = getComputedStyle(document.documentElement).getPropertyValue(
    "--color-bg",
  );
  return { rootBg: rootBg.trim(), res };
});
console.log(JSON.stringify(out, null, 1));
await browser.close();
