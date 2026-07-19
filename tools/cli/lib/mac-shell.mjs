// Branded macOS shell: launch the app from a bundle that IS 0xCopilot.
//
// The Dock takes an app's icon and name (the hover tooltip) from the .app
// bundle hosting the process — its Info.plist + .icns — never from anything
// the JS runtime can set (app.setName / app.dock.setIcon cannot change the
// tooltip). A CLI launch spawns node_modules' stock Electron.app, so without
// this the Dock shows the Electron atom labelled "Electron". The fix mirrors
// what electron-packager does, minus the rebuild: clone Electron.app into the
// user's state dir, rewrite its identity, re-sign ad-hoc, launch that.
//
//   <STATE_DIR>/shell/0xCopilot.app
//     Contents/Info.plist              CFBundleName + CFBundleDisplayName ->
//                                      "0xCopilot", CFBundleIdentifier ->
//                                      com.0x-copilot.app
//     Contents/Resources/electron.icns <- <appDir>/out/main/icon.icns
//                                      (staged there by the desktop build)
//
// The clone is APFS copy-on-write (`cp -c`), so it costs ~no disk; plain
// `cp -R` is the cross-filesystem fallback. CFBundleExecutable stays
// "Electron", so `app.isPackaged` remains false and the COPILOT_PRODUCTION
// posture contract (apps/desktop/main/posture.ts) is untouched. Only the
// outer bundle is re-signed: the identity edits break only the outer seal,
// while the untouched nested frameworks keep their original valid ad-hoc
// signatures — same model as stage.mjs's ad-hoc signing of the runtime.
// Every failure falls back to the stock binary: worse branding, never a
// blocked launch.

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

import { APP_NAME, STATE_DIR } from "./paths.mjs";
import * as ui from "./ui.mjs";

// = electron-builder.yml appId and apps/desktop/main/branding.ts APP_ID.
const BUNDLE_ID = "com.0x-copilot.app";

/** <dist>/Electron.app for a <dist>/Electron.app/Contents/MacOS/Electron
 *  binary, or null when the binary is not laid out as a mac bundle. */
function sourceBundleRoot(electronBinary) {
  const macos = path.dirname(electronBinary);
  const contents = path.dirname(macos);
  const bundle = path.dirname(contents);
  if (path.basename(macos) !== "MacOS") return null;
  if (path.basename(contents) !== "Contents") return null;
  if (!bundle.endsWith(".app")) return null;
  return bundle;
}

function run(cmd, args) {
  const res = spawnSync(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    const stderr = res.stderr?.toString().trim();
    throw new Error(
      `${cmd} ${args.join(" ")}: ${stderr || `exit ${res.status}`}`,
    );
  }
  return res.stdout?.toString() ?? "";
}

/**
 * Ensure the branded shell exists and is current; return the binary to launch.
 * Returns the stock `electronBinary` unchanged on non-mac hosts, when the
 * built app carries no icon yet, or when any shell step fails.
 */
export function ensureBrandedShell({
  electronBinary,
  appDir,
  stateDir = STATE_DIR,
}) {
  if (process.platform !== "darwin") return electronBinary;
  try {
    const source = sourceBundleRoot(electronBinary);
    const icns = path.join(appDir, "out", "main", "icon.icns");
    if (source === null || !existsSync(icns)) return electronBinary;

    const shellDir = path.join(stateDir, "shell");
    const bundle = path.join(shellDir, `${APP_NAME}.app`);
    const brandedBinary = path.join(bundle, "Contents", "MacOS", "Electron");
    const stampFile = path.join(shellDir, "shell-stamp.json");

    // Rebuild whenever the Electron the CLI resolves OR our icon changes.
    let electronVersion = "unknown";
    try {
      electronVersion = run("plutil", [
        "-extract",
        "CFBundleVersion",
        "raw",
        path.join(source, "Contents", "Info.plist"),
      ]).trim();
    } catch {
      // keyed by source path + icon hash alone
    }
    const stamp = JSON.stringify({
      source,
      electronVersion,
      icnsSha256: createHash("sha256").update(readFileSync(icns)).digest("hex"),
    });
    if (existsSync(brandedBinary)) {
      try {
        if (readFileSync(stampFile, "utf8") === stamp) return brandedBinary;
      } catch {
        // unreadable stamp -> rebuild
      }
    }

    ui.step("preparing the 0xCopilot app shell…");
    rmSync(bundle, { recursive: true, force: true });
    mkdirSync(shellDir, { recursive: true });
    try {
      run("/bin/cp", ["-c", "-R", source, bundle]);
    } catch {
      rmSync(bundle, { recursive: true, force: true });
      run("/bin/cp", ["-R", source, bundle]);
    }

    const plist = path.join(bundle, "Contents", "Info.plist");
    run("plutil", ["-replace", "CFBundleName", "-string", APP_NAME, plist]);
    run("plutil", [
      "-replace",
      "CFBundleDisplayName",
      "-string",
      APP_NAME,
      plist,
    ]);
    run("plutil", [
      "-replace",
      "CFBundleIdentifier",
      "-string",
      BUNDLE_ID,
      plist,
    ]);
    // CFBundleIconFile already points at electron.icns — swap the bytes.
    copyFileSync(
      icns,
      path.join(bundle, "Contents", "Resources", "electron.icns"),
    );
    run("codesign", ["--force", "--sign", "-", bundle]);

    writeFileSync(stampFile, stamp);
    return brandedBinary;
  } catch (err) {
    ui.warn(
      `could not prepare the branded app shell (launching plain Electron): ${
        err?.message ?? err
      }`,
    );
    return electronBinary;
  }
}
