// Open the existing tab and dump all chat messages + card states.
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 1200 },
});
const page = await context.newPage();
const log = (s) => console.log(s);

page.on("console", (msg) => {
  const t = msg.text();
  // Filter the noisy citations spam.
  if (!t.startsWith("[citations]")) log(`[console.${msg.type()}] ${t}`);
});

await page.goto("http://127.0.0.1:5173/", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2000);

// Click the most recent conversation in the sidebar (the one we already have).
const firstConv = page
  .locator(
    '[class*="conversation-row"], button:has-text("Search for all active tasks")',
  )
  .first();
if (await firstConv.count()) {
  await firstConv.click().catch(() => {});
  await page.waitForTimeout(1500);
}

// Dump every message bubble + tool card + approval card.
const cards = await page
  .locator(
    '[data-message-id], [class*="aui-message"], [class*="approval"], [class*="connector-auth"], button:has-text("Connect"), button:has-text("Skip")',
  )
  .allTextContents();
log("=== CHAT CARDS / MESSAGES ===");
cards.forEach((c, i) => log(`[${i}] ${c.slice(0, 500).replace(/\s+/g, " ")}`));

log("\n=== TOPBAR STATUS ===");
const topbar = await page
  .locator('[class*="topbar"], [class*="aui-topbar"]')
  .first()
  .textContent()
  .catch(() => "(no topbar found)");
log(topbar);

log("\n=== APPROVAL PANE (right side) ===");
const approvalPane = await page
  .locator('text="Atlas is waiting on you"')
  .first()
  .isVisible()
  .catch(() => false);
log(`approval pane visible: ${approvalPane}`);

await browser.close();
