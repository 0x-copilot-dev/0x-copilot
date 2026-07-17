// `copilot uninstall` — remove everything the CLI created on this machine.
// This does NOT remove the npm package itself (`npm rm -g @0x-copilot/cli`
// does that); it clears the staged runtime, the download cache, and the app's
// local data (secrets, embedded postgres cluster, logs).

import { existsSync, rmSync } from "node:fs";
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

  ui.warn("this will permanently delete:");
  for (const t of present) {
    ui.plain(`  ${ui.c.red("−")} ${t.path}  ${ui.c.dim(`(${t.label})`)}`);
  }
  ui.plain();

  if (!yes) {
    const proceed = await confirm("Delete these?");
    if (!proceed) {
      ui.info("cancelled — nothing was deleted");
      return false;
    }
  }

  let failed = false;
  for (const t of present) {
    try {
      rmSync(t.path, { recursive: true, force: true });
      ui.ok(`removed ${t.path}`);
    } catch (e) {
      failed = true;
      ui.err(`could not remove ${t.path}: ${e.message}`);
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
