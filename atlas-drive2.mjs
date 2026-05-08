// Send a SECOND message now that Linear is authenticated.
// Test whether `suggest_connector` short-circuits with ALREADY_AUTHENTICATED.

import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 1200 },
});
const page = await context.newPage();
const log = (s) => console.log(s);

page.on("console", (msg) => {
  const t = msg.text();
  if (t.startsWith("[citations]")) return;
  log(`[console.${msg.type()}] ${t}`);
});
page.on("response", (res) => {
  const u = res.url();
  if (u.includes("/v1/agent/runs/") || u.includes("/v1/mcp/")) {
    log(`[net] ${res.status()} ${res.request().method()} ${u}`);
  }
});

await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2000);

// Click "+ New chat" to start a fresh run with a clean slate.
const newChat = page
  .locator('button:has-text("New chat"), button:has-text("+ New chat")')
  .first();
if (await newChat.count()) {
  await newChat.click();
  log("clicked New chat");
  await page.waitForTimeout(500);
}

const composer = page
  .locator('textarea[placeholder*="Ask Atlas"], textarea')
  .first();
await composer.waitFor({ state: "visible", timeout: 15000 });
await composer.click();
const PROMPT = "list my active tasks on linear";
await composer.fill(PROMPT);
await page.keyboard.press("Enter");
log(`prompt sent: ${JSON.stringify(PROMPT)}`);

// Wait + snapshot
const start = Date.now();
const seen = new Set();
while (Date.now() - start < 25_000) {
  const cards = await page
    .locator(
      '[class*="aui-message"], button:has-text("Connect"), button:has-text("Skip")',
    )
    .allTextContents()
    .catch(() => []);
  const fp = cards.join("\n").slice(0, 1200);
  if (fp && !seen.has(fp)) {
    seen.add(fp);
    log(`[snap @${((Date.now() - start) / 1000).toFixed(1)}s]\n${fp}\n---`);
  }
  await page.waitForTimeout(1000);
}
log("done");
await browser.close();
