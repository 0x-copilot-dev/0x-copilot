// Automated, MOCKED wallet-login capture for the web app (same login UI as the
// desktop sign-in). Injects a fake EIP-6963 MetaMask wallet (REAL fox icon) +
// mocks the SIWE API, drives the flow hands-off with a VISIBLE cursor + click
// ripple, and blocks full-page navigation so no 404 can appear. Captures a clean
// page video via Playwright's own recorder (no screen, no privacy leak).
//
// Env: OUT_DIR, SITE_URL, W, H, PACE_MS, MM_ICON_PATH.

import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = process.env.OUT_DIR || "./wallet";
const URL = process.env.SITE_URL || "http://localhost:4400/";
const PACE = Number(process.env.PACE_MS || "1500");
const W = Number(process.env.W || "1600");
const H = Number(process.env.H || "1000");

// Real MetaMask fox icon -> data URI
const iconPath = process.env.MM_ICON_PATH || path.join(HERE, "metamask.svg");
const ICON =
  "data:image/svg+xml;base64," +
  Buffer.from(readFileSync(iconPath)).toString("base64");

const ADDR = "0x1A2b3C4d5E6f7890AbCdEf1234567890fEdCbA98";

const INIT = `
(() => {
  // --- block full-page navigation so the app can never 404 during the demo ---
  try {
    const L = window.location;
    ["assign","replace","reload"].forEach(m => { try { L[m] = function(){}; } catch(e){} });
    let href = L.href;
    try { Object.defineProperty(L, "href", { get:()=>href, set:function(){}, configurable:true }); } catch(e){}
    window.open = function(){ return null; };
  } catch(e){}

  // --- mock EIP-6963 MetaMask wallet (real fox icon) ---
  const ADDR = ${JSON.stringify(ADDR)};
  const provider = { isMetaMask:true,
    request: async ({method}) => {
      if (method==="eth_requestAccounts"||method==="eth_accounts") return [ADDR];
      if (method==="eth_chainId") return "0x1237"; // 4663 Robinhood Chain
      if (method==="personal_sign"){ await new Promise(r=>setTimeout(r,1100)); return "0x"+"1b".repeat(65); }
      return null;
    }, on(){}, removeListener(){} };
  const info = { uuid:"mock-mm", name:"MetaMask", icon:${JSON.stringify(ICON)}, rdns:"io.metamask" };
  const announce = () => window.dispatchEvent(new CustomEvent("eip6963:announceProvider",{detail:Object.freeze({info,provider})}));
  window.addEventListener("eip6963:requestProvider", announce);
  window.ethereum = provider; announce();

  // --- visible cursor + click ripple so viewers see the interactions ---
  function mount(){
    if (document.getElementById("__cur")) return;
    const st=document.createElement("style");
    st.textContent="#__cur{position:fixed;z-index:2147483647;width:26px;height:26px;margin:-3px 0 0 -3px;pointer-events:none;transition:left .55s cubic-bezier(.4,0,.2,1),top .55s cubic-bezier(.4,0,.2,1);left:50%;top:66%}.__rip{position:fixed;z-index:2147483646;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:rgba(95,178,236,.55);pointer-events:none;animation:__rp .6s ease-out forwards}@keyframes __rp{to{transform:scale(7);opacity:0}}";
    (document.head||document.documentElement).appendChild(st);
    const c=document.createElement("div"); c.id="__cur";
    c.innerHTML='<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l16 9-7 1.7 3.8 7.1-2.9 1.5-3.9-7.2L4 20z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    (document.body||document.documentElement).appendChild(c);
    window.__moveCursor=(x,y)=>{c.style.left=x+"px";c.style.top=y+"px"};
    window.__ripple=(x,y)=>{const r=document.createElement("div");r.className="__rip";r.style.left=x+"px";r.style.top=y+"px";document.body.appendChild(r);setTimeout(()=>r.remove(),700)};
  }
  if (document.readyState!=="loading") mount(); else document.addEventListener("DOMContentLoaded",mount);
})();
`;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: W, height: H },
  colorScheme: "dark",
  deviceScaleFactor: 2,
  recordVideo: { dir: OUT, size: { width: W, height: H } },
});
await context.addInitScript(INIT);
await context.route("**/v1/auth/providers", (r) => r.fulfill({ json: [] }));
// Session-restore check on load -> 401 (no session) so the app shows the login
// screen cleanly instead of choking on an HTML response.
await context.route("**/v1/auth/session", (r) =>
  r.fulfill({ status: 401, json: { detail: "no session" } }));
await context.route("**/v1/telemetry/**", (r) => r.fulfill({ status: 200, json: {} }));
await context.route("**/v1/auth/siwe/nonce**", (r) => r.fulfill({ json: { nonce: "k9Q2xR7mT4wZ" } }));
await context.route("**/v1/auth/siwe/verify**", (r) =>
  r.fulfill({ json: { bearer_token: "mock.bearer.token", session_id: "sess_demo", user_id: "user_demo", requires_mfa: false } }));
await context.route("**/v1/me/**", (r) => r.fulfill({ json: {} }));

const page = await context.newPage();
const sleep = (ms) => page.waitForTimeout(ms);

// Move the on-screen cursor to a target, ripple, then click.
async function showClick(sel) {
  const box = await page.locator(sel).first().boundingBox();
  if (box) {
    const x = Math.round(box.x + box.width / 2);
    const y = Math.round(box.y + box.height / 2);
    await page.evaluate(([x, y]) => window.__moveCursor && window.__moveCursor(x, y), [x, y]);
    await sleep(650);
    await page.evaluate(([x, y]) => window.__ripple && window.__ripple(x, y), [x, y]);
    await sleep(200);
  }
  await page.click(sel);
}

await page.goto(URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector('[data-testid="login-option-wallet"]', { timeout: 25_000 });
await sleep(2600); // hold on the login screen

await showClick('[data-testid="login-option-wallet"]');
await page.waitForSelector('[data-testid="wallet-provider-io.metamask"]', { timeout: 10_000 });
await sleep(PACE); // "Choose a wallet"

await showClick('[data-testid="wallet-provider-io.metamask"]');
await page.waitForSelector('[data-testid="wallet-sign-submit"]', { timeout: 10_000 });
await sleep(PACE + 900); // "Signature request"

await showClick('[data-testid="wallet-sign-submit"]');
await sleep(2200); // "Verifying signature…" then stop (before any redirect)

const video = page.video();
await context.close();
await browser.close();
if (video) console.log("VIDEO_PATH=" + (await video.path()));
process.exit(0);
