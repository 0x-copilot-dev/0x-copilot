import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertPinnedBrowserMetadata,
  BROWSER_RUNTIME_MANIFEST,
  stageBrowserTree,
} from "./browser-runtime.mjs";

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "browser-stage-"));
  const sourceRoot = path.join(root, "cache", "chromium-1228");
  const executable = path.join(sourceRoot, "chrome", "chrome");
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  fs.writeFileSync(executable, "browser", { mode: 0o700 });
  const runtimeDir = path.join(root, "runtime");
  fs.mkdirSync(runtimeDir, { recursive: true });
  return { root, executable, runtimeDir };
}

const metadata = {
  playwrightVersion: "1.61.1",
  chromiumRevision: "1228",
  chromiumVersion: "149.0.7827.55",
};

test("stageBrowserTree atomically copies the exact revision and manifest", () => {
  const f = fixture();
  try {
    const manifest = stageBrowserTree({
      runtimeDir: f.runtimeDir,
      platform: "darwin",
      arch: "arm64",
      executable: f.executable,
      metadata,
    });
    const manifestPath = path.join(
      f.runtimeDir,
      "browser",
      BROWSER_RUNTIME_MANIFEST,
    );
    assert.deepEqual(
      JSON.parse(fs.readFileSync(manifestPath, "utf8")),
      manifest,
    );
    assert.equal(
      fs.readFileSync(
        path.join(f.runtimeDir, "browser", ...manifest.executable.split("/")),
        "utf8",
      ),
      "browser",
    );
  } finally {
    fs.rmSync(f.root, { recursive: true, force: true });
  }
});

test("stageBrowserTree reuses only an exact manifest with an executable", () => {
  const f = fixture();
  const logs = [];
  try {
    stageBrowserTree({
      runtimeDir: f.runtimeDir,
      platform: "darwin",
      arch: "arm64",
      executable: f.executable,
      metadata,
    });
    stageBrowserTree({
      runtimeDir: f.runtimeDir,
      platform: "darwin",
      arch: "arm64",
      executable: f.executable,
      metadata,
      log: (line) => logs.push(line),
    });
    assert.ok(logs.some((line) => line.includes("manifest match")));
  } finally {
    fs.rmSync(f.root, { recursive: true, force: true });
  }
});

test("assertPinnedBrowserMetadata fails closed on package drift", () => {
  assert.throws(
    () =>
      assertPinnedBrowserMetadata(metadata, {
        playwright_version: "1.61.0",
        chromium_revision: "1228",
        chromium_version: "149.0.7827.55",
      }),
    /does not match manifest/u,
  );
});
