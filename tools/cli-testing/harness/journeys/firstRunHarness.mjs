// Shared harness for the First-Run (FTUE) P7 verification journeys.
//
// The four FTUE journeys (J1 local-first, J2 BYOK, J4 skip, J5 returning) share
// the same shape: spawn the REAL supervised Electron app via the driver, drive
// it over the /rpc control API, assert on the FTUE's real testIds + verbatim
// copy, and write a REPORT.md. This module factors that boilerplate so each
// journey file is just the step sequence — mirroring `local-account.mjs`, but
// deduplicated across four drivers.
//
// It performs NO I/O against the services directly; every assertion is a DOM
// read through the driver. Each journey runs hermetically in its own userData
// subdir (COPILOT_DESKTOP_USER_DATA_SUBDIR) so it never touches a real install.
//
// This is a TEST HARNESS, not app code.

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const BOOT_TIMEOUT_MS = 240_000; // embedded PG init + migrations on first boot
const STEP_TIMEOUT_MS = 30_000;
const WORKSPACE_TIMEOUT_MS = 60_000;

// ---------------------------------------------------------------------------
// Real selectors + verbatim copy (single source of truth for all journeys).
//
// Sourced from the shipped FTUE:
//   • sign-in gate      apps/desktop/renderer/SignInGate.tsx
//   • first-run surface packages/chat-surface/src/onboarding/FirstRunSurface.tsx
//   • gate / key form   packages/chat-surface/src/onboarding/{Gate,KeyForm}.tsx
//   • composer / chips  packages/chat-surface/src/onboarding/{OnboardingComposer,SuggestionChips}.tsx
//   • shared composer   packages/chat-surface/src/composer/Composer.tsx
//   • acknowledgment    packages/chat-surface/src/onboarding/Acknowledgment.tsx
//   • run cockpit       packages/chat-surface/src/destinations/run/RunEmptyState.tsx
//   • workspace outlet  apps/desktop/renderer/DestinationOutlet.tsx
// ---------------------------------------------------------------------------

export const SEL = {
  // Sign-in gate
  signInGate: '[data-testid="sign-in-gate"]',
  signInLocal: '[data-testid="sign-in-button"]', // "Use locally, no account"
  signInWallet: '[data-testid="sign-in-wallet-button"]',
  signInGoogle: '[data-testid="sign-in-google-button"]',

  // First-run shell + state A (gate)
  firstRunLoading: '[data-testid="first-run-loading"]',
  firstRunSurface: '[data-testid="first-run-surface"]',
  firstRunBrand: '[data-testid="first-run-brand"]',
  firstRunWalletSlot: '[data-testid="first-run-wallet-slot"]',
  firstRunSkip: '[data-testid="first-run-skip"]',
  firstRunFooter: '[data-testid="first-run-footer"]',
  gate: '[data-testid="first-run-gate"]',
  localCard: '[data-testid="first-run-local-card"]',
  startDownload: '[data-testid="first-run-start-download"]',
  keyCard: '[data-testid="first-run-key-card"]',
  addKey: '[data-testid="first-run-add-key"]',

  // State A → KeyForm (BYOK)
  keyForm: '[data-testid="first-run-keyform"]',
  keyInput: '[data-testid="first-run-key-input"]',
  keyNote: '[data-testid="first-run-key-note"]',
  keyConnect: '[data-testid="first-run-key-connect"]',
  keyError: '[data-testid="first-run-key-error"]',

  // State B (composer)
  composer: '[data-testid="first-run-composer"]',
  composerH1: '[data-testid="first-run-composer-h1"]',
  chips: '[data-testid="first-run-chips"]',
  chipWatchWallet: '[data-testid="first-run-chip-watch-wallet"]',
  chipDraftThread: '[data-testid="first-run-chip-draft-thread"]',
  chipExplainCsv: '[data-testid="first-run-chip-explain-csv"]',
  composerTextarea: '[data-testid="composer-textarea"]',
  composerModelToggle: '[data-testid="composer-model-toggle"]',
  composerSend: '[data-testid="composer-send"]',
  composerError: '[data-testid="first-run-composer-error"]',

  // State C (acknowledgment)
  ack: '[data-testid="first-run-ack"]',
  ackTitle: '[data-testid="first-run-ack-title"]',
  ackError: '[data-testid="first-run-ack-error"]',

  // Workspace (post-handoff / returning)
  destinationOutlet: '[data-testid="destination-outlet"]',
  runEmptyState: '[data-testid="run-empty-state"]',
  runEmptySetupCta: '[data-testid="run-empty-setup-cta"]', // "Set up your model"
};

/** Verbatim copy the journeys assert on (FIRST_RUN_COPY / FIRST_RUN_ACK_TITLES). */
export const COPY = {
  gateH1: "First, give it a model.",
  localTitle: "Download the local model",
  startDownloadBtn: "Start download",
  keyTitle: "Bring your own key",
  addKeyBtn: "Add a key",
  composerH1: "What should we run first?",
  ackStarting: "Starting your first run",
  ackQueued: "Queued — starts when the model lands",
  modelPresetName: "Qwen 3 4B",
};

// ---------------------------------------------------------------------------
// Report — per-journey PASS / PARTIAL / FAIL bookkeeping + REPORT.md.
//
//   • PASS    — every asserted step held.
//   • PARTIAL — the asserted prefix held; the documented blocked tail was
//               skipped (needs a live model / real key / P4 tools). Exit 0.
//   • FAIL    — a step that SHOULD hold did not. Exit 1.
// ---------------------------------------------------------------------------

export function makeReport(runDir, title) {
  const lines = [];
  let blockedCount = 0;
  mkdirSync(runDir, { recursive: true });

  function log(line) {
    console.log(line);
    lines.push(line);
  }
  function pass(line) {
    log(`  PASS: ${line}`);
  }
  function blocked(reason) {
    blockedCount += 1;
    log(`  BLOCKED: ${reason}`);
  }
  function save(result) {
    const header = `# ${title} — ${new Date().toISOString()}\n\nRESULT: ${result}\n`;
    writeFileSync(
      path.join(runDir, "REPORT.md"),
      header + lines.join("\n") + "\n",
    );
    log(`report: ${path.relative(process.cwd(), runDir)}/REPORT.md`);
  }

  log(`# ${title} — ${new Date().toISOString()}`);
  return {
    log,
    pass,
    blocked,
    save,
    get blockedCount() {
      return blockedCount;
    },
  };
}

/** A dated, named run dir under tools/cli-testing/runs/. */
export function makeRunDir(name) {
  return path.join(
    HERE,
    "..",
    "..",
    "runs",
    new Date().toISOString().replace(/[:.]/g, "-") + `-${name}`,
  );
}

// ---------------------------------------------------------------------------
// Driver session — spawns driver.mjs and exposes rpc helpers bound to its port.
//
// J1/J2 spawn one session; J4/J5 spawn TWO sequentially (same userData subdir)
// to prove the persisted first-run flag survives a relaunch. `stop()` frees the
// control port so the next launch can reuse it.
// ---------------------------------------------------------------------------

export async function startDriver({ subdir, port, runDir, env = {} }) {
  const driver = spawn(
    process.execPath,
    [path.join(HERE, "..", "driver.mjs")],
    {
      env: {
        ...process.env,
        ...env,
        CTL_PORT: String(port),
        POSTURE: "prod",
        RUN_DIR: runDir,
        COPILOT_DESKTOP_USER_DATA_SUBDIR: subdir,
      },
      stdio: ["ignore", "pipe", "inherit"],
    },
  );

  await new Promise((resolve, reject) => {
    const onData = (chunk) => {
      process.stdout.write(chunk);
      if (String(chunk).includes("DRIVER_READY")) {
        driver.stdout.off("data", onData);
        resolve(undefined);
      }
    };
    driver.stdout.on("data", onData);
    driver.on("exit", (code) =>
      reject(new Error(`driver exited early (${code})`)),
    );
  });

  async function rpc(cmd, args = {}) {
    // Flat wire shape ({cmd, ...args}) — see driver.mjs rpc(payload.cmd, payload).
    const res = await fetch(`http://127.0.0.1:${port}/rpc`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cmd, ...args }),
    });
    const body = await res.json();
    if (body.ok === false) throw new Error(`${cmd} failed: ${body.error}`);
    return body;
  }

  const waitFor = (selector, timeout = STEP_TIMEOUT_MS) =>
    rpc("waitFor", { selector, timeoutMs: timeout });

  const evalJs = async (js) => (await rpc("pageEval", { js })).value;

  /** True when a selector is present in the DOM right now (no waiting). */
  const isPresent = (selector) =>
    evalJs(`!!document.querySelector(${JSON.stringify(selector)})`);

  let shotIndex = 0;
  async function shot(name) {
    shotIndex += 1;
    await rpc("screenshot", {
      name: `${String(shotIndex).padStart(2, "0")}-${name}`,
    });
  }

  /** Poll for the sign-in gate through the (long) first-boot window. */
  async function waitForSignIn() {
    const start = Date.now();
    for (;;) {
      if (Date.now() - start > BOOT_TIMEOUT_MS) {
        throw new Error("supervised boot did not reach the sign-in gate");
      }
      try {
        await rpc("waitFor", { selector: SEL.signInLocal, timeoutMs: 5_000 });
        return;
      } catch {
        /* still booting — first boot runs initdb + migrations */
      }
    }
  }

  async function stop() {
    try {
      await rpc("quit", {});
    } catch {
      /* driver may already be down */
    }
    driver.kill();
    await new Promise((resolve) => {
      if (driver.exitCode !== null) return resolve(undefined);
      driver.on("exit", () => resolve(undefined));
      setTimeout(() => resolve(undefined), 3_000);
    });
  }

  return {
    rpc,
    waitFor,
    evalJs,
    isPresent,
    shot,
    waitForSignIn,
    stop,
    ports: { control: port },
    timeouts: { step: STEP_TIMEOUT_MS, workspace: WORKSPACE_TIMEOUT_MS },
  };
}

/** Click "Use locally, no account" and settle on the FTUE gate or the shell. */
export async function signInLocal(session) {
  await session.rpc("click", { selector: SEL.signInLocal });
}
