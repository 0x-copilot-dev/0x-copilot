#!/usr/bin/env node
/* Design-composer Tools interaction audit.
 *
 * Exercises the actual vendored design mock rather than inferring behaviour
 * from JSX: pointer, Enter, Space, Web Search state, and Escape. The matching
 * live checks run against the packaged Electron app in
 * tools/desktop-journeys/composer-tools/tools_popover.py, because static live
 * parity HTML deliberately has no React event handlers. */
import { existsSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const url = process.argv[2] ?? "http://127.0.0.1:8099";
const output = resolve(ROOT, "surfaces/composer/out/audit-design-tools.json");

function resolveChromiumExecutable() {
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const revision = (entry) =>
    Number.parseInt(entry.split("-").pop() ?? "0", 10) || 0;
  const candidates = readdirSync(cache)
    .filter(
      (entry) =>
        entry.startsWith("chromium_headless_shell-") ||
        entry.startsWith("chromium-"),
    )
    .sort((a, b) => revision(b) - revision(a));
  for (const entry of candidates) {
    for (const relativePath of [
      "chrome-headless-shell-mac-arm64/chrome-headless-shell",
      "chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
      "chrome-linux/chrome",
    ]) {
      const executable = join(cache, entry, relativePath);
      if (existsSync(executable)) return executable;
    }
  }
  return undefined;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const browser = await chromium.launch({
  executablePath: resolveChromiumExecutable(),
});
const page = await browser.newPage({ viewport: { width: 1040, height: 720 } });

try {
  await page.goto(`${url}/surfaces/composer/design/index.html?state=closed`, {
    waitUntil: "load",
  });
  await page.waitForSelector('[data-parity-ready="1"]');

  const trigger = page.locator('button[title="Tools & connections"]');
  const initial = await trigger.evaluate((node) => ({
    tag: node.tagName.toLowerCase(),
    role: node.getAttribute("role"),
    tabIndex: node.tabIndex,
    open: node.hasAttribute("data-open"),
  }));

  await trigger.click();
  await page.waitForSelector(".pop");
  const pointerOpen = await page.locator(".pop").isVisible();
  const webToggle = page.locator('button[aria-label="Toggle web search"]');
  const before = await webToggle.getAttribute("data-on");
  await webToggle.click();
  const afterPointer = await webToggle.getAttribute("data-on");
  assert(
    before !== afterPointer,
    "design pointer toggle did not change Web Search",
  );
  await page.keyboard.press("Escape");
  await page.waitForSelector(".pop", { state: "detached" });

  await trigger.focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector(".pop");
  const enterOpen = await page.locator(".pop").isVisible();
  await page.keyboard.press("Escape");
  await page.waitForSelector(".pop", { state: "detached" });

  await trigger.focus();
  await page.keyboard.press("Space");
  await page.waitForSelector(".pop");
  const spaceOpen = await page.locator(".pop").isVisible();
  await page.keyboard.press("Escape");
  await page.waitForSelector(".pop", { state: "detached" });

  const result = {
    initial,
    pointerOpen,
    webSearch: { before, afterPointer },
    enterOpen,
    spaceOpen,
    escapeClosed: !(await page.locator(".pop").count()),
  };
  writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
