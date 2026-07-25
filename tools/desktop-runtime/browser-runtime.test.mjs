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
  const framework = path.join(sourceRoot, "chrome", "Browser.framework");
  const frameworkVersion = path.join(framework, "Versions", "1");
  fs.mkdirSync(frameworkVersion, { recursive: true });
  fs.writeFileSync(path.join(frameworkVersion, "Browser"), "framework");
  fs.symlinkSync("Versions/Current/Browser", path.join(framework, "Browser"));
  fs.symlinkSync("1", path.join(framework, "Versions", "Current"));
  const runtimeDir = path.join(root, "runtime");
  fs.mkdirSync(runtimeDir, { recursive: true });
  return { framework, root, executable, runtimeDir };
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
    const stagedFrameworkLink = path.join(
      f.runtimeDir,
      "browser",
      "chromium",
      "chrome",
      "Browser.framework",
      "Browser",
    );
    assert.equal(
      fs.readlinkSync(stagedFrameworkLink),
      "Versions/Current/Browser",
    );
    assert.equal(path.isAbsolute(fs.readlinkSync(stagedFrameworkLink)), false);
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

test("stageBrowserTree replaces a manifest match with escaped symlinks", () => {
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
    const stagedFrameworkLink = path.join(
      f.runtimeDir,
      "browser",
      "chromium",
      "chrome",
      "Browser.framework",
      "Browser",
    );
    fs.unlinkSync(stagedFrameworkLink);
    fs.symlinkSync(
      path.join(f.framework, "Versions", "Current", "Browser"),
      stagedFrameworkLink,
    );

    stageBrowserTree({
      runtimeDir: f.runtimeDir,
      platform: "darwin",
      arch: "arm64",
      executable: f.executable,
      metadata,
      log: (line) => logs.push(line),
    });

    assert.equal(
      fs.readlinkSync(stagedFrameworkLink),
      "Versions/Current/Browser",
    );
    assert.ok(logs.some((line) => line.includes("staged Chromium")));
    assert.ok(!logs.some((line) => line.includes("manifest match")));
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
