// Browser-runtime staging for the supervised Electron browser worker.
//
// Playwright's JS library is packaged with the desktop app, but its Chromium
// payload lives outside node_modules. This module installs the manifest-pinned
// Chromium revision into a shared download cache, copies that exact revision
// into the self-contained runtime, and writes the private manifest consumed by
// Electron main. Cross-target staging deliberately skips this step: browser
// binaries are staged only on a matching native runner, just like service
// wheels.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

export const BROWSER_RUNTIME_MANIFEST = "browser-manifest.json";
export const BROWSER_RUNTIME_SCHEMA_VERSION = 1;

const require = createRequire(import.meta.url);

function fail(message) {
  throw new Error(`browser runtime: ${message}`);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function normalizedRelativePath(value) {
  return value.split(path.sep).join("/");
}

function findRevisionRoot(executable, revision) {
  const expected = `chromium-${revision}`;
  let cursor = path.dirname(fs.realpathSync(executable));
  while (path.dirname(cursor) !== cursor) {
    if (path.basename(cursor) === expected) return cursor;
    cursor = path.dirname(cursor);
  }
  fail(`executable was not inside the expected ${expected} cache entry`);
}

export function readInstalledPlaywrightMetadata() {
  const playwrightPackage = require.resolve("playwright/package.json");
  const corePackage = require.resolve("playwright-core/package.json");
  const playwright = readJson(playwrightPackage);
  const browserRegistry = readJson(
    path.join(path.dirname(corePackage), "browsers.json"),
  );
  const chromium = browserRegistry.browsers?.find(
    (entry) => entry.name === "chromium",
  );
  if (
    typeof playwright.version !== "string" ||
    chromium === undefined ||
    typeof chromium.revision !== "string" ||
    typeof chromium.browserVersion !== "string"
  ) {
    fail("installed Playwright metadata is incomplete");
  }
  return {
    playwrightVersion: playwright.version,
    chromiumRevision: chromium.revision,
    chromiumVersion: chromium.browserVersion,
    playwrightCli: path.join(path.dirname(playwrightPackage), "cli.js"),
  };
}

export function assertPinnedBrowserMetadata(actual, expected) {
  for (const [field, actualValue, expectedValue] of [
    [
      "Playwright version",
      actual.playwrightVersion,
      expected.playwright_version,
    ],
    ["Chromium revision", actual.chromiumRevision, expected.chromium_revision],
    ["Chromium version", actual.chromiumVersion, expected.chromium_version],
  ]) {
    if (actualValue !== expectedValue) {
      fail(`${field} ${actualValue} does not match manifest ${expectedValue}`);
    }
  }
}

export function stageBrowserTree({
  runtimeDir,
  platform,
  arch,
  executable,
  metadata,
  log = () => {},
}) {
  if (!fs.existsSync(executable)) fail("Chromium executable is missing");
  const canonicalExecutable = fs.realpathSync(executable);
  const sourceRoot = findRevisionRoot(
    canonicalExecutable,
    metadata.chromiumRevision,
  );
  const sourceExecutableRelative = path.relative(
    sourceRoot,
    canonicalExecutable,
  );
  if (
    sourceExecutableRelative === "" ||
    sourceExecutableRelative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(sourceExecutableRelative)
  ) {
    fail("Chromium executable escaped its revision root");
  }

  const browserRoot = path.join(runtimeDir, "browser");
  const stagedExecutable = normalizedRelativePath(
    path.join("chromium", sourceExecutableRelative),
  );
  const wantedManifest = {
    schema_version: BROWSER_RUNTIME_SCHEMA_VERSION,
    platform,
    arch,
    playwright_version: metadata.playwrightVersion,
    chromium_revision: metadata.chromiumRevision,
    chromium_version: metadata.chromiumVersion,
    executable: stagedExecutable,
  };
  const existingManifestPath = path.join(browserRoot, BROWSER_RUNTIME_MANIFEST);
  try {
    const existing = readJson(existingManifestPath);
    if (
      JSON.stringify(existing) === JSON.stringify(wantedManifest) &&
      fs.existsSync(path.join(browserRoot, ...stagedExecutable.split("/")))
    ) {
      log("browser: Chromium runtime already staged (manifest match)");
      return wantedManifest;
    }
  } catch {
    // Missing/malformed/old manifests are replaced atomically below.
  }

  const tempRoot = path.join(
    runtimeDir,
    `.browser-stage-${process.pid}-${Date.now()}`,
  );
  fs.rmSync(tempRoot, { recursive: true, force: true });
  fs.mkdirSync(tempRoot, { recursive: true });
  try {
    fs.cpSync(sourceRoot, path.join(tempRoot, "chromium"), {
      recursive: true,
      preserveTimestamps: true,
    });
    const copiedExecutable = path.join(
      tempRoot,
      ...stagedExecutable.split("/"),
    );
    if (!fs.existsSync(copiedExecutable)) {
      fail("copied Chromium tree did not contain its executable");
    }
    fs.writeFileSync(
      path.join(tempRoot, BROWSER_RUNTIME_MANIFEST),
      `${JSON.stringify(wantedManifest, null, 2)}\n`,
      { mode: 0o600 },
    );
    fs.rmSync(browserRoot, { recursive: true, force: true });
    fs.renameSync(tempRoot, browserRoot);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
  log(`browser: staged Chromium ${metadata.chromiumVersion}`);
  return wantedManifest;
}

export function stageBrowserRuntime({
  runtimeDir,
  platform,
  arch,
  hostExec,
  cacheDir,
  expected,
  log = () => {},
}) {
  if (!hostExec) {
    log("browser: cross-target staging — skipping Chromium (no exec)");
    return null;
  }

  const browserCache = path.join(cacheDir, "playwright");
  fs.mkdirSync(browserCache, { recursive: true });
  // Playwright resolves its executable path from this env at module load.
  // Keeping it under the existing desktop-runtime cache also makes the release
  // workflow's cache entry cover the large browser payload.
  process.env.PLAYWRIGHT_BROWSERS_PATH = browserCache;

  const metadata = readInstalledPlaywrightMetadata();
  assertPinnedBrowserMetadata(metadata, expected);
  const { chromium } = require("playwright");
  let executable = chromium.executablePath();
  if (!fs.existsSync(executable)) {
    log(
      `browser: installing Chromium ${metadata.chromiumVersion} ` +
        `(revision ${metadata.chromiumRevision})`,
    );
    const result = spawnSync(
      process.execPath,
      [metadata.playwrightCli, "install", "chromium"],
      {
        stdio: "inherit",
        env: {
          ...process.env,
          PLAYWRIGHT_BROWSERS_PATH: browserCache,
        },
      },
    );
    if (result.error)
      fail(`Playwright install failed: ${result.error.message}`);
    if (result.status !== 0) {
      fail(`Playwright install exited with status ${result.status}`);
    }
    executable = chromium.executablePath();
  }

  return stageBrowserTree({
    runtimeDir,
    platform,
    arch,
    executable,
    metadata,
    log,
  });
}
