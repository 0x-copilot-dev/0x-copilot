// `copilot doctor` — inspect the install and report what would stop a launch.

import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

import {
  appMainEntry,
  appUserDataDir,
  DOWNLOAD_CACHE,
  isPlatformSupported,
  PLATFORM_KEY,
  resolveElectronBinary,
  resolveRoots,
  stagedRuntimeRoot,
} from "./paths.mjs";
import { readStagingManifest } from "./stage.mjs";
import * as ui from "./ui.mjs";

function electronVersion(binary) {
  // The version lives in electron's package.json next to its dist dir.
  let dir = path.dirname(binary);
  for (let i = 0; i < 8; i++) {
    const pkg = path.join(dir, "package.json");
    if (existsSync(pkg)) {
      try {
        const require = createRequire(path.join(dir, "index.js"));
        const v = require(pkg).version;
        if (typeof v === "string") return v;
      } catch {
        /* keep walking */
      }
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return "unknown";
}

function line(label, value) {
  ui.plain(`  ${ui.c.dim(label.padEnd(16))} ${value}`);
}

export function doctor(pkgRoot) {
  ui.banner();
  const problems = [];

  line("platform", PLATFORM_KEY);
  if (!isPlatformSupported()) {
    problems.push(
      "this platform has no staged runtime (macOS + Windows only).",
    );
  }

  // Where the app + staging script come from.
  let roots = null;
  try {
    roots = resolveRoots(pkgRoot);
    line("source", `${roots.mode} (${roots.repoRoot})`);
  } catch (e) {
    problems.push(e.message);
  }

  // Electron.
  if (roots) {
    try {
      const bin = resolveElectronBinary(roots.electronBases);
      line("electron", `${electronVersion(bin)}  ${ui.c.dim(bin)}`);
    } catch (e) {
      problems.push(e.message);
    }
    // App bundle.
    if (existsSync(appMainEntry(roots.appDir))) {
      line("app bundle", ui.c.green("present"));
    } else {
      line("app bundle", ui.c.yellow("not built"));
      if (roots.mode !== "dev") {
        problems.push("app bundle missing — reinstall the CLI.");
      }
    }
  }

  // Staged runtime.
  const manifest = readStagingManifest();
  if (manifest === null) {
    line("runtime", ui.c.yellow("not staged"));
    ui.plain(
      `  ${ui.c.dim("".padEnd(16))} run ${ui.c.bold("copilot install")}`,
    );
  } else if (manifest.host_exec !== true) {
    line("runtime", ui.c.red("download-only (not runnable)"));
    problems.push(
      "runtime was staged download-only — re-run `copilot install`.",
    );
  } else {
    line(
      "runtime",
      `${ui.c.green("staged")}  ${ui.c.dim(`${stagedRuntimeRoot()} · ${manifest.staged_at ?? "?"}`)}`,
    );
    if (process.platform === "darwin") {
      line(
        "signing",
        manifest.adhoc_signed
          ? ui.c.green("ad-hoc signed")
          : ui.c.yellow("unsigned (may not run on Apple Silicon)"),
      );
      verifySignatures(problems);
    }
  }

  line("app data", appUserDataDir());
  line(
    "downloads",
    existsSync(DOWNLOAD_CACHE)
      ? DOWNLOAD_CACHE
      : `${DOWNLOAD_CACHE} ${ui.c.dim("(empty)")}`,
  );

  // A live postmaster is not necessarily an orphan: it can belong to the
  // desktop app that is currently open. The supervisor writes an owner marker
  // while it is alive and leaves it behind only after a crash/force-quit.
  const database = databaseStatus(appUserDataDir());
  if (database.kind === "owned") {
    line(
      "database",
      ui.c.green(
        `running (owned by 0xCopilot; app pid ${database.ownerPid}, database pid ${database.postgresPid})`,
      ),
    );
  } else if (database.kind === "orphaned") {
    line(
      "database",
      ui.c.yellow(`orphaned instance running (pid ${database.postgresPid})`),
    );
    ui.plain(
      `  ${ui.c.dim("".padEnd(16))} if a launch won't start, run ${ui.c.bold("copilot repair")}`,
    );
  } else if (database.kind === "stale") {
    line(
      "database",
      ui.c.yellow(`stale lock (pid ${database.postgresPid} is not running)`),
    );
  } else if (database.kind === "invalid") {
    line("database", ui.c.yellow("unreadable postmaster lock"));
  }
  ui.plain();

  if (problems.length === 0) {
    ui.ok("all good — run `copilot` to start");
    return true;
  }
  for (const p of problems) ui.err(p);
  return false;
}

const POSTGRES_OWNER_MARKER_FILE = ".0xcopilot-owner.pid";

function parsePid(raw) {
  const first = raw.split(/\r?\n/u, 1)[0]?.trim() ?? "";
  if (!/^[1-9]\d*$/u.test(first)) return null;
  const pid = Number(first);
  return Number.isSafeInteger(pid) ? pid : null;
}

function readPid(pathname, { readFile }) {
  try {
    return parsePid(readFile(pathname, "utf-8"));
  } catch {
    return null;
  }
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
  } catch (e) {
    return e && e.code === "EPERM";
  }
  return true;
}

/**
 * Classify our embedded PostgreSQL without conflating the live app with an
 * orphan. Injectable dependencies keep this platform-facing CLI logic covered
 * by a plain node:test suite.
 */
export function databaseStatus(
  userDataDir,
  {
    exists = existsSync,
    readFile = readFileSync,
    processAlive = isPidAlive,
  } = {},
) {
  const pgdata = path.join(userDataDir, "pgdata");
  const pidPath = path.join(pgdata, "postmaster.pid");
  if (!exists(pidPath)) return { kind: "absent" };

  const postgresPid = readPid(pidPath, { readFile });
  if (postgresPid === null) return { kind: "invalid" };
  if (!processAlive(postgresPid)) return { kind: "stale", postgresPid };

  const ownerPath = path.join(pgdata, POSTGRES_OWNER_MARKER_FILE);
  const ownerPid = exists(ownerPath) ? readPid(ownerPath, { readFile }) : null;
  if (ownerPid !== null && processAlive(ownerPid)) {
    return { kind: "owned", postgresPid, ownerPid };
  }
  return { kind: "orphaned", postgresPid };
}

/** Spot-check a couple of critical binaries actually carry a valid signature. */
function verifySignatures(problems) {
  const root = stagedRuntimeRoot();
  const targets = [
    path.join(root, "python", "bin", "python3.13"),
    path.join(root, "postgres", "bin", "postgres"),
  ].filter((p) => existsSync(p));
  const bad = [];
  for (const t of targets) {
    const res = spawnSync("codesign", ["-v", t], { stdio: "ignore" });
    if (res.error || res.status !== 0) bad.push(path.basename(t));
  }
  if (bad.length) {
    problems.push(
      `invalid code signature on: ${bad.join(", ")} — re-run \`copilot install --force\`.`,
    );
  }
}
