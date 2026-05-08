import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 1200 },
});
const page = await context.newPage();
const log = (s) => console.log(s);
page.on("console", (m) => {
  const t = m.text();
  if (t.startsWith("[citations]")) return;
  log(`[console.${m.type()}] ${t}`);
});

await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(2000);

// Open the existing conversation.
const conv = page
  .locator('button:has-text("Search for all active tasks")')
  .first();
if (await conv.count()) await conv.click();
await page.waitForTimeout(1000);

// 1. Direct fetch from the FE side to see what the connectors API returns.
const serversRaw = await page.evaluate(async () => {
  const r = await fetch("/v1/mcp/servers?org_id=org_acme&user_id=usr_sarah", {
    credentials: "same-origin",
  });
  return r.ok ? r.json() : { error: r.status };
});
log("=== /v1/mcp/servers (FE-fetched) ===");
log(JSON.stringify(serversRaw, null, 2).slice(0, 1200));

// 2. Look at the raw run events to see what args got emitted on the Connect card.
const runId = "cf07bb9c0e9f44efa92b1ca353ea1be8";
const events = await page.evaluate(async (rid) => {
  const r = await fetch(
    `/v1/agent/runs/${rid}/events?org_id=org_acme&user_id=usr_sarah&after_sequence=0`,
    {
      credentials: "same-origin",
    },
  );
  return r.ok ? r.json() : { error: r.status };
}, runId);
log("\n=== run events — only mcp_auth_required ===");
const events_arr = events.events || [];
for (const ev of events_arr) {
  if (
    ev.event_type === "mcp_auth_required" ||
    (ev.payload && ev.payload.event_type === "mcp_auth_required")
  ) {
    log(JSON.stringify(ev, null, 2).slice(0, 1500));
  }
}
log(`\n(total events: ${events_arr.length})`);

await browser.close();
