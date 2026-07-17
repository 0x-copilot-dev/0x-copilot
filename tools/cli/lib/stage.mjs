// Staging: drive tools/desktop-runtime/stage.mjs for THIS host, into the
// user's writable runtime dir, ad-hoc-signing on macOS so the unsigned bundle
// runs without a notarized DMG.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

import {
  ARCH,
  PLATFORM,
  PLATFORM_KEY,
  RUNTIME_DEST,
  stagedRuntimeRoot,
  VERSION_MARKER,
} from "./paths.mjs";
import * as ui from "./ui.mjs";

/** Read the staging-manifest of the currently-staged runtime, or null. */
export function readStagingManifest() {
  const file = path.join(stagedRuntimeRoot(), "staging-manifest.json");
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/** True when a runnable runtime is present (staged on-host, not download-only). */
export function isStaged() {
  const m = readStagingManifest();
  return m !== null && m.host_exec === true;
}

/** The CLI version the current runtime was staged for, or null. */
export function readStagedVersion() {
  try {
    return JSON.parse(readFileSync(VERSION_MARKER, "utf8")).version ?? null;
  } catch {
    return null;
  }
}

/**
 * True when we must (re)stage: no runnable runtime, or it was staged for a
 * different CLI version (an upgrade ships a new app bundle + service source, so
 * the runtime must be refreshed rather than silently mismatched).
 */
export function needsStage(version) {
  return !isStaged() || readStagedVersion() !== version;
}

/**
 * Stage the runtime for this host. Idempotent: stage.mjs skips work whose
 * stamp matches, so a warm call only re-verifies. Streams stage.mjs output
 * straight through. Records `version` so a later CLI upgrade re-stages.
 */
export function stageRuntime({ stageScript, version, force = false } = {}) {
  if (!existsSync(stageScript)) {
    throw new Error(`staging script not found at ${stageScript}`);
  }
  if (!force && isStaged() && readStagedVersion() === version) {
    ui.ok("runtime already staged");
    return;
  }

  const args = [
    stageScript,
    "--platform",
    PLATFORM,
    "--arch",
    ARCH,
    "--dest",
    RUNTIME_DEST,
  ];
  // Credential-free ad-hoc signing is macOS-only; Windows .exe run unsigned.
  if (PLATFORM === "darwin") args.push("--adhoc-sign");

  ui.step(
    `staging the ${PLATFORM_KEY} runtime (Python + Postgres + services) — first run downloads a few hundred MB`,
  );
  // Reuse the same node that's running the CLI to execute the staging script.
  const res = spawnSync(process.execPath, args, { stdio: "inherit" });
  if (res.error) throw new Error(`could not run staging: ${res.error.message}`);
  if (res.status !== 0) {
    throw new Error(`staging failed (exit ${res.status})`);
  }
  // Record what this runtime was staged for (used by needsStage on next start).
  if (version !== undefined) {
    try {
      writeFileSync(
        VERSION_MARKER,
        JSON.stringify({ version, staged_at: new Date().toISOString() }) + "\n",
      );
    } catch {
      /* non-fatal: worst case we re-stage next time */
    }
  }
  ui.ok(`runtime staged at ${stagedRuntimeRoot()}`);
}
