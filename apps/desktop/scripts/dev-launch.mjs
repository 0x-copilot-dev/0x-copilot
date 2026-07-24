// Dev launcher for `npm run dev`: brand the macOS Dock as "0xCopilot".
//
// `electron .` boots node_modules' stock Electron.app, so the Dock shows the
// Electron atom labelled "Electron". app.setName / app.dock.setIcon fix the
// userData path + dock icon at runtime, but the Dock TOOLTIP is read from the
// launched bundle's Info.plist and cannot be changed from JS (see
// apps/desktop/main/branding.ts). This routes the dev launch through the SAME
// branded macOS shell the `copilot` CLI uses (tools/cli/lib/mac-shell.mjs): a
// copy-on-write clone of Electron.app whose Info.plist reads "0xCopilot",
// re-signed ad-hoc. The clone keeps CFBundleExecutable "Electron", so
// app.isPackaged stays false and the dev auth posture is untouched. On
// non-macOS — or if any shell step fails — ensureBrandedShell returns the stock
// binary, so this degrades to plain `electron .` (plainer branding, never a
// blocked launch). Precedent for apps/desktop reusing tools/*: the
// `stage:runtime` npm script already shells out to tools/desktop-runtime.
//
// Runs after `npm run build`; the branded shell needs out/main/icon.icns, which
// the build stages there (esbuild.config.mjs copyAssetsTask).

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { ensureBrandedShell } from "../../../tools/cli/lib/mac-shell.mjs";

const appDir = join(dirname(fileURLToPath(import.meta.url)), "..");
// electron's Node entrypoint exports the absolute path to its executable.
const electronBinary = createRequire(import.meta.url)("electron");

const launchBinary = ensureBrandedShell({ electronBinary, appDir });

// Mirror the previous `dev` script: clear ELECTRON_RUN_AS_NODE so the binary
// boots as Electron (not Node) even if the variable is inherited.
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(launchBinary, [appDir], { stdio: "inherit", env });
child.on("exit", (code, signal) => {
  if (signal !== null) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
