// Capture the REAL 0xCopilot hero sequence as trailer footage.
//
//   boot -> sign in -> FTUE add a provider key -> type a goal -> agent runs ->
//   the Studio canvas AUTO-PRESENTS the CSV dataset it just made
//
// This is `recapp.mjs`'s capture pattern (CDP Page.startScreencast against the
// real packaged Electron app, visible cursor + click ripple injected into the
// renderer, throwaway userData subdir so every launch is a genuine first run)
// driving the flow that `tools/desktop-journeys/generative-workflows/
// g2b_csv_canvas_autopresent.py` proves: the run ends and the table is simply
// there — no Sources click, no navigation.
//
// The app is launched with its OWN shipped defaults (SURFACES_V2 /
// ARTIFACT_EFFECTS_V2 / ARTIFACT_DRAFTS_V2 are already on in
// apps/desktop/main/services/service-env.ts), so what lands in the frames is
// the product, not a test-only configuration. Provider keys are stripped from
// the child environment on purpose: the key the film shows being added is the
// key the run actually uses.
//
// SECURITY: the BYOK value is read from services/ai-backend/.env and typed into
// the app's `type="password"` field. It is never printed, never written to any
// output file, and the run self-checks its own artifacts for it before exiting.
//
// Output (OUT_DIR):
//   frames/f-XXXXX.jpg   every captured frame
//   markers.json         { markers, meta:[{n,t}], frames, window, ... }
//   run-report.json      real run id / status / event census / CSV bytes
//   rec.log              the same beat log printed to stdout
//   still-*.png          full-resolution stills at the key beats
//
// Env: OUT_DIR, USER_SUBDIR, APP_DIR, COPILOT_HOME, PROVIDER (auto|openai|
//      anthropic|openrouter), MODEL, GOAL, MAX_FPS, TYPE_DELAY_MS,
//      RUN_TIMEOUT_MS, BOOT_TIMEOUT_MS, HOLD_CANVAS_MS, WIN_W, WIN_FORCE.

import { _electron as electron } from "playwright";
import {
  existsSync,
  mkdirSync,
  writeFileSync,
  appendFileSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");

const APP_DIR = process.env.APP_DIR || path.join(REPO_ROOT, "apps", "desktop");
const OUT = process.env.OUT_DIR || path.join(HERE, "..", "runs", "trailer");
const FRAMES = path.join(OUT, "frames");
const USER_SUBDIR = process.env.USER_SUBDIR || "trailer-hero";
const COPILOT_HOME =
  process.env.COPILOT_HOME || path.join(os.homedir(), ".0xcopilot");
const PROVIDER = (process.env.PROVIDER || "auto").trim().toLowerCase();
const MODEL = (process.env.MODEL || "").trim();
const MAX_FPS = Number(process.env.MAX_FPS || "30");
const MAX_FRAMES = Number(process.env.MAX_FRAMES || "24000");
const TYPE_DELAY = Number(process.env.TYPE_DELAY_MS || "12");
const RUN_TIMEOUT_MS = Number(process.env.RUN_TIMEOUT_MS || "480000");
const BOOT_TIMEOUT_MS = Number(process.env.BOOT_TIMEOUT_MS || "300000");
const HOLD_CANVAS_MS = Number(process.env.HOLD_CANVAS_MS || "10000");
const WIN_W = Number(process.env.WIN_W || "1504");
const WIN_FORCE = /^(1|true|yes|on)$/i.test(process.env.WIN_FORCE || "");

mkdirSync(FRAMES, { recursive: true });
const LOGFILE = path.join(OUT, "rec.log");

function log(line) {
  console.log(line);
  try {
    appendFileSync(LOGFILE, line + "\n");
  } catch {
    /* ignore */
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── the goal the film shows being typed ──────────────────────────────────────
// Verbatim from g2_csv_lifecycle.CREATE_PROMPT (the proven prompt for this
// beat), flattened to one line: the composer sends on Enter, so a typed
// newline would fire the message half-written.
const DEFAULT_GOAL = `Create a reviewable CSV dataset artifact named forecast.csv.
It must be a valid UTF-8 RFC-4180-style CSV with exactly these headers in this
order: month,region,bookings,forecast. Include at least three monthly rows and
integer bookings and forecast values. Keep it as an editable dataset/table in
Studio. Do not write any local workspace file, do not stage an effect, do not
browse, and do not use connectors or unrelated tools.`;
const GOAL = (process.env.GOAL || DEFAULT_GOAL).replace(/\s+/g, " ").trim();

// ── BYOK key (read, never printed) ───────────────────────────────────────────
const DOTENV =
  process.env.COPILOT_JOURNEY_DOTENV ||
  path.join(REPO_ROOT, "services", "ai-backend", ".env");
const KEY_VAR = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
};
const PROVIDER_LABEL = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openrouter: "OpenRouter",
};

function loadEnvKey(provider) {
  const name = KEY_VAR[provider];
  if (!name || !existsSync(DOTENV)) return null;
  for (const line of readFileSync(DOTENV, "utf8").split(/\r?\n/)) {
    if (!line.startsWith(`${name}=`)) continue;
    const value = line
      .slice(name.length + 1)
      .trim()
      .replace(/^["']|["']$/g, "");
    return value || null;
  }
  return null;
}

function pickProvider() {
  const order =
    PROVIDER === "auto" ? ["openai", "anthropic", "openrouter"] : [PROVIDER];
  for (const provider of order) {
    const key = loadEnvKey(provider);
    if (key) return { provider, key };
  }
  throw new Error(
    `no BYOK key available in ${DOTENV} for ${order.join("/")} (value never printed)`,
  );
}

const { provider, key: BYOK } = pickProvider();
log(`provider=${provider} keySource=${DOTENV} (value never printed)`);

// ── the visible cursor + click ripple (same as recapp.mjs) ───────────────────
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

function resolveElectron() {
  const require = createRequire(path.join(REPO_ROOT, "index.js"));
  const resolved = require("electron");
  if (typeof resolved === "string" && existsSync(resolved)) return resolved;
  throw new Error("could not resolve the electron binary");
}

// ── launch env: the shipped product, minus any inherited provider key ────────
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
for (const name of [
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "GOOGLE_API_KEY",
  "OPENROUTER_API_KEY",
]) {
  delete env[name];
}
env.COPILOT_RUNTIME_DIR = COPILOT_HOME;
env.COPILOT_PRODUCTION = "1";
env.COPILOT_DESKTOP_USER_DATA_SUBDIR = USER_SUBDIR;

log(`appDir=${APP_DIR}`);
log(`runtimeDir=${COPILOT_HOME}`);
log(`userDataSubdir=${USER_SUBDIR}`);
log(`outDir=${OUT}`);

const app = await electron.launch({
  executablePath: resolveElectron(),
  args: [APP_DIR],
  cwd: REPO_ROOT,
  env,
  timeout: BOOT_TIMEOUT_MS,
});

// Never hand the OS browser a sign-in/OAuth URL from a capture run.
await app.evaluate(async ({ shell }) => {
  globalThis.__extUrls = [];
  shell.openExternal = async (url) => {
    globalThis.__extUrls.push(url);
    return undefined;
  };
});

const proc = app.process();
const mainLog = path.join(OUT, "main.log");
proc.stdout?.on("data", (b) => {
  try {
    appendFileSync(mainLog, `[out] ${b.toString()}`);
  } catch {
    /* ignore */
  }
});
proc.stderr?.on("data", (b) => {
  try {
    appendFileSync(mainLog, `[err] ${b.toString()}`);
  } catch {
    /* ignore */
  }
});

const page = await app.firstWindow({ timeout: BOOT_TIMEOUT_MS });

// ── 16:9 content area so the footage composes straight into a 1920x1080 film ─
// WIN_FORCE=1 asks for the requested width even when it overflows this host's
// screen: the renderer viewport is what the screencast captures, so an
// off-screen window still yields the layout a wider display would show — which
// is how the Studio canvas gets enough room to render the whole table.
const windowSize = await app
  .evaluate(
    async ({ BrowserWindow, screen }, opts) => {
      const win = BrowserWindow.getAllWindows().find((w) => !w.isDestroyed());
      if (!win) return null;
      const area = screen.getPrimaryDisplay().workAreaSize;
      const want = opts.want;
      if (opts.force) {
        const width = want;
        const height = Math.round((width * 9) / 16);
        win.setContentSize(width, height);
        const [cw, ch] = win.getContentSize();
        return { width: cw, height: ch, workArea: area, forced: true };
      }
      let width = Math.min(want, area.width - 24);
      let height = Math.round((width * 9) / 16);
      if (height > area.height - 32) {
        height = area.height - 32;
        width = Math.round((height * 16) / 9);
      }
      win.setContentSize(width, height);
      win.center();
      const [cw, ch] = win.getContentSize();
      return { width: cw, height: ch, workArea: area };
    },
    { want: WIN_W, force: WIN_FORCE },
  )
  .catch(() => null);
log(`window=${JSON.stringify(windowSize)}`);

// ── CDP screencast ───────────────────────────────────────────────────────────
const client = await page.context().newCDPSession(page);
let frames = 0;
let dropped = 0;
let lastWrittenAt = 0;
const minFrameGap = MAX_FPS > 0 ? 1000 / MAX_FPS : 0;
const meta = [];
const t0 = Date.now();
let capturing = true;

client.on("Page.screencastFrame", async (f) => {
  const now = Date.now();
  const take =
    capturing && frames < MAX_FRAMES && now - lastWrittenAt >= minFrameGap;
  if (take) {
    lastWrittenAt = now;
    const idx = frames++;
    try {
      writeFileSync(
        path.join(FRAMES, `f-${String(idx).padStart(5, "0")}.jpg`),
        Buffer.from(f.data, "base64"),
      );
      meta.push({ n: idx, t: (now - t0) / 1000 });
    } catch {
      /* ignore */
    }
  } else {
    dropped++;
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

// ── beat markers ─────────────────────────────────────────────────────────────
const markers = {};
function mark(name) {
  markers[name] = Number(((Date.now() - t0) / 1000).toFixed(3));
  log(`MARK ${name} @ ${markers[name].toFixed(2)}s (frame ~${frames})`);
}

// ── page helpers ─────────────────────────────────────────────────────────────
const inject = () => page.evaluate(CURSOR_JS).catch(() => {});

async function has(sel) {
  try {
    return await page.evaluate((s) => !!document.querySelector(s), sel);
  } catch {
    return false;
  }
}

async function waitFor(sel, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await has(sel)) return true;
    await sleep(300);
  }
  return false;
}

async function waitGone(sel, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await has(sel))) return true;
    await sleep(300);
  }
  return false;
}

async function moveCursorTo(x, y) {
  await page.evaluate(
    ([px, py]) => window.__moveCursor && window.__moveCursor(px, py),
    [x, y],
  );
}

async function showClick(sel, { settle = 700 } = {}) {
  await inject();
  const box = await page
    .locator(sel)
    .last()
    .boundingBox()
    .catch(() => null);
  if (box) {
    const x = Math.round(box.x + box.width / 2);
    const y = Math.round(box.y + box.height / 2);
    await moveCursorTo(x, y);
    await sleep(settle);
    await page.evaluate(
      ([px, py]) => window.__ripple && window.__ripple(px, py),
      [x, y],
    );
    await sleep(200);
  }
  await page
    .locator(sel)
    .last()
    .click({ timeout: 30_000 })
    .catch(async () => {
      // Some FTUE controls re-render under the pointer; a DOM click still
      // exercises the same handler and keeps the capture moving. `:has-text()`
      // is Playwright-only syntax, so this fallback simply cannot apply to
      // those selectors — swallow rather than turn a retry into the failure.
      await page
        .evaluate((s) => document.querySelector(s)?.click(), sel)
        .catch(() => {});
    });
}

// Nothing in the trailer sits still: during a held beat the cursor drifts, so
// the compositor keeps producing frames and the shot carries motion.
async function holdWithDrift(ms) {
  await inject();
  const size = await page
    .evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }))
    .catch(() => ({ w: 1200, h: 800 }));
  const start = Date.now();
  while (Date.now() - start < ms) {
    const phase = ((Date.now() - start) / Math.max(ms, 1)) * Math.PI * 2;
    await moveCursorTo(
      Math.round(size.w * (0.5 + 0.17 * Math.cos(phase))),
      Math.round(size.h * (0.6 + 0.11 * Math.sin(phase))),
    );
    await sleep(480);
  }
}

async function still(name) {
  try {
    await page.screenshot({ path: path.join(OUT, `still-${name}.png`) });
  } catch {
    /* ignore */
  }
}

async function transport(method, apiPath) {
  const raw = await page.evaluate(
    async ({ m, p }) => {
      try {
        const r = await window.bridge.ipc.invoke("transport.request", {
          method: m,
          path: p,
        });
        if (r && r.kind === "transport-result") {
          if (!r.ok)
            return `ERR:HTTP ${String(r.error?.status ?? "unknown")} ${String(
              r.error?.message ?? "request failed",
            )}`;
          return JSON.stringify(r.value);
        }
        return JSON.stringify(r);
      } catch (e) {
        return `ERR:${e?.message ?? e}`;
      }
    },
    { m: method, p: apiPath },
  );
  if (typeof raw === "string" && raw.startsWith("ERR:")) {
    throw new Error(`${method} ${apiPath} -> ${raw}`);
  }
  return JSON.parse(raw);
}

async function waitModelPillResolved(timeoutMs = 30_000) {
  const unresolved = new Set(["", "model", "select a model"]);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await has(".atlas-model-pill"))) return true;
    const text = String(
      (await page
        .evaluate(
          () =>
            (document.querySelector(".atlas-model-pill") || {}).innerText || "",
        )
        .catch(() => "")) || "",
    ).trim();
    if (!unresolved.has(text.toLowerCase())) return true;
    await sleep(500);
  }
  return false;
}

async function selectModel(fragment, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await showClick(".atlas-model-pill", { settle: 400 });
    if (await waitFor(".atlas-model-pill__item", 5_000)) {
      const clicked = await page.evaluate((want) => {
        const rows = [...document.querySelectorAll(".atlas-model-pill__item")];
        const row = rows.find((r) => {
          const nm = r.querySelector(".atlas-model-pill__nm");
          return (
            nm &&
            nm.innerText.toLowerCase().includes(want.toLowerCase()) &&
            !r.disabled
          );
        });
        if (!row) return null;
        row.click();
        return row.innerText;
      }, fragment);
      if (clicked) return true;
    }
    await page.evaluate(() => document.body.click()).catch(() => {});
    await sleep(900);
  }
  return false;
}

// ── the sequence ─────────────────────────────────────────────────────────────
const report = {
  provider,
  goal: GOAL,
  appDir: APP_DIR,
  runtimeDir: COPILOT_HOME,
  userDataSubdir: USER_SUBDIR,
  window: windowSize,
};
let failure = null;

try {
  mark("boot");
  if (!(await waitFor('[data-testid="sign-in-gate"]', BOOT_TIMEOUT_MS))) {
    throw new Error("sign-in gate never appeared");
  }
  mark("login_ready");
  await inject();
  await holdWithDrift(2600);
  await still("01-login");

  // 1 — sign in (the no-account device session)
  await showClick('[data-testid="sign-in-button"]');
  if (!(await waitGone('[data-testid="sign-in-gate"]', 120_000))) {
    throw new Error("sign-in gate never dropped");
  }
  mark("signed_in");
  await inject();
  await holdWithDrift(1800);
  await still("02-signed-in");

  // 2 — FTUE: add a real provider key
  if (!(await waitFor('[data-testid="first-run-add-key"]', 240_000))) {
    throw new Error("FTUE key card never appeared");
  }
  await showClick('[data-testid="first-run-add-key"]');
  if (!(await waitFor('[data-testid="first-run-keyform"]', 60_000))) {
    throw new Error("FTUE key form never appeared");
  }
  await still("03-keyform");
  await showClick(`[role=radio]:has-text("${PROVIDER_LABEL[provider]}")`, {
    settle: 450,
  });
  await sleep(400);
  // The field is type="password": what the camera sees is dots, never the key.
  await page.locator('[data-testid="first-run-key-input"]').last().click();
  await page.keyboard.type(BYOK, { delay: 8 });
  await sleep(500);
  await showClick('[data-testid="first-run-key-connect"]');
  if (!(await waitFor('[data-testid="first-run-composer"]', 120_000))) {
    throw new Error("key connect did not reveal the composer");
  }
  await waitModelPillResolved();
  if (MODEL) {
    const picked = await selectModel(MODEL);
    log(`model select "${MODEL}" -> ${picked ? "ok" : "not found"}`);
  }
  report.model =
    (await page
      .evaluate(
        () =>
          (document.querySelector(".atlas-model-pill") || {}).innerText || null,
      )
      .catch(() => null)) || null;
  mark("key_added");
  log(`model=${JSON.stringify(report.model)}`);
  await inject();
  await holdWithDrift(1800);
  await still("04-key-added");

  // 3 — type the goal, live
  await page.locator('[data-testid="composer-textarea"]').last().click();
  await page.keyboard.type(GOAL, { delay: TYPE_DELAY });
  await sleep(700);
  await still("05-goal-typed");
  await showClick('button[aria-label="Send message"]', { settle: 600 });
  mark("goal_sent");

  // 4 — the run
  let conversationId = null;
  const convoDeadline = Date.now() + 90_000;
  while (Date.now() < convoDeadline && conversationId === null) {
    const hash = String(
      (await page.evaluate(() => window.location.hash).catch(() => "")) || "",
    );
    const m = /^#\/convo\/([^/?#]+)/.exec(hash);
    if (m) conversationId = m[1];
    else await sleep(400);
  }
  if (!conversationId) throw new Error("the goal never bound a conversation");
  report.conversationId = conversationId;
  log(`conversation=${conversationId}`);

  let runId = null;
  const runDeadline = Date.now() + 120_000;
  while (Date.now() < runDeadline && runId === null) {
    try {
      const listing = await transport(
        "GET",
        `/v1/agent/conversations/${conversationId}/runs`,
      );
      const runs = Array.isArray(listing?.runs) ? listing.runs : [];
      if (runs.length > 0 && typeof runs[0]?.run_id === "string") {
        runId = runs[0].run_id;
      }
    } catch {
      /* the facade may still be settling */
    }
    if (!runId) await sleep(700);
  }
  if (!runId) throw new Error("no run was created for the goal");
  report.runId = runId;
  mark("running");
  log(`run=${runId}`);

  // 5 — hold on the live run until it seals AND the canvas presents the table
  const TERMINAL = new Set([
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "timed_out",
  ]);
  const deadline = Date.now() + RUN_TIMEOUT_MS;
  let status = null;
  let canvasShown = false;
  let lastLogged = 0;
  let terminalAt = 0;
  while (Date.now() < deadline) {
    if (
      !canvasShown &&
      (await has('[data-testid="artifact-dataset-renderer"]'))
    ) {
      canvasShown = true;
      mark("canvas_shown");
      await still("06-canvas-shown");
    }
    if (status === null || !TERMINAL.has(status)) {
      try {
        const detail = await transport("GET", `/v1/agent/runs/${runId}`);
        status = detail?.status ?? status;
        report.safeError = detail?.safe_error ?? null;
      } catch {
        /* transient */
      }
      if (TERMINAL.has(status) && markers.run_terminal === undefined) {
        mark("run_terminal");
        terminalAt = Date.now();
        log(`run status=${status}`);
      }
    }
    if (canvasShown && TERMINAL.has(status)) break;
    // The run is sealed but the canvas has not presented. That is exactly the
    // regression g2b guards, so give it a bounded grace period and then stop
    // waiting — a failed run gets a much shorter one.
    if (terminalAt > 0) {
      const grace = status === "completed" ? 90_000 : 20_000;
      if (Date.now() - terminalAt > grace) break;
    }
    if (Date.now() - lastLogged > 15_000) {
      lastLogged = Date.now();
      log(
        `… status=${status} canvas=${canvasShown} frames=${frames} t=${(
          (Date.now() - t0) /
          1000
        ).toFixed(1)}s`,
      );
    }
    await sleep(600);
  }
  report.status = status;
  report.canvasShown = canvasShown;

  // 6 — the hero hold: the table is simply there
  if (canvasShown) {
    await inject();
    const box = await page
      .locator('[data-testid="artifact-dataset-renderer"]')
      .last()
      .boundingBox()
      .catch(() => null);
    if (box) {
      await moveCursorTo(
        Math.round(box.x + box.width * 0.35),
        Math.round(box.y + box.height * 0.4),
      );
      await sleep(900);
    }
    await holdWithDrift(HOLD_CANVAS_MS);
    await still("07-canvas-hold");
  } else {
    // Partial footage still beats none: hold on whatever the run left on screen.
    await holdWithDrift(Math.min(HOLD_CANVAS_MS, 6000));
    await still("07-no-canvas");
  }
  mark("end");
} catch (err) {
  failure = err?.stack || String(err);
  log(`FAILED: ${err?.message ?? err}`);
  await still("99-failure");
  if (markers.end === undefined) mark("end");
}

// ── evidence: what the run really produced ───────────────────────────────────
try {
  if (report.runId) {
    const replay = await transport(
      "GET",
      `/v1/agent/runs/${report.runId}/events`,
    );
    const events = Array.isArray(replay?.events) ? replay.events : [];
    const census = {};
    for (const e of events) {
      const name = String(e?.event_type ?? "unknown");
      census[name] = (census[name] ?? 0) + 1;
    }
    report.eventCensus = census;
    report.eventCount = events.length;
    const artifact = events
      .filter((e) => e?.event_type === "artifact.created")
      .map((e) => ({
        artifactId: e?.payload?.artifact_id,
        revision: e?.payload?.revision,
        kind: e?.payload?.kind,
        sequenceNo: e?.sequence_no,
      }))
      .pop();
    if (artifact?.artifactId) {
      report.artifact = artifact;
      const detail = await transport(
        "GET",
        `/v1/agent/artifacts/${artifact.artifactId}`,
      ).catch(() => null);
      report.artifactTitle =
        detail?.artifact?.title ?? detail?.suggested_filename ?? null;
      // Read the immutable bytes through the app's own IPC so the film team has
      // the exact table text that is on screen.
      const b64 = await page
        .evaluate(
          async ({ artifactId, revision }) => {
            const opened = await window.bridge.ipc.invoke(
              "transport.artifact-content.open",
              { artifactId, revision },
            );
            const bytes = [];
            try {
              for (;;) {
                const next = await window.bridge.ipc.invoke(
                  "transport.artifact-content.read",
                  { handle: opened.handle },
                );
                if (next.done) break;
                if (next.chunk === null) break;
                for (const v of next.chunk) {
                  bytes.push(v);
                  if (bytes.length > 262144)
                    throw new Error("artifact too large");
                }
              }
            } finally {
              await window.bridge.ipc.invoke(
                "transport.artifact-content.close",
                {
                  handle: opened.handle,
                },
              );
            }
            let binary = "";
            for (const v of bytes) binary += String.fromCharCode(v);
            return btoa(binary);
          },
          { artifactId: artifact.artifactId, revision: artifact.revision ?? 1 },
        )
        .catch(() => null);
      if (typeof b64 === "string") {
        report.csv = Buffer.from(b64, "base64").toString("utf8");
      }
    }
  }
} catch (err) {
  report.evidenceError = String(err?.message ?? err);
}

// ── stop, persist, verify no secret leaked into our own output ───────────────
capturing = false;
try {
  await client.send("Page.stopScreencast");
} catch {
  /* ignore */
}

if (failure) report.failure = failure;
report.frames = frames;
report.droppedFrames = dropped;
report.markers = markers;

writeFileSync(
  path.join(OUT, "markers.json"),
  JSON.stringify(
    {
      markers,
      meta,
      frames,
      droppedFrames: dropped,
      window: windowSize,
      maxFps: MAX_FPS,
      startedAt: new Date(t0).toISOString(),
      failure,
    },
    null,
    2,
  ),
);
writeFileSync(
  path.join(OUT, "run-report.json"),
  JSON.stringify(report, null, 2),
);

// The key is typed into a masked field and never written by this script; prove
// it for the text artifacts we DO write rather than asserting it.
const needle = Buffer.from(BYOK, "utf8");
for (const name of readdirSync(OUT)) {
  const p = path.join(OUT, name);
  if (!/\.(json|log|txt|html)$/.test(name)) continue;
  try {
    if (readFileSync(p).includes(needle)) {
      log(`SECURITY: plaintext BYOK material found in ${name} — delete it`);
      process.exitCode = 1;
    }
  } catch {
    /* ignore */
  }
}

await app.close().catch(() => {});
log(
  `DONE frames=${frames} dropped=${dropped} status=${report.status ?? "n/a"} ` +
    `canvas=${report.canvasShown ?? false} markers=${JSON.stringify(markers)}`,
);
process.exit(failure ? 1 : 0);
