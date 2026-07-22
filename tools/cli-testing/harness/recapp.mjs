// Capture the REAL Electron desktop app as video via CDP Page.startScreencast
// (Playwright's recordVideo breaks Electron's firstWindow, so we screencast the
// renderer directly). Injects the same visible cursor + click ripple as the
// browser mock, forces a fresh login screen (throwaway userData subdir), and
// drives the flow hands-off. openExternal is suppressed in main so clicking the
// wallet button never launches a real browser.
//
// MODE=bridge   : loading -> login -> click "Continue with a wallet" -> "Waiting for your wallet…"
// MODE=signedin : loading -> login -> click "Use locally, no account" -> Run cockpit
//
// Env: OUT_DIR, MODE, APP_DIR, USER_SUBDIR, PACE_MS.

import { _electron as electron } from "playwright";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const APP_DIR = process.env.APP_DIR || path.join(REPO_ROOT, "apps", "desktop");
const OUT = process.env.OUT_DIR || path.join(HERE, "..", "runs", "app");
const MODE = process.env.MODE || "signedin";
const PACE = Number(process.env.PACE_MS || "1500");
const USER_SUBDIR = process.env.USER_SUBDIR || "rec-app";
const FRAMES = path.join(OUT, "frames");
mkdirSync(FRAMES, { recursive: true });

function resolveElectron() {
  const require = createRequire(path.join(REPO_ROOT, "index.js"));
  const r = require("electron");
  if (typeof r === "string" && existsSync(r)) return r;
  throw new Error("could not resolve electron binary");
}

// Cursor + ripple, injected into the renderer once the DOM is live.
const CURSOR_JS = `
(() => {
  if (document.getElementById("__cur")) return;
  const st = document.createElement("style");
  st.textContent = "#__cur{position:fixed;z-index:2147483647;width:26px;height:26px;margin:-3px 0 0 -3px;pointer-events:none;transition:left .55s cubic-bezier(.4,0,.2,1),top .55s cubic-bezier(.4,0,.2,1);left:50%;top:70%}.__rip{position:fixed;z-index:2147483646;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:rgba(95,178,236,.55);pointer-events:none;animation:__rp .6s ease-out forwards}@keyframes __rp{to{transform:scale(7);opacity:0}}";
  (document.head||document.documentElement).appendChild(st);
  const c = document.createElement("div"); c.id="__cur";
  c.innerHTML='<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l16 9-7 1.7 3.8 7.1-2.9 1.5-3.9-7.2L4 20z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg>';
  (document.body||document.documentElement).appendChild(c);
  window.__moveCursor=(x,y)=>{c.style.left=x+"px";c.style.top=y+"px"};
  window.__ripple=(x,y)=>{const r=document.createElement("div");r.className="__rip";r.style.left=x+"px";r.style.top=y+"px";document.body.appendChild(r);setTimeout(()=>r.remove(),700)};
})();
`;

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
env.COPILOT_RUNTIME_DIR =
  process.env.COPILOT_HOME || path.join(os.homedir(), ".0xcopilot");
env.COPILOT_PRODUCTION = "1";
// Throwaway client session dir -> app always starts at the login screen.
env.COPILOT_DESKTOP_USER_DATA_SUBDIR = USER_SUBDIR;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const app = await electron.launch({
  executablePath: resolveElectron(),
  args: [APP_DIR],
  cwd: REPO_ROOT,
  env,
  timeout: 120_000,
});

// Suppress the external-browser handoff in the main process.
await app.evaluate(async ({ shell }) => {
  const orig = shell.openExternal.bind(shell);
  globalThis.__extUrls = [];
  shell.openExternal = async (url) => {
    globalThis.__extUrls.push(url);
    return undefined;
  };
  void orig;
});

const page = await app.firstWindow({ timeout: 120_000 });

// --- CDP screencast: save every frame + its wall-clock offset ---
const client = await page.context().newCDPSession(page);
let n = 0;
const meta = [];
const t0 = Date.now();
client.on("Page.screencastFrame", async (f) => {
  const idx = n++;
  try {
    writeFileSync(
      path.join(FRAMES, `f-${String(idx).padStart(5, "0")}.jpg`),
      Buffer.from(f.data, "base64"),
    );
    meta.push({ n: idx, t: (Date.now() - t0) / 1000 });
  } catch {
    /* ignore */
  }
  try {
    await client.send("Page.screencastFrameAck", { sessionId: f.sessionId });
  } catch {
    /* ignore */
  }
});
await client.send("Page.startScreencast", {
  format: "jpeg",
  quality: 85,
  maxWidth: 2400,
  maxHeight: 1600,
  everyNthFrame: 1,
});

const markers = {};
const mark = (name) => {
  markers[name] = (Date.now() - t0) / 1000;
  console.log(`MARK ${name} @ ${markers[name].toFixed(2)}s (frame ~${n})`);
};
const has = async (sel) => {
  try {
    return await page.evaluate((s) => !!document.querySelector(s), sel);
  } catch {
    return false;
  }
};
async function showClick(sel) {
  await page.evaluate(CURSOR_JS);
  const box = await page.locator(sel).first().boundingBox();
  if (box) {
    const x = Math.round(box.x + box.width / 2);
    const y = Math.round(box.y + box.height / 2);
    await page.evaluate(([x, y]) => window.__moveCursor && window.__moveCursor(x, y), [x, y]);
    await sleep(700);
    await page.evaluate(([x, y]) => window.__ripple && window.__ripple(x, y), [x, y]);
    await sleep(220);
  }
  await page.click(sel).catch(() => {});
}

mark("boot");
// Wait for the login gate (this window covers the loading/boot screen).
for (let i = 0; i < 200; i++) {
  if (await has('[data-testid="sign-in-gate"]')) break;
  await sleep(300);
}
mark("login");
await page.evaluate(CURSOR_JS).catch(() => {});
await sleep(2600); // hold on the login screen

if (MODE === "bridge") {
  await showClick('[data-testid="sign-in-wallet-button"]');
  mark("clicked_wallet");
  // "Waiting for your wallet…" spinner — the browser is opening.
  for (let i = 0; i < 20; i++) {
    if (await has('[data-testid="sign-in-waiting"]')) break;
    await sleep(150);
  }
  mark("waiting");
  await sleep(2800);
  mark("end");
} else {
  await showClick('[data-testid="sign-in-button"]');
  mark("clicked_local");
  // Wait for the gate to drop (signed in -> Run cockpit).
  for (let i = 0; i < 80; i++) {
    if (!(await has('[data-testid="sign-in-gate"]'))) break;
    await sleep(400);
  }
  mark("signed_in");
  await sleep(3200); // hold on the Run cockpit
  mark("end");
}

try {
  await client.send("Page.stopScreencast");
} catch {
  /* ignore */
}
writeFileSync(
  path.join(OUT, "markers.json"),
  JSON.stringify({ mode: MODE, markers, meta, frames: n }, null, 2),
);
await app.close().catch(() => {});
console.log(`DONE mode=${MODE} frames=${n} markers=${JSON.stringify(markers)}`);
process.exit(0);
