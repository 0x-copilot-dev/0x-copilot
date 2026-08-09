// The differentials in `destinations/run/` shell out to the AI backend, so
// their first failure mode is a path, not a fold. Under `resolve(process.cwd(),
// "../..")` a git worktree resolved to `<main>/.claude` — an existing directory
// with no `services/` under it — and the corpus runner "did not exist".
//
// These assert the resolution itself, so that failure reads as a path bug here
// instead of as a differential failure two files away.

/// <reference types="node" />
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  aiBackendPython,
  mainCheckoutRoot,
  repoRoot,
} from "./repoPaths.testutil";

describe("repo path resolution (main checkout and git worktree)", () => {
  it("resolves the checkout that owns this test file", () => {
    // The strongest available assertion: the root must literally contain THIS
    // file. `<main>/.claude` passes an `existsSync` and fails this.
    expect(
      existsSync(
        resolve(repoRoot(), "packages/chat-surface/src/test/repoPaths.test.ts"),
      ),
    ).toBe(true);
    expect(existsSync(resolve(repoRoot(), "services/ai-backend"))).toBe(true);
  });

  it("resolves a workspace root, not a nested package directory", () => {
    for (const root of [repoRoot(), mainCheckoutRoot()]) {
      const manifest = JSON.parse(
        readFileSync(resolve(root, "package.json"), "utf8"),
      ) as { workspaces?: unknown };
      expect(manifest.workspaces).toBeDefined();
    }
  });

  it("resolves an ai-backend interpreter even when this checkout has no venv", () => {
    // A worktree has no `.venv` of its own and is not meant to grow one, so the
    // lookup has to reach the main checkout's. This is the assertion that fails
    // if the fallback is dropped.
    expect(existsSync(aiBackendPython("repo path test"))).toBe(true);
  });
});
