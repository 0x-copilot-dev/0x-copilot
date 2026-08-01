// Live-smoke driver for the 0xCopilot desktop app.
//
// Launches the REAL Electron app the same way `copilot start` does (same
// electron binary, same appDir, same env: COPILOT_RUNTIME_DIR + production
// posture), but through Playwright's Electron driver so a test can click,
// screenshot, read the DOM/console, and intercept the system-browser handoff
// URLs (wallet / Google sign-in). A tiny localhost HTTP control server keeps
// the app alive across steps — each step is a `curl` to /rpc.
//
// This is a TEST HARNESS, not app code. It deliberately intercepts
// shell.openExternal so the sign-in browser handoff can be driven in a
// controlled Chrome (with Rabby) instead of the OS default browser.

import { createServer } from "node:http";
import {
  existsSync,
  mkdirSync,
  appendFileSync,
  writeFileSync,
  readFileSync,
} from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

import { _electron as electron } from "playwright";
import { resolveLaunchTarget } from "./launch-target.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const TARGET = resolveLaunchTarget({ repoRoot: REPO_ROOT });
const APP_DIR = TARGET.appDir;

const CTL_PORT = Number(process.env.CTL_PORT ?? "8790");
const POSTURE = process.env.POSTURE ?? "prod"; // "prod" | "dev"
const RUN_DIR = process.env.RUN_DIR ?? path.join(HERE, "..", "runs", "adhoc");
const SHOTS = path.join(RUN_DIR, "screenshots");
const LOGS = path.join(RUN_DIR, "logs");
for (const d of [SHOTS, LOGS]) mkdirSync(d, { recursive: true });

const MAIN_LOG = path.join(LOGS, `main-${POSTURE}.log`);
const CONSOLE_LOG = path.join(LOGS, `renderer-console-${POSTURE}.log`);

function logMain(line) {
  appendFileSync(MAIN_LOG, line.endsWith("\n") ? line : line + "\n");
}
function logConsole(line) {
  appendFileSync(CONSOLE_LOG, line.endsWith("\n") ? line : line + "\n");
}

// ---- resolve the electron binary exactly like the target CLI ----------------
function resolveElectron() {
  for (const base of TARGET.electronBases) {
    try {
      const require = createRequire(path.join(base, "index.js"));
      const resolved = require("electron");
      if (typeof resolved === "string" && existsSync(resolved)) return resolved;
    } catch {
      /* next */
    }
  }
  throw new Error(
    `could not resolve Electron for ${TARGET.kind} from ${TARGET.electronBases.join(", ")}`,
  );
}

// ---- build the launch env, mirroring tools/cli/lib/launch.mjs -------------
function config() {
  const p = path.join(HERE, "..", "run-config.local.json");
  if (existsSync(p)) {
    try {
      return JSON.parse(readFileSync(p, "utf8"));
    } catch {
      return {};
    }
  }
  return {};
}

function buildEnv() {
  const cfg = config();
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE; // window must open, don't run-as-node
  env.COPILOT_RUNTIME_DIR =
    process.env.COPILOT_HOME || path.join(os.homedir(), ".0xcopilot");
  if (POSTURE === "dev") {
    // Dev posture: exercise the dev-mint "Use locally, no account" path.
    env.COPILOT_DEV = "1";
    delete env.COPILOT_PRODUCTION;
    // A separate userData dir so the dev session never collides with prod.
    env.COPILOT_DESKTOP_USER_DATA_SUBDIR = "cli-test-dev";
  } else {
    env.COPILOT_PRODUCTION = "1"; // real install posture, like the CLI
  }
  // Google provider is only advertised when the facade child sees a client id.
  if (cfg.googleClientId && !env.GOOGLE_OAUTH_CLIENT_ID) {
    env.GOOGLE_OAUTH_CLIENT_ID = cfg.googleClientId;
  }
  return env;
}

// ---------------------------------------------------------------------------
// How long Playwright waits for Electron to come up. A FIRST boot on a fresh
// userData dir runs `initdb`, the migrations and three uvicorns before the
// window exists, and on a loaded machine that can outlast the 120s default —
// which surfaces as `electron.launch: Timeout exceeded` with a perfectly
// healthy supervisor log, i.e. a harness timeout wearing a product failure's
// clothes. Overridable so a slow host can be told the truth; the default is
// unchanged, so nothing that passes today starts waiting longer.
const LAUNCH_TIMEOUT_MS = Number(
  process.env.DRIVER_LAUNCH_TIMEOUT_MS || 120_000,
);

let app = null;
let page = null;
const capturedUrls = [];

async function launch() {
  const executablePath = resolveElectron();
  const env = buildEnv();
  logMain(
    `[driver] launching electron target=${TARGET.kind} posture=${POSTURE} port=${CTL_PORT}`,
  );
  logMain(`[driver] appDir=${APP_DIR}`);
  if (TARGET.cliPackageRoot) {
    logMain(`[driver] cliPackageRoot=${TARGET.cliPackageRoot}`);
  }
  logMain(`[driver] executablePath=${executablePath}`);
  logMain(`[driver] COPILOT_RUNTIME_DIR=${env.COPILOT_RUNTIME_DIR}`);
  logMain(
    `[driver] googleProvider=${env.GOOGLE_OAUTH_CLIENT_ID ? "configured" : "unset"}`,
  );

  app = await electron.launch({
    executablePath,
    args: [APP_DIR],
    cwd: REPO_ROOT,
    env,
    timeout: LAUNCH_TIMEOUT_MS,
  });

  // Pipe the electron main-process stdout/stderr (incl. supervisor + service
  // boot logs) to a file.
  const proc = app.process();
  proc.stdout?.on("data", (b) => logMain(`[out] ${b.toString().trimEnd()}`));
  proc.stderr?.on("data", (b) => logMain(`[err] ${b.toString().trimEnd()}`));

  // Intercept shell.openExternal in the MAIN process: record + suppress, so
  // the sign-in browser handoff can be driven in a controlled Chrome.
  await app.evaluate(async ({ shell }) => {
    const g = globalThis;
    g.__capturedUrls = g.__capturedUrls || [];
    const orig = shell.openExternal.bind(shell);
    g.__origOpenExternal = orig;
    shell.openExternal = async (url, opts) => {
      g.__capturedUrls.push({ url, at: Date.now() });
      return; // suppress the OS-browser open; the test drives Chrome itself
    };
    return true;
  });
  logMain("[driver] shell.openExternal intercept installed");

  page = await app.firstWindow({ timeout: LAUNCH_TIMEOUT_MS });
  wireWindow(page);
  logMain(`[driver] firstWindow url=${page.url()}`);

  // Track any additional windows.
  app.on("window", (w) => {
    logMain(`[driver] new window url=${w.url()}`);
  });
}

function wireWindow(p) {
  p.on("console", (msg) => {
    logConsole(`[${msg.type()}] ${msg.text()}`);
  });
  p.on("pageerror", (err) => {
    logConsole(`[pageerror] ${err?.message ?? err}`);
  });
  p.on("crash", () => logConsole("[crash] renderer crashed"));
}

// Return the most relevant window (last opened non-devtools).
function activePage() {
  if (!app) return page;
  const wins = app.windows();
  const real = wins.filter((w) => !w.url().startsWith("devtools://"));
  return real[real.length - 1] ?? page;
}

// ---------------------------------------------------------------------------
async function rpc(cmd, args) {
  const p = activePage();
  switch (cmd) {
    case "status": {
      const url = p ? p.url() : null;
      const title = p ? await p.title().catch(() => null) : null;
      const signInGate = p
        ? await p
            .evaluate(
              () => !!document.querySelector('[data-testid="sign-in-gate"]'),
            )
            .catch(() => false)
        : false;
      const bodyText = p
        ? await p
            .evaluate(() => document.body?.innerText?.slice(0, 4000) ?? "")
            .catch(() => "")
        : "";
      return {
        target: TARGET.kind,
        pid: app?.process()?.pid ?? null,
        appDir: APP_DIR,
        cliPackageRoot: TARGET.cliPackageRoot,
        posture: POSTURE,
        url,
        title,
        signInGate,
        windows: app?.windows().map((w) => w.url()),
        bodyText,
      };
    }
    case "screenshot": {
      const name = args.name ?? `shot-${Date.now()}`;
      const file = path.join(SHOTS, `${name}.png`);
      await p.screenshot({ path: file, fullPage: false });
      return { file };
    }
    case "click": {
      await p.click(args.selector, { timeout: args.timeoutMs ?? 15_000 });
      return { clicked: args.selector };
    }
    case "press": {
      if (args.selector) await p.focus(args.selector);
      await p.keyboard.press(args.key);
      return { pressed: args.key };
    }
    case "typeText": {
      if (args.selector) await p.focus(args.selector);
      await p.keyboard.type(args.text, { delay: args.delay ?? 10 });
      return { typed: args.text };
    }
    case "fill": {
      await p.fill(args.selector, args.value, {
        timeout: args.timeoutMs ?? 15_000,
      });
      return { filled: args.selector };
    }
    // `fill`/`click` above go through `page.fill`/`page.click`, which are
    // STRICT: two matches is an error, not a choice. The chat surface can
    // legitimately mount more than one composer (the cockpit's and a
    // destination's), and a journey driving "the one I am looking at" wants the
    // last one in document order rather than a failure.
    case "fillLast": {
      await p
        .locator(args.selector)
        .last()
        .fill(args.value, { timeout: args.timeoutMs ?? 15_000 });
      return { filled: args.selector };
    }
    case "clickLast": {
      await p
        .locator(args.selector)
        .last()
        .click({ timeout: args.timeoutMs ?? 15_000 });
      return { clicked: args.selector };
    }
    case "waitFor": {
      await p.waitForSelector(args.selector, {
        timeout: args.timeoutMs ?? 30_000,
        state: args.state ?? "visible",
      });
      return { found: args.selector };
    }
    case "text": {
      const t = await p.textContent(args.selector, {
        timeout: args.timeoutMs ?? 10_000,
      });
      return { text: t };
    }
    case "pageEval": {
      const val = await p.evaluate(args.js);
      return { value: val };
    }
    case "mainEval": {
      // Evaluate in the MAIN process. `args.js` is the source of a function
      // taking (electron, arg) — the same shape `resizeWindow` below uses,
      // just caller-supplied.
      //
      // Exists to stub NATIVE MODAL DIALOGS. A journey that has to open
      // `dialog.showOpenDialog` cannot proceed: the dialog is OS chrome, not
      // page content, so Playwright cannot see it, and driving it through
      // System Events needs Accessibility permission the runner does not have
      // (`osascript is not allowed assistive access (-25211)`, every attempt).
      // Stubbing the dialog is the standard Electron testing answer and is
      // HONEST here — the picker's only job is to return a path, and every
      // interesting step (realpath, the grant store, the IPC back to the
      // renderer, the bar re-render) still runs for real.
      // Wrapped in a real function rather than handed to Playwright as a
      // string: a bare string is evaluated as an EXPRESSION, so a function
      // source evaluates to the function and is never called — the probe that
      // caught this got `{ok:true}` with no value while every side effect
      // silently did not happen. `new Function` closes over nothing, so the
      // source still serializes into the main process intact.
      const fn = new Function(
        "electron",
        "arg",
        `return (${args.js})(electron, arg);`,
      );
      const val = await app.evaluate(fn, args.arg);
      return { value: val ?? null };
    }
    case "resizeWindow": {
      // Resize the REAL BrowserWindow (not a Playwright viewport override) so
      // window-size-dependent layout — internal scroll regions, the reserved
      // titlebar strip, anything sized off `vh` — is exercised exactly as it is
      // for a user who drags the window smaller. Sets the CONTENT size, so the
      // requested height is the renderer viewport height on every platform
      // regardless of the hidden-inset titlebar.
      const { width, height } = args;
      if (!Number.isFinite(width) || !Number.isFinite(height)) {
        throw new Error("resizeWindow needs numeric width and height");
      }
      const applied = await app.evaluate(
        async ({ BrowserWindow }, size) => {
          const win = BrowserWindow.getAllWindows().find(
            (w) => !w.isDestroyed(),
          );
          if (!win) throw new Error("no live BrowserWindow to resize");
          win.setContentSize(Math.round(size.width), Math.round(size.height));
          const [w, h] = win.getContentSize();
          return { width: w, height: h };
        },
        { width, height },
      );
      // Let the renderer settle on the new size before the caller measures.
      const viewport = await p
        .evaluate(
          () =>
            new Promise((resolve) => {
              requestAnimationFrame(() =>
                requestAnimationFrame(() =>
                  resolve({
                    innerWidth: window.innerWidth,
                    innerHeight: window.innerHeight,
                  }),
                ),
              );
            }),
        )
        .catch(() => null);
      return { requested: { width, height }, applied, viewport };
    }
    case "dumpDom": {
      const name = args.name ?? `dom-${Date.now()}`;
      const html = await p.content();
      const file = path.join(LOGS, `${name}.html`);
      writeFileSync(file, html);
      return { file, bytes: html.length };
    }
    case "openedUrls": {
      const urls = await app.evaluate(() => globalThis.__capturedUrls ?? []);
      return { urls };
    }
    case "openExternalReal": {
      // Actually open a URL via the original (un-intercepted) openExternal —
      // unused by default; the harness drives Chrome directly instead.
      await app.evaluate(
        async ({}, url) => globalThis.__origOpenExternal(url),
        args.url,
      );
      return { opened: args.url };
    }
    case "quit": {
      // Closing only this Node control process leaves its Electron child and
      // supervised desktop services orphaned. Close the real app first so its
      // normal shutdown path tears down the per-journey Postgres/services.
      await app?.close().catch(() => undefined);
      setTimeout(() => process.exit(0), 200);
      return { quitting: true };
    }
    default:
      throw new Error(`unknown cmd: ${cmd}`);
  }
}

function startServer() {
  const server = createServer((req, res) => {
    if (req.method !== "POST" || req.url !== "/rpc") {
      res.writeHead(404).end("use POST /rpc");
      return;
    }
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let payload;
      try {
        payload = JSON.parse(body || "{}");
      } catch (e) {
        res.writeHead(400).end(JSON.stringify({ error: "bad json" }));
        return;
      }
      try {
        const out = await rpc(payload.cmd, payload);
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, ...out }));
      } catch (e) {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(
          JSON.stringify({
            ok: false,
            error: e?.message ?? String(e),
            stack: e?.stack,
          }),
        );
      }
    });
  });
  server.listen(CTL_PORT, "127.0.0.1", () => {
    logMain(`[driver] control server on http://127.0.0.1:${CTL_PORT}/rpc`);
    console.log(`DRIVER_READY port=${CTL_PORT} posture=${POSTURE}`);
  });
}

launch()
  .then(() => startServer())
  .catch((e) => {
    logMain(`[driver] launch failed: ${e?.stack ?? e}`);
    console.error("DRIVER_LAUNCH_FAILED", e?.message ?? e);
    process.exit(1);
  });
