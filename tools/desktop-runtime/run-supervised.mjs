#!/usr/bin/env node
/**
 * run-supervised.mjs — ONE command to build-from-source and run the REAL
 * supervised desktop app in production posture. Codifies the hand-assembly the
 * README's "Service supervisor" section otherwise spells out step by step:
 *
 *   node tools/desktop-runtime/run-supervised.mjs
 *   # or: make desktop-supervised
 *
 * What it does, in order:
 *   1. detect the host platform/arch and validate a runtime bundle exists for it
 *      (manifest.json ships darwin-arm64, darwin-x64, win32-x64 only);
 *   2. STAGE the self-contained runtime (python + postgres + the three python
 *      services) for the host via tools/desktop-runtime/stage.mjs — idempotent,
 *      so a warm re-run stamp-skips the expensive pip installs and only refreshes
 *      the cheap source copy. On macOS it stages with --adhoc-sign (credential-
 *      free signing; Apple Silicon refuses to exec an unsigned arm64 mach-o), the
 *      same finalize the proven run-local.mjs drill and the `copilot` CLI use;
 *   3. BUILD + LAUNCH the Electron shell against that staged runtime by delegating
 *      to `npm run dev --workspace @0x-copilot/desktop` (which is
 *      `npm run build && ELECTRON_RUN_AS_NODE= electron .`) with COPILOT_RUNTIME_DIR
 *      set to the staged dest. That env var flips main/services/boot-mode.ts
 *      #shouldSupervise ON, so the app boots embedded postgres + all three
 *      services itself under the single_user_desktop PRODUCTION posture
 *      (main/posture.ts#isProductionPosture → real SIWE/Google sign-in, no dev
 *      IdP). This is the GUI counterpart to run-local.mjs, which boots the same
 *      backend topology headlessly but never opens the Electron shell.
 *
 * When to use which:
 *   - this script / `make desktop-supervised` — the REAL supervised desktop app
 *     (embedded postgres + 3 services + Electron GUI) from source, production
 *     posture. The from-source dev-loop equivalent of the published `copilot` CLI.
 *   - `npm run dev --workspace @0x-copilot/desktop` — Electron shell ONLY against
 *     MockTransport (or COPILOT_FACADE_URL); no supervisor, no embedded postgres.
 *   - `node tools/desktop-runtime/run-local.mjs` — the supervised BACKEND stack
 *     headlessly (no Electron GUI) + a hermetic run→stream smoke; the CI drill.
 *
 * Flags:
 *   --skip-stage        skip staging (fast path when only main/renderer changed
 *                       and the runtime is already staged for this host).
 *   --no-adhoc-sign     stage without --adhoc-sign on macOS (default: sign).
 *   --dest <dir>        staging dest (default apps/desktop/resources); becomes
 *                       COPILOT_RUNTIME_DIR. Must match what you staged.
 *   -h, --help          print this and exit.
 *
 * Zero non-builtin node deps. Reuses stage.mjs + the desktop `dev` npm script;
 * it does not reinvent staging or the Electron launch.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const STAGE_SCRIPT = path.join(HERE, "stage.mjs");
const MANIFEST_PATH = path.join(HERE, "manifest.json");

function log(msg) {
  process.stdout.write(`[run-supervised] ${msg}\n`);
}

function fail(msg) {
  process.stderr.write(`[run-supervised] ERROR: ${msg}\n`);
  process.exit(1);
}

function printHelp() {
  process.stdout.write(
    [
      "run-supervised.mjs — build-from-source + run the REAL supervised desktop app.",
      "",
      "  node tools/desktop-runtime/run-supervised.mjs [flags]",
      "  make desktop-supervised",
      "",
      "Stages the host runtime (python + postgres + 3 services), then builds and",
      "launches the Electron shell against it with COPILOT_RUNTIME_DIR set, so the",
      "app supervises embedded postgres + all three services in production posture.",
      "",
      "Flags:",
      "  --skip-stage       skip staging (runtime already staged for this host)",
      "  --no-adhoc-sign    stage without --adhoc-sign on macOS (default: sign)",
      "  --dest <dir>       staging dest (default apps/desktop/resources)",
      "  -h, --help         show this help",
      "",
    ].join("\n"),
  );
}

function parseArgs(argv) {
  const args = {
    skipStage: false,
    adhocSign: true,
    dest: path.join(REPO_ROOT, "apps", "desktop", "resources"),
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--skip-stage") args.skipStage = true;
    else if (a === "--no-adhoc-sign") args.adhocSign = false;
    else if (a === "--dest") args.dest = path.resolve(argv[++i]);
    else if (a === "-h" || a === "--help") {
      printHelp();
      process.exit(0);
    } else fail(`unknown argument ${a}`);
  }
  return args;
}

/** darwin/win32 only; site-packages are host-specific so we run host-arch only. */
function resolveHostTarget() {
  const platform = process.platform;
  const arch = process.arch;
  const key = `${platform}-${arch}`;
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  const supported = Object.keys(manifest.python.platforms);
  if (!supported.includes(key)) {
    fail(
      `no staged runtime bundle for this host (${key}).\n` +
        `  Supported hosts: ${supported.join(", ")}.\n` +
        `  Linux has no bundle — use \`make dev\` for the non-supervised local stack.`,
    );
  }
  return { platform, arch, key };
}

/** True when a host-executable runtime is already staged at dest for this host. */
function isStagedFresh(dest, key) {
  const manifestPath = path.join(dest, "runtime", key, "staging-manifest.json");
  if (!fs.existsSync(manifestPath)) return false;
  try {
    const staged = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    return staged.host_exec === true;
  } catch {
    return false;
  }
}

function stage({ platform, arch, dest, adhocSign }) {
  const npmDefault = path.join(REPO_ROOT, "apps", "desktop", "resources");
  const stageArgs = [STAGE_SCRIPT, "--platform", platform, "--arch", arch];
  if (dest !== npmDefault) stageArgs.push("--dest", dest);
  // Apple Silicon refuses to execute an UNSIGNED arm64 mach-o; ad-hoc signing
  // (identity "-") needs no Apple credentials and is a no-op off darwin. This is
  // what makes the supervised stack boot reliably from a fresh source stage.
  if (adhocSign && platform === "darwin") stageArgs.push("--adhoc-sign");
  log(`staging ${platform}-${arch} -> ${path.relative(REPO_ROOT, dest)}`);
  const res = spawnSync(process.execPath, stageArgs, {
    stdio: "inherit",
    cwd: REPO_ROOT,
  });
  if (res.error) fail(`stage.mjs failed to launch: ${res.error.message}`);
  if (res.status !== 0) fail(`stage.mjs exited with status ${res.status}`);
}

/**
 * Build + launch the Electron shell via the desktop `dev` npm script. `dev` is
 * `npm run build && ELECTRON_RUN_AS_NODE= electron .`, so it rebuilds the bundle
 * (esbuild, sub-second) and opens the window. COPILOT_RUNTIME_DIR = <dest> flips
 * shouldSupervise() on; the supervisor + production posture follow from that one
 * signal (no COPILOT_PRODUCTION needed — see main/posture.ts). Signals are
 * forwarded and the child's exit code is propagated.
 */
function buildAndLaunch(dest) {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const env = { ...process.env, COPILOT_RUNTIME_DIR: dest };
  log(`COPILOT_RUNTIME_DIR=${dest}`);
  log("building + launching the Electron shell (npm run dev)…");
  const child = spawn(
    npm,
    ["run", "dev", "--workspace", "@0x-copilot/desktop"],
    { stdio: "inherit", cwd: REPO_ROOT, env },
  );

  const forward = (sig) => {
    if (!child.killed) child.kill(sig);
  };
  process.on("SIGINT", () => forward("SIGINT"));
  process.on("SIGTERM", () => forward("SIGTERM"));

  child.on("error", (err) => fail(`failed to launch: ${err.message}`));
  child.on("exit", (code, signal) => {
    if (signal) {
      log(`Electron exited on ${signal}`);
      process.exit(1);
    }
    process.exit(code ?? 0);
  });
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const { platform, arch, key } = resolveHostTarget();
  log(`host ${key}`);

  if (args.skipStage) {
    if (!isStagedFresh(args.dest, key)) {
      fail(
        `--skip-stage given but no host-executable runtime is staged at ` +
          `${path.join(args.dest, "runtime", key)}. ` +
          `Run once without --skip-stage.`,
      );
    }
    log("--skip-stage: reusing the already-staged runtime");
  } else {
    if (isStagedFresh(args.dest, key)) {
      log("runtime already staged for this host — re-staging (idempotent)");
    }
    stage({ platform, arch, dest: args.dest, adhocSign: args.adhocSign });
  }

  buildAndLaunch(args.dest);
}

main();
