// Launch the Electron app. Setting COPILOT_RUNTIME_DIR flips the app's
// supervisor on (main/services/boot-mode.ts#shouldSupervise) so it boots the
// staged runtime itself — the CLI does not orchestrate postgres/services.

import { existsSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";

import { appMainEntry, RUNTIME_DEST } from "./paths.mjs";
import * as ui from "./ui.mjs";

/**
 * Ensure the app's bundled JS exists. Published payloads ship it prebuilt; a
 * dev checkout may need `npm run build` first.
 */
export function ensureAppBuilt({ appDir, mode, repoRoot }) {
  if (existsSync(appMainEntry(appDir))) return;
  if (mode !== "dev") {
    throw new Error(
      `the app bundle is missing at ${appMainEntry(appDir)} — reinstall the CLI.`,
    );
  }
  ui.step("building the desktop app (dev checkout, first run)…");
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

  ui.step("starting 0xCopilot…");
  const child = spawn(electronBinary, [appDir], { stdio: "inherit", env });
  return child;
}
