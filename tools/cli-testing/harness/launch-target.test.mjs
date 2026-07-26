import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";

import {
  INSTALLED_PAYLOAD_TARGET,
  resolveLaunchTarget,
} from "./launch-target.mjs";

const REPO = "/repo";
const CLI = "/global/node_modules/@0x-copilot/cli";

test("source target uses the checkout and permits an explicit APP_DIR", () => {
  assert.deepEqual(
    resolveLaunchTarget({
      repoRoot: REPO,
      env: { APP_DIR: "/branch/desktop" },
    }),
    {
      kind: "source",
      appDir: "/branch/desktop",
      electronBases: [REPO],
      cliPackageRoot: null,
    },
  );
});

test("installed-payload target launches only the globally installed payload", () => {
  const required = new Set([
    path.join(CLI, "package.json"),
    path.join(CLI, "payload", "desktop", "package.json"),
    path.join(CLI, "payload", "desktop", "out", "main", "index.js"),
  ]);
  const result = resolveLaunchTarget({
    repoRoot: REPO,
    env: { COPILOT_DESKTOP_TEST_TARGET: INSTALLED_PAYLOAD_TARGET },
    exists: (candidate) => required.has(candidate),
    getGlobalNpmRoot: () => "/global/node_modules",
  });
  assert.deepEqual(result, {
    kind: INSTALLED_PAYLOAD_TARGET,
    appDir: path.join(CLI, "payload", "desktop"),
    electronBases: [CLI],
    cliPackageRoot: CLI,
  });
});

test("installed-payload target rejects an APP_DIR override so it cannot test source by accident", () => {
  assert.throws(
    () =>
      resolveLaunchTarget({
        repoRoot: REPO,
        env: {
          COPILOT_DESKTOP_TEST_TARGET: INSTALLED_PAYLOAD_TARGET,
          APP_DIR: "/repo/apps/desktop",
        },
      }),
    /APP_DIR cannot be combined/u,
  );
});
