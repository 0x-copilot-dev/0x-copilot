// Loader tests for index.cjs — the module that decides whether running WITHOUT
// the native addon is acceptable on this platform.
//
// Everything here drives the injected seams (platform / arch / env / isPackaged
// / require / log), so the whole matrix runs identically on a macOS host, an
// ubuntu CI runner and a Windows CI runner. The real-binary behaviour lives in
// openBeneath.test.mjs, which needs a compiled artifact.
//
// The case that matters most is the one that cannot be observed by running the
// app on this machine: a packaged win32 install with no compiled binary must
// hand back a stand-in that DENIES, because the alternative — `undefined` — makes
// host-fs.ts silently serve every confined read through the non-atomic realpath
// recheck. A green tool card over a TOCTOU-exposed read is exactly the failure
// mode this file exists to prevent.

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const {
  NATIVE_REQUIREMENT,
  candidatePaths,
  isProductionPosture,
  loadNative,
  nativeRequirement,
  nonAtomicFallbackAllowed,
} = require("./index.cjs");

/** A require seam where nothing is installed. */
function noBinary() {
  return () => {
    throw new Error("MODULE_NOT_FOUND");
  };
}

/** Collect log lines instead of writing to stderr. */
function recorder() {
  const lines = [];
  return { lines, log: (message) => lines.push(message) };
}

function load(overrides) {
  const { lines, log } = recorder();
  const native = loadNative({
    dir: path.join("/app", "native", "workspace-fs"),
    require: noBinary(),
    log,
    ...overrides,
  });
  return { native, lines, text: lines.join("\n") };
}

// --- per-platform requirement ---------------------------------------------

test("darwin does not require the addon; win32 and linux do", () => {
  assert.equal(nativeRequirement("darwin").required, false);
  assert.equal(nativeRequirement("win32").required, true);
  assert.equal(nativeRequirement("linux").required, true);
  // Each reason has to name the primitive, because it is quoted verbatim into
  // the operator-facing log a user or support engineer reads.
  assert.match(nativeRequirement("darwin").reason, /O_NOFOLLOW_ANY/u);
  assert.match(nativeRequirement("win32").reason, /NtCreateFile/u);
  assert.match(nativeRequirement("linux").reason, /openat2/u);
});

test("an unrecognised platform is treated as requiring the addon", () => {
  // Guessing permissively is how a TOCTOU window ships.
  const requirement = nativeRequirement("sunos");
  assert.equal(requirement.required, true);
  assert.match(requirement.reason, /unknown platform/u);
});

test("the requirement table cannot be mutated by a consumer", () => {
  assert.throws(() => {
    NATIVE_REQUIREMENT.win32.required = false;
  });
  assert.equal(NATIVE_REQUIREMENT.win32.required, true);
});

// --- production posture ----------------------------------------------------

test("production posture covers packaged, the CLI, and a supervised stack", () => {
  assert.equal(isProductionPosture({}, true), true);
  assert.equal(isProductionPosture({ COPILOT_PRODUCTION: "1" }, false), true);
  assert.equal(
    isProductionPosture({ COPILOT_RUNTIME_DIR: "/opt/copilot" }, false),
    true,
  );
  assert.equal(isProductionPosture({}, false), false);
  assert.equal(isProductionPosture({ COPILOT_RUNTIME_DIR: "" }, false), false);
});

test("COPILOT_DEV=1 wins over every production signal", () => {
  // `copilot dev` launches a real install layout on purpose; it must not be held
  // to the packaged-install standard.
  assert.equal(isProductionPosture({ COPILOT_DEV: "1" }, true), false);
  assert.equal(
    isProductionPosture(
      { COPILOT_DEV: "1", COPILOT_PRODUCTION: "1" },
      /* isPackaged */ false,
    ),
    false,
  );
});

test("the non-atomic opt-out is exactly one explicit value", () => {
  assert.equal(
    nonAtomicFallbackAllowed({ COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS: "1" }),
    true,
  );
  for (const value of [undefined, "", "0", "true", "yes"]) {
    assert.equal(
      nonAtomicFallbackAllowed({ COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS: value }),
      false,
      `"${String(value)}" must not be read as consent`,
    );
  }
});

// --- candidate resolution --------------------------------------------------

test("the canonical build output is probed before raw node-gyp output", () => {
  const dir = path.join("/app", "native", "workspace-fs");
  const candidates = candidatePaths(dir, "win32", "x64", undefined);
  assert.deepEqual(candidates, [
    path.join(dir, "prebuilds", "win32-x64", "workspace_fs.node"),
    path.join(dir, "build", "Release", "workspace_fs.node"),
    path.join(dir, "build", "Debug", "workspace_fs.node"),
  ]);
});

test("a packaged app also probes the extraResources destination", () => {
  const dir = path.join("/app.asar", "native", "workspace-fs");
  const candidates = candidatePaths(dir, "win32", "x64", "/app/resources");
  // electron-builder maps native/workspace-fs/prebuilds -> <resources>/workspace-fs,
  // so the per-target subdirectory is preserved. The bare path is the fallback
  // for a hand-assembled resources tree.
  assert.ok(
    candidates.includes(
      path.join(
        "/app/resources",
        "workspace-fs",
        "win32-x64",
        "workspace_fs.node",
      ),
    ),
  );
  assert.ok(
    candidates.includes(
      path.join("/app/resources", "workspace-fs", "workspace_fs.node"),
    ),
  );
});

test("an empty resourcesPath adds no candidate", () => {
  assert.equal(
    candidatePaths("/app/native/workspace-fs", "darwin", "arm64", "").length,
    3,
  );
});

// --- loading a real module -------------------------------------------------

test("a loadable addon is wrapped and its options are flattened", () => {
  const calls = [];
  const { native, lines } = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    require: () => ({
      openBeneath: (...args) => {
        calls.push(args);
        return 7;
      },
    }),
  });
  assert.equal(native.available, true);
  assert.equal(native.platform, "win32");
  assert.equal(native.openBeneath("/root", "a/b.txt", { directory: false }), 7);
  // The C entry point takes four positional arguments; missing options must
  // become explicit false rather than undefined.
  assert.deepEqual(calls, [["/root", "a/b.txt", false, false]]);
  native.openBeneath("/root", "d", { directory: true, write: true });
  assert.deepEqual(calls[1], ["/root", "d", true, true]);
  assert.deepEqual(lines, [], "a successful load must be silent");
});

test("a module that loaded but exports no openBeneath is reported, not skipped in silence", () => {
  const good = { openBeneath: () => 3 };
  const { native, text } = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    // First candidate is a wrong-shaped module; a later one is fine.
    require: (id) => (id.includes("prebuilds") ? {} : good),
  });
  assert.equal(native.available, true);
  assert.match(text, /does not export openBeneath/u);
});

test("the v2 write lifecycle is attached only when every method is present", () => {
  const v2 = [
    "workspaceRootIdentity",
    "workspacePrepare",
    "workspaceWrite",
    "workspaceSeal",
    "workspaceCommit",
    "workspaceReconcile",
    "workspaceReconcileClaim",
    "workspaceAbort",
    "workspaceProposeRecovery",
    "workspaceProposeRecoveryClaim",
  ];
  const partial = { openBeneath: () => 1 };
  for (const name of v2.slice(0, -1)) partial[name] = () => name;
  const incomplete = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    require: () => partial,
  }).native;
  // All-or-nothing: one missing method must not present a half-usable writable
  // capability that would otherwise fall back to node:fs mutations.
  assert.equal(incomplete.workspacePrepare, undefined);

  const complete = { openBeneath: () => 1 };
  for (const name of v2) complete[name] = () => name;
  const whole = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    require: () => complete,
  }).native;
  for (const name of v2) assert.equal(typeof whole[name], "function");
  assert.equal(whole.workspaceSeal(), "workspaceSeal");
});

// --- the fail-closed cases -------------------------------------------------

test("darwin without a binary is silently optional", () => {
  const { native, lines } = load({
    platform: "darwin",
    arch: "arm64",
    isPackaged: true,
  });
  assert.equal(native, undefined);
  assert.deepEqual(
    lines,
    [],
    "darwin's pure-Node open is already atomic, so there is nothing to warn about",
  );
});

for (const [label, env, isPackaged] of [
  ["a packaged install", {}, true],
  ["the copilot CLI", { COPILOT_PRODUCTION: "1" }, false],
  ["a supervised stack", { COPILOT_RUNTIME_DIR: "/opt/copilot" }, false],
]) {
  test(`win32 without a binary in ${label} fails closed`, () => {
    const { native, text } = load({
      platform: "win32",
      arch: "x64",
      env,
      isPackaged,
    });
    assert.notEqual(
      native,
      undefined,
      "returning undefined would make host-fs use the non-atomic path silently",
    );
    assert.equal(native.available, false);
    let error;
    try {
      native.openBeneath("/root", "a.txt", { directory: false });
      assert.fail("openBeneath must not return a descriptor");
    } catch (thrown) {
      error = thrown;
    }
    // ENOSYS and ENOTSUP both mean "the kernel lacks the primitive, fall back to
    // the Node path" to host-fs.ts, which is precisely the outcome the stand-in
    // exists to prevent. EPERM is a hard denial.
    assert.equal(error.code, "EPERM");
    assert.match(error.message, /TOCTOU/u);
    assert.match(error.message, /build:workspace-fs/u);
    assert.match(text, /FAIL-CLOSED/u);
    assert.match(text, /win32-x64/u);
  });
}

test("an unknown platform in production also fails closed", () => {
  const { native } = load({ platform: "sunos", arch: "x64", isPackaged: true });
  assert.equal(native.available, false);
});

test("win32 in development warns loudly and keeps the fallback", () => {
  const { native, text } = load({
    platform: "win32",
    arch: "x64",
    isPackaged: false,
  });
  assert.equal(native, undefined, "fast iteration must keep working");
  assert.match(text, /WARNING/u);
  assert.match(text, /NON-ATOMIC/u);
  assert.match(text, /build:workspace-fs/u);
});

test("the non-atomic opt-out turns the production denial back into a warning", () => {
  const { native, text } = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    env: { COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS: "1" },
  });
  assert.equal(native, undefined);
  assert.match(text, /WARNING/u);
  // The log must name the opt-out, so an operator reading it can tell a
  // deliberate choice from an accident.
  assert.match(text, /COPILOT_ALLOW_NONATOMIC_WORKSPACE_FS=1/u);
  assert.doesNotMatch(text, /FAIL-CLOSED/u);
});

test("COPILOT_DEV=1 in a packaged app degrades to the development warning", () => {
  const { native, text } = load({
    platform: "win32",
    arch: "x64",
    isPackaged: true,
    env: { COPILOT_DEV: "1" },
  });
  assert.equal(native, undefined);
  assert.match(text, /Development posture/u);
});
