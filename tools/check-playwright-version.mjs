#!/usr/bin/env node

import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(root, relativePath), "utf8"));
}

function fail(message) {
  process.stderr.write(`Playwright version check failed: ${message}\n`);
  process.exitCode = 1;
}

function readExportedString(relativePath, exportName) {
  const source = fs.readFileSync(path.join(root, relativePath), "utf8");
  const match = source.match(
    new RegExp(`export const ${exportName} = ["']([^"']+)["'];`, "u"),
  );
  return match?.[1];
}

const rootManifest = readJson("package.json");
const canonicalVersion = rootManifest.devDependencies?.playwright;

if (!/^\d+\.\d+\.\d+$/.test(canonicalVersion ?? "")) {
  fail(
    `root devDependencies.playwright must be an exact stable version; received ${JSON.stringify(canonicalVersion)}`,
  );
} else {
  if (rootManifest.overrides?.playwright !== "$playwright") {
    fail('root overrides.playwright must be "$playwright"');
  }

  const workspaceConsumers = [
    "apps/desktop/package.json",
    "tools/cli-testing/package.json",
  ];
  for (const relativePath of workspaceConsumers) {
    const manifest = readJson(relativePath);
    if (manifest.dependencies?.playwright !== "*") {
      fail(
        `${relativePath} must consume the root-enforced Playwright version with "*"`,
      );
    }
  }

  // The CLI is published outside the monorepo, so its tarball must carry an
  // exact runtime dependency. Keep that release mirror equal to the root pin.
  const cliManifest = readJson("tools/cli/package.json");
  if (cliManifest.dependencies?.playwright !== canonicalVersion) {
    fail(
      `tools/cli/package.json must mirror the root pin for its published runtime (${canonicalVersion})`,
    );
  }

  const nestedLock = path.join(root, "tools/cli-testing/package-lock.json");
  if (fs.existsSync(nestedLock)) {
    fail(
      "tools/cli-testing/package-lock.json must not exist; use the root lockfile",
    );
  }

  const lock = readJson("package-lock.json");
  const lockedVersion = lock.packages?.["node_modules/playwright"]?.version;
  if (lockedVersion !== canonicalVersion) {
    fail(
      `package-lock.json resolves Playwright ${lockedVersion ?? "<missing>"}, expected ${canonicalVersion}`,
    );
  }

  const harnessRequire = createRequire(
    path.join(root, "tools/cli-testing/harness/driver.mjs"),
  );
  const harnessPlaywrightPath = harnessRequire.resolve(
    "playwright/package.json",
  );
  const expectedPlaywrightPath = path.join(
    root,
    "node_modules/playwright/package.json",
  );
  if (path.resolve(harnessPlaywrightPath) !== expectedPlaywrightPath) {
    fail(
      `journey harness resolves a nested Playwright at ${harnessPlaywrightPath}; remove its nested node_modules`,
    );
  }

  const staging = readJson("tools/desktop-runtime/manifest.json").browser;
  if (staging?.playwright_version !== canonicalVersion) {
    fail(
      `desktop runtime manifest pins Playwright ${staging?.playwright_version ?? "<missing>"}, expected ${canonicalVersion}`,
    );
  }

  const coreManifestPath = require.resolve("playwright-core/package.json");
  const browserRegistry = readJson(
    path.relative(
      root,
      path.join(path.dirname(coreManifestPath), "browsers.json"),
    ),
  );
  const chromium = browserRegistry.browsers?.find(
    (entry) => entry.name === "chromium",
  );
  if (
    chromium?.revision !== staging?.chromium_revision ||
    chromium?.browserVersion !== staging?.chromium_version
  ) {
    fail(
      "desktop runtime Chromium revision/version must match the root Playwright installation",
    );
  }

  const protocolPath = "apps/desktop/main/browser/protocol.ts";
  const protocolPlaywright = readExportedString(
    protocolPath,
    "PINNED_PLAYWRIGHT_VERSION",
  );
  const protocolChromium = readExportedString(
    protocolPath,
    "PINNED_CHROMIUM_VERSION",
  );
  if (protocolPlaywright !== canonicalVersion) {
    fail(
      `${protocolPath} pins Playwright ${protocolPlaywright ?? "<missing>"}, expected ${canonicalVersion}`,
    );
  }
  if (protocolChromium !== staging?.chromium_version) {
    fail(
      `${protocolPath} pins Chromium ${protocolChromium ?? "<missing>"}, expected ${staging?.chromium_version ?? "<missing>"}`,
    );
  }
}

if (process.exitCode !== 1) {
  process.stdout.write(
    `Playwright ${canonicalVersion} is pinned by the root workspace and shared by desktop + journey tooling.\n`,
  );
}
