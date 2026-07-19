// `copilot repair` — NON-destructive recovery. Unblocks a stuck local install
// (an orphaned embedded PostgreSQL still holding its data dir, a leftover lock,
// or — with --session — a wedged sign-in) WITHOUT deleting your conversations,
// database, or settings. That total wipe is `copilot uninstall`. Diagnose first
// with `copilot doctor`.
//
// Note: the app now auto-reclaims an orphaned database on boot, so this is
// mostly a manual escape hatch for when a launch still won't come up.

import { existsSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";

import { appUserDataDir, HOME, PLATFORM, stagedRuntimeRoot } from "./paths.mjs";
import * as ui from "./ui.mjs";

// Guard against a misconfigured env (empty HOME, COPILOT_HOME="/", …) turning a
// targeted cleanup into a catastrophic delete. Mirrors uninstall.mjs: absolute,
// not the fs root, not HOME itself, at least two levels deep.
function isSafeTarget(p) {
  if (typeof p !== "string" || p === "" || !path.isAbsolute(p)) return false;
  const resolved = path.resolve(p);
  const root = path.parse(resolved).root;
  if (resolved === root || resolved === HOME) return false;
  const depth = path
    .relative(root, resolved)
    .split(path.sep)
    .filter(Boolean).length;
  return depth >= 2;
}

function pgCtlPath() {
  const exe = PLATFORM === "win32" ? "pg_ctl.exe" : "pg_ctl";
  return path.join(stagedRuntimeRoot(), "postgres", "bin", exe);
}

// kill(pid, 0): ESRCH proves the process is gone; EPERM means alive-but-not-ours.
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err && err.code === "EPERM";
  }
}

function parsePid(pidPath) {
  try {
    const first = readFileSync(pidPath, "utf-8").split(/\r?\n/u, 1)[0] ?? "";
    const pid = Number.parseInt(first.trim(), 10);
    return Number.isNaN(pid) ? null : pid;
  } catch {
    return null;
  }
}

function stopCluster(pgCtl, pgdata, mode) {
  spawnSync(pgCtl, ["-D", pgdata, "-m", mode, "-w", "-t", "30", "stop"], {
    stdio: "ignore",
  });
}

// Reclaim the embedded Postgres data dir if a previous boot left a cluster
// orphaned (force-quit / crash before a clean stop). Returns true if it found
// and acted on something.
function reclaimPostgres() {
  const pgdata = path.join(appUserDataDir(), "pgdata");
  const pidPath = path.join(pgdata, "postmaster.pid");
  if (!existsSync(pidPath)) return false;
  if (!isSafeTarget(pidPath)) {
    ui.warn(`skipping unsafe path: ${pidPath}`);
    return false;
  }

  const pid = parsePid(pidPath);
  if (pid === null || !isAlive(pid)) {
    // Dead postmaster left a stale lock — just clear it.
    rmSync(pidPath, { force: true });
    ui.ok("cleared a stale database lock (no process was running)");
    return true;
  }

  const pgCtl = pgCtlPath();
  if (!existsSync(pgCtl)) {
    ui.warn(
      `an embedded database (pid ${pid}) is running, but the staged runtime is missing.`,
    );
    ui.plain(`  ${ui.c.dim(`stop it manually, then retry: kill ${pid}`)}`);
    return true;
  }

  ui.step(`stopping an orphaned embedded database (pid ${pid})…`);
  stopCluster(pgCtl, pgdata, "fast");
  if (isAlive(pid)) stopCluster(pgCtl, pgdata, "immediate");

  if (isAlive(pid)) {
    ui.warn(`the database (pid ${pid}) did not stop; force it: kill -9 ${pid}`);
  } else {
    ui.ok("stopped the orphaned database and freed its lock");
  }
  rmSync(pidPath, { force: true });
  return true;
}

// Clear saved sign-in sessions so a rejected/stuck bearer can't strand the app
// on the wrong account. SecretStorage keeps per-workspace sessions in
// <userData>/secrets/<workspaceId>/… (DIRECTORIES); the sibling file
// secrets/boot-env.bin holds the boot secrets and is LEFT ALONE — rotating it
// would orphan the local database. Returns true if anything was cleared.
function clearSessions() {
  const secretsDir = path.join(appUserDataDir(), "secrets");
  if (!existsSync(secretsDir)) return false;
  let cleared = false;
  for (const entry of readdirSync(secretsDir, { withFileTypes: true })) {
    // Only per-workspace session directories. boot-env.bin is a FILE → skipped.
    if (!entry.isDirectory()) continue;
    const dir = path.join(secretsDir, entry.name);
    if (!isSafeTarget(dir)) continue;
    rmSync(dir, { recursive: true, force: true });
    cleared = true;
  }
  if (cleared) {
    ui.ok("cleared saved sign-in sessions — you'll sign in again next launch");
  }
  return cleared;
}

export async function repair({ yes: _yes = false, session = false } = {}) {
  ui.banner();

  let acted = reclaimPostgres();

  if (session) {
    acted = clearSessions() || acted;
  } else {
    ui.plain(
      `  ${ui.c.dim(
        "signed in as the wrong account or seeing “Invalid bearer token”? re-run with --session (or sign out in the app)",
      )}`,
    );
  }

  ui.plain();
  if (!acted) {
    ui.ok("nothing to repair — no orphaned services or stale locks found");
  } else {
    ui.info(`done. Run ${ui.c.bold("copilot")} to start the app.`);
  }
  ui.plain(
    `  ${ui.c.dim(
      "your conversations + settings were kept. To wipe everything: copilot uninstall",
    )}`,
  );
  return true;
}
