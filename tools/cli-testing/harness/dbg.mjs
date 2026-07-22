import { chromium } from "playwright";
const URL = process.env.SITE_URL || "http://localhost:4400/";
const b = await chromium.launch({ headless: true });
const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
await ctx.route("**/v1/auth/providers", (r) => r.fulfill({ json: [] }));
await ctx.route("**/v1/auth/siwe/nonce**", (r) => r.fulfill({ json: { nonce: "k9Q2xR7mT4wZ" } }));
await ctx.route("**/v1/auth/siwe/verify**", (r) => r.fulfill({ json: { bearer_token: "b", session_id: "s", user_id: "u", requires_mfa: false } }));
await ctx.route("**/v1/me/**", (r) => r.fulfill({ json: {} }));
const p = await ctx.newPage();
p.on("console", (m) => { if (["error", "warning"].includes(m.type())) console.log(`[console.${m.type()}] ${m.text().slice(0, 160)}`); });
p.on("response", async (res) => {
  const u = res.url();
  if (!/\/(v1|api)\//.test(u)) return;
  const ct = res.headers()["content-type"] || "";
  console.log(`[resp] ${res.status()} ${ct.split(";")[0].padEnd(24)} ${u.replace(/^https?:\/\/[^/]+/, "")}`);
});
await p.goto(URL, { waitUntil: "domcontentloaded" });
const ok = await p.waitForSelector('[data-testid="login-option-wallet"]', { timeout: 8000 }).then(() => true).catch(() => false);
console.log("LOGIN_RENDERED=" + ok);
if (ok) {
  await p.click('[data-testid="login-option-wallet"]').catch(() => {});
  await p.waitForTimeout(1500);
  await p.click('[data-testid="wallet-provider-io.metamask"]').catch(() => {});
  await p.waitForTimeout(1500);
}
await b.close();
process.exit(0);
