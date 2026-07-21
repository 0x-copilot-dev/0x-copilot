#!/usr/bin/env node
// Journey: "Use locally, no account" → the device account (D-series decisions).
//
// Drives the REAL supervised app (embedded PG + the three services from the
// staged runtime) through the driver's control API and proves, live:
//   1. the SignInGate shows all three options,
//   2. "Use locally" mints the device account and lands in the workspace
//      (host-token mint — PR #166/#167, never the dev IdP),
//   3. Settings → Profile shows the HONEST device identity (D3): "This
//      device" anchor, "Signed in on this device", no @local.invalid leak,
//   4. both link CTAs are offered (wallet + Google),
//   5. re-entry: sign out → "Use locally" again → back in the workspace
//      (the same-account guarantee, D4-A, is a server-side singleton — its
//      arbitration is covered by the backend live gate; here we prove the
//      full round-trip works).
//
// Hermetic: runs in its own userData subdir (COPILOT_DESKTOP_USER_DATA_SUBDIR)
// so it never touches a real install's data. Prereqs: the staged runtime
// (node tools/desktop-runtime/stage.mjs) and `npm install` in tools/cli-testing.
//
//   COPILOT_HOME=<dir containing runtime/<platform>-<arch>> \
//     node harness/journeys/local-account.mjs

import { spawn } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.CTL_PORT ?? "8791");
const RUN_DIR = path.join(
  HERE,
  "..",
  "..",
  "runs",
  new Date().toISOString().replace(/[:.]/g, "-") + "-local-account",
);
const SUBDIR = `journey-local-${Date.now()}`;
const BOOT_TIMEOUT_MS = 240_000; // embedded PG init + migrations on first boot
const STEP_TIMEOUT_MS = 30_000;

const report = [];
function log(line) {
  console.log(line);
  report.push(line);
}

async function rpc(cmd, args = {}) {
  // The driver's wire shape is FLAT: {cmd, ...args} (it passes the whole
  // payload as the handler's args — see driver.mjs rpc(payload.cmd, payload)).
  const res = await fetch(`http://127.0.0.1:${PORT}/rpc`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ cmd, ...args }),
  });
  const body = await res.json();
  if (body.ok === false) throw new Error(`${cmd} failed: ${body.error}`);
  return body;
}

async function waitFor(selector, timeout = STEP_TIMEOUT_MS) {
  return rpc("waitFor", { selector, timeoutMs: timeout });
}

let shotIndex = 0;
async function shot(name) {
  shotIndex += 1;
  const file = `${String(shotIndex).padStart(2, "0")}-${name}`;
  // The driver writes into RUN_DIR/screenshots/<name>.png itself.
  await rpc("screenshot", { name: file });
  log(`  screenshot: ${file}.png`);
}

async function evalJs(js) {
  const r = await rpc("pageEval", { js });
  return r.value;
}

async function waitForSignInGate() {
  const start = Date.now();
  for (;;) {
    if (Date.now() - start > BOOT_TIMEOUT_MS) {
      throw new Error("supervised boot did not reach the sign-in gate");
    }
    try {
      await rpc("waitFor", {
        selector: '[data-testid="sign-in-button"]',
        timeoutMs: 5_000,
      });
      return;
    } catch {
      /* still booting — first boot runs initdb + migrations */
    }
  }
}

async function main() {
  mkdirSync(RUN_DIR, { recursive: true });
  log(
    `# Journey: local account (device account) — ${new Date().toISOString()}`,
  );
  log(`userData subdir: ${SUBDIR} (hermetic; deleted on success)`);

  const driver = spawn(
    process.execPath,
    [path.join(HERE, "..", "driver.mjs")],
    {
      env: {
        ...process.env,
        CTL_PORT: String(PORT),
        POSTURE: "prod",
        RUN_DIR,
        COPILOT_DESKTOP_USER_DATA_SUBDIR: SUBDIR,
      },
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  const ready = new Promise((resolve, reject) => {
    driver.stdout.on("data", (chunk) => {
      process.stdout.write(chunk);
      if (String(chunk).includes("DRIVER_READY")) resolve(undefined);
    });
    driver.on("exit", (code) =>
      reject(new Error(`driver exited early (${code})`)),
    );
  });

  let failed = false;
  try {
    await ready;

    log("## 1. Sign-in gate shows all three options");
    await waitForSignInGate();
    await waitFor('[data-testid="sign-in-wallet-button"]');
    await waitFor('[data-testid="sign-in-google-button"]');
    await shot("sign-in-gate");
    log("  PASS: wallet + google + local options present");

    log('## 2. "Use locally" mints the device account');
    await rpc("click", { selector: '[data-testid="sign-in-button"]' });
    await waitFor('[data-testid="destination-outlet"]', 60_000);
    await shot("workspace");
    log("  PASS: workspace loaded from the local mint");

    log("## 3. Settings → Profile is HONEST about the device account (D3)");
    await rpc("click", { selector: '[aria-label="Settings"]' });
    await waitFor('[data-testid="profile-device-anchor"]');
    const anchor = await evalJs(
      `document.querySelector('[data-testid="profile-device-anchor"]').value`,
    );
    if (anchor !== "This device") {
      throw new Error(`device anchor reads ${JSON.stringify(anchor)}`);
    }
    const leaks = await evalJs(
      `document.body.innerHTML.includes("local.invalid")`,
    );
    if (leaks === true) {
      throw new Error("placeholder email leaked into the profile DOM");
    }
    const labeled = await evalJs(
      `document.body.innerText.includes("Signed in on this device")`,
    );
    if (labeled !== true) {
      throw new Error('missing "Signed in on this device" label');
    }
    await shot("profile-device");
    log("  PASS: 'This device' anchor, no placeholder leak, honest label");

    log("## 4. Both link CTAs offered (wallet + Google)");
    await waitFor('[data-testid="profile-link-wallet"]');
    await waitFor('[data-testid="profile-link-google"]');
    await shot("link-ctas");
    log("  PASS: link-wallet + link-google CTAs present");

    log("## 5. Sign out → 'Use locally' re-enters the workspace (D4-A)");
    await rpc("click", { selector: '[data-testid="profile-signout"]' });
    await waitFor('[data-testid="sign-in-button"]');
    await rpc("click", { selector: '[data-testid="sign-in-button"]' });
    await waitFor('[data-testid="destination-outlet"]', 60_000);
    await shot("re-entry");
    log("  PASS: re-entry reached the workspace");

    log("\nRESULT: PASS");
  } catch (err) {
    failed = true;
    log(`\nRESULT: FAIL — ${err instanceof Error ? err.message : err}`);
    try {
      await shot("failure");
    } catch {
      /* window may be gone */
    }
    process.exitCode = 1;
  } finally {
    try {
      await rpc("quit", {});
    } catch {
      /* driver may already be down */
    }
    driver.kill();
    writeFileSync(path.join(RUN_DIR, "REPORT.md"), report.join("\n") + "\n");
    log(`report: ${path.relative(process.cwd(), RUN_DIR)}/REPORT.md`);
    if (!failed) {
      const ud = path.join(
        process.env.HOME ?? "",
        "Library",
        "Application Support",
        "0xCopilot",
        SUBDIR,
      );
      rmSync(ud, { recursive: true, force: true });
    }
  }
}

await main();
