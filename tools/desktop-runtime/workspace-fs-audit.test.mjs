import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  addonRequiredFor,
  auditWorkspaceFsAddon,
} from "./workspace-fs-audit.mjs";

/** A repo-shaped temp tree, optionally with a prebuilt addon for `target`. */
function fixture({ layout = "checkout", target } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "wfs-audit-"));
  const base =
    layout === "checkout"
      ? path.join(root, "apps", "desktop", "native", "workspace-fs")
      : path.join(root, "desktop", "native", "workspace-fs");
  if (target !== undefined) {
    const dir = path.join(base, "prebuilds", target);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "workspace_fs.node"), "binary");
  }
  return root;
}

function audit(root, overrides) {
  const lines = [];
  const result = auditWorkspaceFsAddon({
    repoRoot: root,
    platform: "win32",
    arch: "x64",
    hostPlatform: "win32",
    hostArch: "x64",
    log: (message) => lines.push(message),
    ...overrides,
  });
  return { result, text: lines.join("\n") };
}

test("darwin does not require the addon; every other platform does", () => {
  assert.equal(addonRequiredFor("darwin"), false);
  assert.equal(addonRequiredFor("win32"), true);
  assert.equal(addonRequiredFor("linux"), true);
  // An unknown platform has no verified atomic primitive, so it is required.
  assert.equal(addonRequiredFor("sunos"), true);
});

test("a present binary is reported with its repo-relative path", () => {
  const root = fixture({ target: "win32-x64" });
  try {
    const { result, text } = audit(root);
    assert.equal(result.present, true);
    assert.equal(result.required, true);
    assert.equal(
      result.path,
      path.join(
        "apps",
        "desktop",
        "native",
        "workspace-fs",
        "prebuilds",
        "win32-x64",
        "workspace_fs.node",
      ),
    );
    assert.match(text, /addon present for win32-x64/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the published CLI payload layout is found too", () => {
  // There repoRoot is the payload directory and the app lives at desktop/.
  const root = fixture({ layout: "payload", target: "win32-x64" });
  try {
    assert.equal(audit(root).result.present, true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a binary for a DIFFERENT target does not count as present", () => {
  const root = fixture({ target: "darwin-arm64" });
  try {
    const { result } = audit(root, { platform: "win32", arch: "x64" });
    assert.equal(result.present, false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("absence on a required platform is a WARNING that names the consequence", () => {
  const root = fixture();
  try {
    const { result, text } = audit(root);
    assert.equal(result.present, false);
    assert.equal(result.required, true);
    // Silence here is the defect: a staged tree must not look complete while the
    // shipped read path has no atomic primitive.
    assert.match(text, /WARNING/u);
    assert.match(text, /NON-ATOMIC/u);
    assert.match(result.reason, /build:workspace-fs/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("absence on darwin is reported without a warning", () => {
  const root = fixture();
  try {
    const { result, text } = audit(root, {
      platform: "darwin",
      arch: "arm64",
      hostPlatform: "darwin",
      hostArch: "arm64",
    });
    assert.equal(result.required, false);
    assert.doesNotMatch(text, /WARNING/u);
    assert.match(text, /O_NOFOLLOW_ANY/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a cross-target stage says the runner is wrong, not the build step", () => {
  const root = fixture();
  try {
    const { result } = audit(root, {
      platform: "win32",
      arch: "x64",
      hostPlatform: "darwin",
      hostArch: "arm64",
    });
    // Telling these apart matters: one is "you forgot to build", the other is
    // "node-gyp cannot cross-compile, use a Windows runner".
    assert.match(result.reason, /does not cross-compile from darwin-arm64/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
