// `copilot uninstall` — remove everything the CLI created on this machine.
// This does NOT remove the npm package itself (`npm rm -g @0x-copilot/cli`
// does that); it clears the staged runtime, the download cache, and the app's
// local data (secrets, embedded postgres cluster, logs).
//
// Before deleting, it STOPS any 0xCopilot processes still running from those
// trees (the supervised embedded postgres, staged service binaries, crashpad).
// Without that step a leaked postmaster — e.g. after a crashed or force-quit
// app — keeps writing into `pgdata` while we delete it: `rmSync` races the
// respawned files and either fails (ENOTEMPTY) or leaves an orphan postgres
// running against unlinked files. Matching is by absolute path prefix against
// OUR directories only, never by process name, so unrelated processes are
// never touched.

import { existsSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createInterface } from "node:readline/promises";
import path from "node:path";

import { appUserDataDir, DOWNLOAD_CACHE, HOME, STATE_DIR } from "./paths.mjs";
import * as ui from "./ui.mjs";

// Guard against a misconfigured env (empty HOME, COPILOT_HOME="/", …) turning a
// cleanup into a catastrophic `rm -rf`. A target must be an absolute path, not
// the filesystem root, not the home dir itself, and at least two levels deep.
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

async function confirm(question) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await rl.question(`${question} [y/N] `))
      .trim()
      .toLowerCase();
    return answer === "y" || answer === "yes";
  } finally {
    rl.close();
  }
}

/**
 * List running processes whose command line references one of our absolute
 * install paths (staged runtime / app data). POSIX only — on win32 the
 * supervisor owns child lifetime and `ps` isn't available; we skip and let
 * the rm failures surface with a hint instead of guessing at taskkill.
 */
function listOurProcesses(roots) {
  if (process.platform === "win32") return [];
  let out = "";
  try {
    out = execFileSync("ps", ["ax", "-o", "pid=,command="], {
      encoding: "utf8",
      maxBuffer: 8 * 1024 * 1024,
    });
  } catch {
    return [];
  }
  const found = [];
  for (const line of out.split("\n")) {
    const m = line.match(/^\s*(\d+)\s+(.*)$/);
    if (!m) continue;
    const pid = Number(m[1]);
    if (!Number.isFinite(pid) || pid === process.pid) continue;
    const command = m[2];
    if (roots.some((root) => command.includes(root))) {
      found.push({ pid, command });
    }
  }
  return found;
}

function aliveOnly(procs) {
  return procs.filter((p) => {
    try {
      process.kill(p.pid, 0);
      return true;
    } catch {
      return false;
    }
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** SIGTERM our matches, wait briefly, SIGKILL stragglers. */
async function stopProcesses(procs) {
  for (const p of procs) {
    try {
      process.kill(p.pid, "SIGTERM");
    } catch {
      /* already gone or not ours to signal */
    }
  }
  const deadline = Date.now() + 3000;
  let alive = aliveOnly(procs);
  while (alive.length > 0 && Date.now() < deadline) {
    await sleep(200);
    alive = aliveOnly(alive);
  }
  for (const p of alive) {
    try {
      process.kill(p.pid, "SIGKILL");
    } catch {
      /* raced its exit */
    }
  }
  if (procs.length > 0) {
    // Let file handles + pid files release before rm starts.
    await sleep(300);
  }
}

export async function uninstall({ yes = false } = {}) {
  ui.banner();

  const targets = [
    { label: "staged runtime", path: STATE_DIR },
    { label: "download cache", path: DOWNLOAD_CACHE },
    { label: "app data (secrets, database, logs)", path: appUserDataDir() },
  ];
  for (const t of targets.filter((t) => !isSafeTarget(t.path))) {
    ui.warn(`skipping unsafe path (${t.label}): ${t.path}`);
  }
  const present = targets.filter(
    (t) => isSafeTarget(t.path) && existsSync(t.path),
  );

  if (present.length === 0) {
    ui.ok("nothing to remove — 0xCopilot is not staged on this machine");
    ui.plain(
      `  ${ui.c.dim("to remove the CLI itself: npm rm -g @0x-copilot/cli")}`,
    );
    return true;
  }

  const roots = present.map((t) => t.path);
  const running = listOurProcesses(roots);

  ui.warn("this will permanently delete:");
  for (const t of present) {
    ui.plain(`  ${ui.c.red("−")} ${t.path}  ${ui.c.dim(`(${t.label})`)}`);
  }
  if (running.length > 0) {
    ui.plain();
    ui.warn(
      `${running.length} running 0xCopilot process${running.length === 1 ? "" : "es"} will be stopped first:`,
    );
    for (const p of running) {
      ui.plain(
        `  ${ui.c.red("×")} pid ${p.pid}  ${ui.c.dim(p.command.slice(0, 96))}`,
      );
    }
  }
  ui.plain();

  if (!yes) {
    // A detached/closed stdin can never answer the prompt — readline would
    // wait forever. Fail with the actionable flag instead of hanging.
    if (!process.stdin.isTTY) {
      ui.err(
        "not an interactive terminal — re-run with --yes to confirm deletion",
      );
      return false;
    }
    const proceed = await confirm("Delete these?");
    if (!proceed) {
      ui.info("cancelled — nothing was deleted");
      return false;
    }
  }

  if (running.length > 0) {
    ui.step(
      `stopping ${running.length} running process${running.length === 1 ? "" : "es"} …`,
    );
    await stopProcesses(running);
    // A supervising parent (the app) can respawn a child we just stopped —
    // sweep once more so the rm below isn't racing a fresh postmaster.
    const respawned = listOurProcesses(roots);
    if (respawned.length > 0) {
      await stopProcesses(respawned);
    }
    const survivors = aliveOnly(listOurProcesses(roots));
    if (survivors.length > 0) {
      ui.warn(
        `${survivors.length} process${survivors.length === 1 ? "" : "es"} would not stop — close the 0xCopilot app and re-run`,
      );
    }
  }

  let failed = false;
  for (const t of present) {
    let lastError = null;
    // One retry: on macOS a just-killed postgres can hold pgdata for a beat.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        rmSync(t.path, { recursive: true, force: true });
        lastError = null;
        break;
      } catch (e) {
        lastError = e;
        await sleep(500);
      }
    }
    if (lastError === null) {
      ui.ok(`removed ${t.path}`);
    } else {
      failed = true;
      ui.err(`could not remove ${t.path}: ${lastError.message}`);
      ui.plain(
        `  ${ui.c.dim("something may still be running from this tree — close the 0xCopilot app and re-run `copilot uninstall`")}`,
      );
    }
  }
  if (!failed) {
    ui.plain();
    ui.info(
      `done. To remove the command itself: ${ui.c.bold("npm rm -g @0x-copilot/cli")}`,
    );
  }
  return !failed;
}
