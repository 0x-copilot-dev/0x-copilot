// Inspect the OLD conversation ("Search for all active tasks on linear")
// where the Connect card was stuck pre-fix.
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 1200 },
});
const page = await context.newPage();
const log = (s) => console.log(s);
page.on("console", (m) => {
  const t = m.text();
  if (!t.startsWith("[citations]")) log(`[console.${m.type()}] ${t}`);
});

await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2000);

const oldConv = page
  .locator('button:has-text("Search for all active tasks")')
  .first();
if (await oldConv.count()) {
  await oldConv.click();
  log("clicked OLD conversation");
} else {
  log("OLD conversation not found");
}
await page.waitForTimeout(2000);

const cards = await page
  .locator(
    '[class*="aui-message"], [class*="approval"], [class*="connector-auth"], button:has-text("Connect"), button:has-text("Skip")',
  )
  .allTextContents();
log("=== OLD CHAT CARDS ===");
cards.forEach((c, i) => log(`[${i}] ${c.slice(0, 400).replace(/\s+/g, " ")}`));

log("\n=== TOPBAR ===");
log(
  await page
    .locator('[class*="topbar"], [class*="aui-topbar"]')
    .first()
    .textContent()
    .catch(() => "?"),
);

log("\n=== APPROVAL PANE ===");
log(
  "waiting visible: " +
    (await page
      .locator('text="Atlas is waiting on you"')
      .first()
      .isVisible()
      .catch(() => false)),
);
log(
  "recent visible:  " +
    (await page
      .locator('text="Resolved within the last hour"')
      .first()
      .isVisible()
      .catch(() => false)),
);

await browser.close();
