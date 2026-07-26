// @vitest-environment node
import { createRequire } from "node:module";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import {
  BROWSER_RUNTIME_MANIFEST,
  resolveBrowserExecutablePath,
} from "./browser-runtime";
import { PINNED_CHROMIUM_VERSION, PINNED_PLAYWRIGHT_VERSION } from "./protocol";

const require = createRequire(import.meta.url);
const roots: string[] = [];

function runtimeFixture(overrides: Record<string, unknown> = {}) {
  const runtimeRoot = mkdtempSync(join(tmpdir(), "browser-runtime-"));
  roots.push(runtimeRoot);
  const browserRoot = join(runtimeRoot, "browser");
  const executable = join(browserRoot, "chromium", "bin", "chrome");
  mkdirSync(dirname(executable), { recursive: true });
  writeFileSync(executable, "browser", { mode: 0o700 });
  writeFileSync(
    join(browserRoot, BROWSER_RUNTIME_MANIFEST),
    JSON.stringify({
      schema_version: 1,
      platform: "darwin",
      arch: "arm64",
      playwright_version: PINNED_PLAYWRIGHT_VERSION,
      chromium_revision: "9999",
      chromium_version: PINNED_CHROMIUM_VERSION,
      executable: "chromium/bin/chrome",
      ...overrides,
    }),
  );
  return { runtimeRoot, browserRoot, executable };
}

afterEach(() => {
  for (const root of roots.splice(0)) {
    rmSync(root, { recursive: true, force: true });
  }
});

describe("resolveBrowserExecutablePath", () => {
  it("returns only the exact manifest-pinned executable under the runtime", () => {
    const fixture = runtimeFixture();
    expect(
      resolveBrowserExecutablePath({
        runtimeRoot: fixture.runtimeRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).toBe(realpathSync(fixture.executable));
  });

  it("rejects traversal and symlink escape attempts", () => {
    const traversal = runtimeFixture({ executable: "../outside" });
    expect(() =>
      resolveBrowserExecutablePath({
        runtimeRoot: traversal.runtimeRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).toThrow(/path is invalid/u);

    const linked = runtimeFixture({ executable: "chromium/bin/link" });
    const outside = join(linked.runtimeRoot, "outside");
    writeFileSync(outside, "outside", { mode: 0o700 });
    symlinkSync(outside, join(linked.browserRoot, "chromium", "bin", "link"));
    expect(() =>
      resolveBrowserExecutablePath({
        runtimeRoot: linked.runtimeRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).toThrow(/escaped/u);
  });

  it("rejects wrong target and dependency pins", () => {
    const wrongTarget = runtimeFixture({ arch: "x64" });
    expect(() =>
      resolveBrowserExecutablePath({
        runtimeRoot: wrongTarget.runtimeRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).toThrow(/target/u);

    const wrongPin = runtimeFixture({ chromium_version: "0.0.0.0" });
    expect(() =>
      resolveBrowserExecutablePath({
        runtimeRoot: wrongPin.runtimeRoot,
        platform: "darwin",
        arch: "arm64",
      }),
    ).toThrow(/version pin/u);
  });

  it("accepts a main-owned existing override without exposing it elsewhere", () => {
    const fixture = runtimeFixture();
    expect(
      resolveBrowserExecutablePath({
        runtimeRoot: "/not-used",
        executableOverride: fixture.executable,
      }),
    ).toBe(realpathSync(fixture.executable));
  });

  it("keeps package, staging manifest, and runtime pins in lockstep", () => {
    const playwright = require("playwright/package.json") as {
      version: string;
    };
    const corePackage = require.resolve("playwright-core/package.json");
    const registry = JSON.parse(
      readFileSync(join(dirname(corePackage), "browsers.json"), "utf8"),
    ) as {
      browsers: Array<{
        name: string;
        revision: string;
        browserVersion: string;
      }>;
    };
    const chromium = registry.browsers.find(
      (entry) => entry.name === "chromium",
    );
    const staging = JSON.parse(
      readFileSync(
        fileURLToPath(
          new URL(
            "../../../../tools/desktop-runtime/manifest.json",
            import.meta.url,
          ),
        ),
        "utf8",
      ),
    ) as {
      browser: {
        playwright_version: string;
        chromium_revision: string;
        chromium_version: string;
      };
    };

    expect(playwright.version).toBe(PINNED_PLAYWRIGHT_VERSION);
    expect(staging.browser.playwright_version).toBe(PINNED_PLAYWRIGHT_VERSION);
    expect(chromium?.revision).toBe(staging.browser.chromium_revision);
    expect(chromium?.browserVersion).toBe(PINNED_CHROMIUM_VERSION);
    expect(staging.browser.chromium_version).toBe(PINNED_CHROMIUM_VERSION);
  });
});
