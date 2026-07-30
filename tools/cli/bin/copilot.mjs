#!/usr/bin/env node
// 0xCopilot launcher. `copilot` stages the self-contained desktop runtime and
// starts the Electron app; no DMG/installer, no Apple/Windows signing creds.
//
//   copilot            start (staging first if needed)
//   copilot start
//   copilot install    stage/refresh the runtime without launching
//   copilot doctor      diagnose the install
//   copilot repair      unblock a stuck launch (keeps data); --session also signs out
//   copilot uninstall   remove the staged runtime + app data
//   copilot help | version

import { existsSync, readFileSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  isPlatformSupported,
  PLATFORM,
  resolveElectronBinary,
  resolveRoots,
} from "../lib/paths.mjs";
import {
  isBrowserStaged,
  isMontyStaged,
  isStaged,
  stageRuntime,
} from "../lib/stage.mjs";
import { ensureAppBuilt, launchApp } from "../lib/launch.mjs";
import { ensureBrandedShell } from "../lib/mac-shell.mjs";
import { doctor } from "../lib/doctor.mjs";
import { repair } from "../lib/repair.mjs";
import { uninstall } from "../lib/uninstall.mjs";
import * as ui from "../lib/ui.mjs";

const PKG_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

function readVersion() {
  try {
    return JSON.parse(readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"))
      .version;
  } catch {
    return "0.0.0";
  }
}

function printHelp() {
  ui.banner();
  ui.plain(`  ${ui.c.bold("Usage")}  copilot [command]`);
  ui.plain();
  ui.plain(
    `  ${ui.c.cyan("(no command)")}   stage if needed, then start the app`,
  );
  ui.plain(`  ${ui.c.cyan("start")}          same as no command`);
  ui.plain(
    `  ${ui.c.cyan("install")}        download + stage the runtime, don't launch`,
  );
  ui.plain(
    `  ${ui.c.dim("                 add --browser to install optional browser automation")}`,
  );
  ui.plain(
    `  ${ui.c.dim("                 add --code-mode to install the optional code interpreter")}`,
  );
  ui.plain(
    `  ${ui.c.cyan("doctor")}         check the install and report problems`,
  );
  ui.plain(
    `  ${ui.c.cyan("repair")}         unblock a stuck launch (orphaned database / stale lock); keeps your data`,
  );
  ui.plain(
    `  ${ui.c.cyan("uninstall")}      remove the staged runtime + local app data`,
  );
  ui.plain(`  ${ui.c.cyan("help")}           show this help`);
  ui.plain(`  ${ui.c.cyan("version")}        print the CLI version`);
  ui.plain();
  ui.plain(
    `  ${ui.c.dim("Flags: --force (re-stage), --browser (optional Chromium), --code-mode (optional interpreter), --yes (skip prompts), --session (repair also clears sign-in)")}`,
  );
  ui.plain();
}

function requireSupportedPlatform() {
  if (isPlatformSupported()) return;
  ui.err(
    `0xCopilot desktop currently supports macOS and Windows (this host is ${PLATFORM}).`,
  );
  process.exit(1);
}

function browserRequestedByEnvironment() {
  const raw = process.env.RUNTIME_ENABLE_DESKTOP_BROWSER;
  return (
    typeof raw === "string" &&
    new Set(["1", "true", "yes", "on", "enabled"]).has(raw.trim().toLowerCase())
  );
}

function codeModeRequestedByEnvironment() {
  const raw = process.env.RUNTIME_ENABLE_MONTY;
  return (
    typeof raw === "string" &&
    new Set(["1", "true", "yes", "on", "enabled"]).has(raw.trim().toLowerCase())
  );
}

function installTimingsEnabled() {
  const raw = process.env.COPILOT_INSTALL_TIMINGS;
  return (
    typeof raw === "string" &&
    new Set(["1", "true", "yes", "on", "enabled"]).has(raw.trim().toLowerCase())
  );
}

function prepareDesktopShellInParallel() {
  const startedAt = performance.now();
  const script = path.join(PKG_ROOT, "lib", "prepare-shell.mjs");
  const child = spawn(process.execPath, [script], { stdio: "inherit" });
  const done = new Promise((resolve, reject) => {
    child.once("error", (error) => {
      reject(new Error(`could not prepare Electron: ${error.message}`));
    });
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve((performance.now() - startedAt) / 1000);
        return;
      }
      reject(
        new Error(
          `Electron preparation failed (exit ${code ?? "unknown"}${
            signal === null ? "" : `, signal ${signal}`
          })`,
        ),
      );
    });
  });
  return {
    done,
    cancel: () => {
      if (child.exitCode === null && !child.killed) child.kill("SIGTERM");
    },
  };
}

async function stageRuntimeWithShellPreparation(options) {
  const startedAt = performance.now();
  const shellPreparation = prepareDesktopShellInParallel();
  try {
    const runtimeStartedAt = performance.now();
    stageRuntime(options);
    const runtimeSeconds = (performance.now() - runtimeStartedAt) / 1000;
    const shellSeconds = await shellPreparation.done;
    if (installTimingsEnabled()) {
      ui.plain(`[install-timing] runtime-stage ${runtimeSeconds.toFixed(2)}s`);
      ui.plain(`[install-timing] electron-shell ${shellSeconds.toFixed(2)}s`);
      ui.plain(
        `[install-timing] concurrent-critical-path ${(
          (performance.now() - startedAt) /
          1000
        ).toFixed(2)}s`,
      );
    }
  } catch (error) {
    shellPreparation.cancel();
    try {
      await shellPreparation.done;
    } catch {
      // Preserve the staging/root cause; the companion task was cancelled.
    }
    throw error;
  }
}

async function cmdStart({ force, browser, codeMode }) {
  requireSupportedPlatform();
  const roots = resolveRoots(PKG_ROOT);
  ensureAppBuilt(roots);
  // Passing the version makes stageRuntime re-stage when the CLI was upgraded
  // since the runtime was last staged (new app bundle ↔ matching services).
  await stageRuntimeWithShellPreparation({
    stageScript: roots.stageScript,
    version: readVersion(),
    force,
    // Once explicitly installed, retain the optional browser across CLI
    // upgrades instead of silently dropping it during an automatic re-stage.
    includeBrowser:
      browser || browserRequestedByEnvironment() || isBrowserStaged(),
    includeMonty:
      codeMode || codeModeRequestedByEnvironment() || isMontyStaged(),
  });
  if (!isStaged()) {
    ui.err(
      "runtime is not staged — run `copilot install` and check `copilot doctor`.",
    );
    process.exit(1);
  }
  const electronBinary = resolveElectronBinary(roots.electronBases);
  // macOS: launch through a shell bundle carrying our name + icon so the Dock
  // doesn't present the app as "Electron". No-op elsewhere; falls back to the
  // stock binary on any failure.
  const launchBinary = ensureBrandedShell({
    electronBinary,
    appDir: roots.appDir,
  });
  const child = launchApp({
    electronBinary: launchBinary,
    appDir: roots.appDir,
    enableMonty: isMontyStaged(),
  });

  // Forward termination to the app; exit with its code.
  const forward = (sig) => {
    if (child.killed || child.exitCode !== null || !child.pid) return;
    if (process.platform === "win32") {
      // Windows has no POSIX signals: kill the whole tree so the embedded
      // postgres + python children aren't orphaned when the CLI is stopped.
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
      });
    } else {
      child.kill(sig);
    }
  };
  process.on("SIGINT", forward);
  process.on("SIGTERM", forward);
  child.on("error", (err) => {
    ui.err(`failed to launch: ${err.message}`);
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    process.exit(signal ? 1 : (code ?? 0));
  });
}

async function cmdInstall({ browser, codeMode }) {
  requireSupportedPlatform();
  ui.banner();
  const roots = resolveRoots(PKG_ROOT);
  // install always re-runs staging (stamps make a warm run cheap); it's the
  // explicit "make sure everything is present + signed" entry point.
  await stageRuntimeWithShellPreparation({
    stageScript: roots.stageScript,
    version: readVersion(),
    force: true,
    includeBrowser: browser || isBrowserStaged(),
    includeMonty: codeMode || isMontyStaged(),
  });
  ui.ok("ready — run `copilot` to start the app");
}

async function main() {
  const argv = process.argv.slice(2);
  const flags = new Set(argv.filter((a) => a.startsWith("-")));
  const positional = argv.filter((a) => !a.startsWith("-"));
  const force = flags.has("--force") || flags.has("-f");
  const yes = flags.has("--yes") || flags.has("-y");
  const session = flags.has("--session");
  const browser = flags.has("--browser");
  const codeMode = flags.has("--code-mode");

  const KNOWN_FLAGS = new Set([
    "--force",
    "-f",
    "--yes",
    "-y",
    "--session",
    "--browser",
    "--code-mode",
    "--help",
    "-h",
    "--version",
    "-v",
  ]);
  const unknown = [...flags].filter((f) => !KNOWN_FLAGS.has(f));
  if (unknown.length > 0) {
    ui.err(`unknown option: ${unknown.join(", ")}`);
    printHelp();
    process.exit(1);
  }

  // Help/version work as flags too (`copilot --version`), so resolve them
  // before falling back to the default "start" command.
  if (flags.has("--help") || flags.has("-h")) {
    printHelp();
    return;
  }
  if (flags.has("--version") || flags.has("-v")) {
    ui.plain(readVersion());
    return;
  }
  const command = positional[0] ?? "start";

  switch (command) {
    case "start":
      await cmdStart({ force, browser, codeMode });
      break;
    case "install":
      await cmdInstall({ browser, codeMode });
      break;
    case "doctor":
      process.exit(doctor(PKG_ROOT) ? 0 : 1);
      break;
    case "repair":
      process.exit((await repair({ yes, session })) ? 0 : 1);
      break;
    case "uninstall":
      process.exit((await uninstall({ yes })) ? 0 : 1);
      break;
    case "help":
      printHelp();
      break;
    case "version":
      ui.plain(readVersion());
      break;
    default:
      ui.err(`unknown command: ${command}`);
      printHelp();
      process.exit(1);
  }
}

main().catch((err) => {
  ui.err(err?.message ?? String(err));
  process.exit(1);
});
