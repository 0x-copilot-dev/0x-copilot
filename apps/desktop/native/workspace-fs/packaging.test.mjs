// Guards against the addon going back to being orphaned.
//
// It WAS orphaned: `grep -rn workspace-fs apps/desktop/package.json
// esbuild.config.mjs electron-builder.yml .github/workflows/` matched nothing, so
// nothing built it, nothing packaged it, and loadNativeWorkspaceFs() could not
// resolve a binary in any shipped layout. Every assertion below names one of the
// wires that were missing. Deleting a wire fails a test instead of quietly
// returning the Windows read path to its non-atomic fallback.
//
// The manifests are asserted as TEXT, not parsed. There is no YAML parser this
// repo owns (js-yaml is only a transitive electron-builder dependency), and the
// failure being guarded against is a human deleting or renaming a line — which a
// line-level assertion catches exactly, with a message that quotes what must be
// restored.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const DESKTOP = path.resolve(import.meta.dirname, "..", "..");
const REPO = path.resolve(DESKTOP, "..", "..");

function read(...segments) {
  return fs.readFileSync(path.join(...segments), "utf8");
}

const pkg = JSON.parse(read(DESKTOP, "package.json"));

test("the desktop compile step builds the addon", () => {
  assert.match(
    pkg.scripts.compile,
    /build:workspace-fs/u,
    "`compile` must chain build:workspace-fs the way it chains " +
      "build:workspace-commit-helper, or the addon is built by nothing again",
  );
  assert.match(pkg.scripts["build:workspace-fs"], /native\/workspace-fs/u);
});

test("the desktop test step builds the addon and runs its own tests", () => {
  // Without the build, openBeneath.test.mjs skips everywhere and the kernel
  // primitive is never exercised in CI.
  assert.match(pkg.scripts.test, /build:workspace-fs/u);
  assert.match(pkg.scripts.test, /test:native/u);
  assert.match(pkg.scripts["test:native"], /--test/u);
});

test("every script that produces a distributable requires the addon", () => {
  // A packaged build is the one artifact a user runs without a toolchain, so it
  // must not be producible while the addon is missing. `--require` turns
  // build.mjs's warning into a build failure.
  for (const name of [
    "package",
    "dist:mac:arm64",
    "dist:mac:x64",
    "dist:win",
  ]) {
    assert.match(
      pkg.scripts[name],
      /build:workspace-fs:required/u,
      `${name} must chain build:workspace-fs:required`,
    );
  }
  assert.match(pkg.scripts["build:workspace-fs:required"], /--require/u);
});

test("electron-builder ships the loader inside the asar", () => {
  // host-fs.ts requires ../../native/workspace-fs/index.cjs relative to
  // out/main/index.js, i.e. <appRoot>/native/workspace-fs/index.cjs. `files`
  // previously listed only out/** and package.json, so in a packaged app that
  // require threw and host-fs fell back to `undefined` — which is the SILENT
  // non-atomic path, not the fail-closed one.
  const config = read(DESKTOP, "electron-builder.yml");
  assert.match(
    config,
    /^ {2}- native\/workspace-fs\/index\.cjs$/mu,
    "electron-builder `files` must carry native/workspace-fs/index.cjs",
  );
});

test("electron-builder ships the compiled binary outside the asar", () => {
  const config = read(DESKTOP, "electron-builder.yml");
  // The loader probes <resourcesPath>/workspace-fs/<platform>-<arch>/, so the
  // per-target subdirectory layout of prebuilds/ has to be preserved by the copy.
  assert.match(
    config,
    /^ {2}- from: native\/workspace-fs\/prebuilds$/mu,
    "extraResources must copy native/workspace-fs/prebuilds",
  );
  assert.match(config, /^ {4}to: workspace-fs$/mu);
});

test("the CLI payload carries the loader and any prebuilds", () => {
  // The npm CLI is the primary distribution channel, and its app root is
  // payload/desktop — a different layout from the electron-builder one, with its
  // own way to miss the file.
  const assemble = read(
    REPO,
    "tools",
    "cli",
    "scripts",
    "assemble-payload.mjs",
  );
  assert.match(assemble, /workspace-fs/u);
  assert.match(assemble, /index\.cjs/u);
  assert.match(assemble, /prebuilds/u);
});

test("CI builds the addon on macOS and on Windows", () => {
  // The Win32 NtCreateFile walk had never been compiled, let alone run. A job
  // that builds and exercises it on windows-latest is the only thing standing
  // between "implemented against the documented contract" and evidence.
  const workflow = read(REPO, ".github", "workflows", "ci-desktop.yml");
  assert.match(workflow, /native-workspace-fs:/u, "the job must exist");
  assert.match(workflow, /macos-latest/u);
  assert.match(workflow, /windows-latest/u);
  // Required, not best-effort: a compile or selfcheck failure has to red the job.
  assert.match(workflow, /build:workspace-fs:required/u);
  assert.match(workflow, /test:native/u);
});

test("the release build cannot ship without the addon", () => {
  const release = read(REPO, ".github", "workflows", "release-desktop.yml");
  assert.match(
    release,
    /COPILOT_REQUIRE_NATIVE_WORKSPACE_FS: "1"/u,
    "the release workflow must make a missing addon fatal, on every platform " +
      "it publishes",
  );
});

test("compiled artifacts are not committed", () => {
  const ignore = read(import.meta.dirname, ".gitignore");
  for (const pattern of ["build/", "prebuilds/", "*.node"]) {
    assert.ok(
      ignore.includes(pattern),
      `${pattern} must stay gitignored — a .node is per platform+arch and is ` +
        `produced at build time`,
    );
  }
});
