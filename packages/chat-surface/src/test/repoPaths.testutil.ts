// Test-only: locate the checkout these tests belong to, and the Python
// interpreter that runs the AI backend's half of a differential corpus.
//
// Why this is not `resolve(process.cwd(), "../..")`. Vitest does NOT chdir into
// `--root`, so `process.cwd()` is wherever the runner was invoked — the repo
// root for `npx vitest run --root packages/chat-surface`, the package dir for
// `npm run test --workspace @0x-copilot/chat-surface`. Two levels up from those
// is a different directory each time, and from a git worktree
// (`<main>/.claude/worktrees/<id>`) it lands on `<main>/.claude` — a path with
// no `services/` under it at all, so the differential failed with a runner
// script that "does not exist" rather than with a fold divergence.
//
// The root is therefore derived from THIS FILE's location, never from cwd:
// `git rev-parse` answers it exactly in both layouts, and the walk-up fallback
// keys on the ONE marker that only a real checkout root carries — a
// `package.json` declaring npm `workspaces`. A bare `.git`/`package.json`/
// `node_modules` marker is what stops a walk-up short: `.claude/` sits between
// a worktree and the main checkout and would swallow the walk.

/// <reference types="node" />
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AI_BACKEND_VENV_PYTHON = "services/ai-backend/.venv/bin/python";

/** This module's own directory — the anchor every lookup below starts from. */
function moduleDir(): string {
  try {
    return dirname(fileURLToPath(import.meta.url));
  } catch {
    // Not a file URL in this runner; cwd is the only anchor left.
    return process.cwd();
  }
}

function git(args: readonly string[], cwd: string): string | null {
  try {
    const out = execFileSync("git", [...args], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    return out === "" ? null : out;
  } catch {
    // No git binary, or not a repository (a source tarball / vendored copy).
    return null;
  }
}

/**
 * A checkout root is the one directory carrying a `package.json` with an npm
 * `workspaces` field. Deliberately stricter than "has a package.json": every
 * workspace package has one of those, and a walk-up would stop at the first.
 */
function isCheckoutRoot(dir: string): boolean {
  const manifest = resolve(dir, "package.json");
  if (!existsSync(manifest)) return false;
  try {
    const parsed = JSON.parse(readFileSync(manifest, "utf8")) as {
      workspaces?: unknown;
    };
    return parsed.workspaces !== undefined;
  } catch {
    return false;
  }
}

function walkUpToCheckoutRoot(from: string): string | null {
  let dir = resolve(from);
  for (;;) {
    if (isCheckoutRoot(dir)) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/**
 * The checkout that owns THIS file — the main checkout normally, the worktree
 * root when running from a git worktree. Source under test (runner scripts,
 * corpora) is read from here, so a branch's Python is what the differential
 * executes.
 */
export function repoRoot(): string {
  const anchor = moduleDir();
  const topLevel = git(["rev-parse", "--show-toplevel"], anchor);
  if (topLevel !== null && isCheckoutRoot(topLevel)) return topLevel;
  const walked =
    walkUpToCheckoutRoot(anchor) ?? walkUpToCheckoutRoot(process.cwd());
  if (walked === null) {
    throw new Error(
      `Could not locate the repository root from ${anchor} (no ancestor package.json declares npm workspaces).`,
    );
  }
  return walked;
}

/**
 * The PRIMARY worktree — where `make setup` put the per-service `.venv`s. A git
 * worktree shares the main checkout's object store (`--git-common-dir` resolves
 * to `<main>/.git`) but has no `.venv` of its own, and is not supposed to grow
 * one: a second per-service venv per branch is hundreds of MB of duplicate
 * install. Equals `repoRoot()` in a main checkout.
 */
export function mainCheckoutRoot(): string {
  const anchor = moduleDir();
  const commonDir = git(["rev-parse", "--git-common-dir"], anchor);
  if (commonDir !== null) {
    // git prints this relative to the cwd it was handed when that cwd IS the
    // top level (plain `.git`), absolute otherwise.
    const candidate = dirname(resolve(anchor, commonDir));
    if (isCheckoutRoot(candidate)) return candidate;
  }
  return repoRoot();
}

/**
 * The ai-backend venv interpreter: `$PYTHON` if set, else this checkout's venv,
 * else the main checkout's. The fallback is what makes a worktree run possible
 * at all — and it stays honest about WHICH source it folds, because each corpus
 * runner puts its own `<root>/services/ai-backend/src` (derived from the
 * runner's `__file__`, i.e. the worktree's) at `sys.path[0]`, ahead of the
 * `__editable__.agent_runtime` path the main venv appends via site-packages.
 */
export function aiBackendPython(purpose: string): string {
  const explicit = process.env.PYTHON;
  if (explicit !== undefined && explicit !== "") {
    if (!existsSync(explicit)) {
      throw new Error(
        `${purpose} requires a Python interpreter; $PYTHON is set to ${explicit}, which does not exist.`,
      );
    }
    return explicit;
  }

  const candidates = Array.from(
    new Set([
      resolve(repoRoot(), AI_BACKEND_VENV_PYTHON),
      resolve(mainCheckoutRoot(), AI_BACKEND_VENV_PYTHON),
    ]),
  );
  const found = candidates.find((candidate) => existsSync(candidate));
  if (found === undefined) {
    throw new Error(
      `${purpose} requires the ai-backend venv. Tried:\n  ${candidates.join(
        "\n  ",
      )}\nCreate it with \`make setup\`, or point $PYTHON at an interpreter that can import agent_runtime.`,
    );
  }
  return found;
}

/**
 * A corpus run is an interpreter start plus `import agent_runtime…`, which pulls
 * in LangChain/LangGraph: ~11s measured on a warm laptop, in BOTH checkouts.
 * That is over twice vitest's 5s default, and `execFileSync` blocks the loop, so
 * the body runs to completion and is then failed for elapsed time — a real
 * differential result thrown away as a timeout. Both callers pass this to `it`.
 */
export const PYTHON_CORPUS_TIMEOUT_MS = 120_000;

/**
 * Execute a Python corpus runner and parse the JSON projection it prints.
 *
 * @param runnerPath repo-root-relative path to the runner script.
 * @param purpose    prefix for the "no interpreter" error, naming the caller.
 */
export function runPythonCorpus<T>(runnerPath: string, purpose: string): T {
  const root = repoRoot();
  const python = aiBackendPython(purpose);
  const runner = resolve(root, runnerPath);
  if (!existsSync(runner)) {
    throw new Error(
      `${purpose} could not find its corpus runner at ${runner}.`,
    );
  }
  return JSON.parse(
    execFileSync(python, [runner], { cwd: root, encoding: "utf8" }),
  ) as T;
}
