// Launch the Electron app. Setting COPILOT_RUNTIME_DIR flips the app's
// supervisor on (main/services/boot-mode.ts#shouldSupervise) so it boots the
// staged runtime itself — the CLI does not orchestrate postgres/services.

import { existsSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";

import { appMainEntry, RUNTIME_DEST } from "./paths.mjs";
import * as ui from "./ui.mjs";

/**
 * Ensure the app's bundled JS is present AND current.
 *
 * Published payloads ship a prebuilt bundle (never rebuilt here). A **dev
 * checkout** rebuilds on every start: the source is live and may have advanced
 * (a `git pull`, a new `main`) since `out/` was last built, and `out/` merely
 * *existing* does NOT mean it matches the checked-out source. Skipping the
 * rebuild there is the trap that silently runs stale renderer code — e.g. an old
 * sign-in / loading screen after the branch moved. esbuild is ~300ms, so the
 * cost is sub-second; correctness wins.
 */
export function ensureAppBuilt({ appDir, mode, repoRoot }) {
  const built = existsSync(appMainEntry(appDir));
  if (mode !== "dev") {
    if (built) return;
    throw new Error(
      `the app bundle is missing at ${appMainEntry(appDir)} — reinstall the CLI.`,
    );
  }
  ui.step(
    built
      ? "rebuilding the desktop app (dev checkout — keeping it in sync with source)…"
      : "building the desktop app (dev checkout, first run)…",
  );
  // On Windows npm is `npm.cmd`; Node's spawn only auto-resolves .exe from a
  // bare name, so pick the platform-correct binary.
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const res = spawnSync(
    npm,
    ["run", "build", "--workspace", "@0x-copilot/desktop"],
    { stdio: "inherit", cwd: repoRoot },
  );
  if (res.error) {
    throw new Error(`failed to run \`npm run build\`: ${res.error.message}`);
  }
  if (res.status !== 0) {
    throw new Error("failed to build the desktop app (`npm run build`)");
  }
  if (!existsSync(appMainEntry(appDir))) {
    throw new Error("app build completed but the entry is still missing");
  }
}

/**
 * Spawn Electron pointed at the app dir. Returns the child process. The caller
 * wires signal forwarding + exit propagation.
 */
export function launchApp({ electronBinary, appDir }) {
  const env = { ...process.env };
  // ELECTRON_RUN_AS_NODE=1 (set by some CI/agent harnesses) makes Electron
  // behave as plain Node — the window never opens. Force it off.
  delete env.ELECTRON_RUN_AS_NODE;
  // The supervisor reads its runtime tree from here.
  env.COPILOT_RUNTIME_DIR = RUNTIME_DEST;
  // A CLI launch spawns Electron against a directory, so `app.isPackaged` is
  // false even though this is a real end-user install. Flag production posture
  // explicitly so the app runs real sign-in and fails closed on stale sessions
  // (main/posture.ts#isProductionPosture) instead of dropping into dev-mint.
  env.COPILOT_PRODUCTION = "1";

  ui.step("starting 0xCopilot…");
  const child = spawn(electronBinary, [appDir], { stdio: "inherit", env });
  return child;
}
