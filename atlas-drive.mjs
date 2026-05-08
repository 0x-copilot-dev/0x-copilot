// Drive the Atlas chat end-to-end and capture browser console + network.
// Output → /tmp/atlas-browser.log

import { chromium } from "playwright";
import { writeFileSync, appendFileSync } from "node:fs";

const LOG = "/tmp/atlas-browser.log";
writeFileSync(LOG, `=== ${new Date().toISOString()} ===\n`);
const log = (line) => {
  appendFileSync(LOG, line + "\n");
  process.stdout.write(line + "\n");
};

const PROMPT = "Search for all active tasks on linear";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
});
const page = await context.newPage();

page.on("console", (msg) => log(`[console.${msg.type()}] ${msg.text()}`));
page.on("pageerror", (err) => log(`[pageerror] ${err.message}`));
page.on("requestfailed", (req) =>
  log(`[reqfail] ${req.method()} ${req.url()} :: ${req.failure()?.errorText}`),
);
page.on("response", async (res) => {
  const u = res.url();
  // Only the interesting endpoints — proxy chatter is in server log.
  if (
    u.includes("/v1/agent/") ||
    u.includes("/v1/mcp/") ||
    u.includes("/v1/auth/") ||
    u.includes("/v1/dev/")
  ) {
    log(`[net] ${res.status()} ${res.request().method()} ${u}`);
  }
});

log(`navigating to http://127.0.0.1:5173/`);
await page.goto("http://127.0.0.1:5173/", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});

// Give the dev IdP auto-mint + initial loads time to settle.
await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
await page.waitForTimeout(1500);

// Find the composer textarea.
const composer = page
  .locator('textarea[placeholder*="Ask Atlas"], textarea')
  .first();
await composer.waitFor({ state: "visible", timeout: 20000 });
log(`composer ready, typing prompt`);

await composer.click();
await composer.fill(PROMPT);
await page.keyboard.press("Enter");
log(`prompt sent: ${JSON.stringify(PROMPT)}`);

// Watch the chat for ~30s, dumping any pulse-debug + key UI states.
const start = Date.now();
const seen = new Set();
while (Date.now() - start < 30_000) {
  const cards = await page
    .locator(
      '[class*="aui-message"], [data-testid*="approval"], button:has-text("Connect"), button:has-text("Skip")',
    )
    .allTextContents()
    .catch(() => []);
  const fingerprint = cards.join("\n").slice(0, 400);
  if (fingerprint && !seen.has(fingerprint)) {
    seen.add(fingerprint);
    log(
      `[ui-snapshot @${((Date.now() - start) / 1000).toFixed(1)}s]\n${fingerprint}\n---`,
    );
  }
  await page.waitForTimeout(1000);
}

log(`done, closing`);
await browser.close();
