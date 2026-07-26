import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const BIN = path.join(PACKAGE_ROOT, "bin", "copilot.mjs");

function runCli(...args) {
  return spawnSync(process.execPath, [BIN, ...args], {
    cwd: PACKAGE_ROOT,
    encoding: "utf8",
  });
}

test("workspace metadata never blocks Linux CI installation", () => {
  const manifest = JSON.parse(
    readFileSync(path.join(PACKAGE_ROOT, "package.json"), "utf8"),
  );

  assert.equal(manifest.os, undefined);
});

test(
  "unsupported hosts can install the workspace but cannot stage a desktop runtime",
  {
    skip: process.platform !== "linux",
  },
  () => {
    // The workspace must remain installable on Linux CI even though the packaged
    // desktop app deliberately supports macOS and Windows only. The executable,
    // rather than npm package metadata, owns that user-facing platform boundary.
    const result = runCli("install");

    assert.equal(result.status, 1);
    assert.match(
      result.stderr,
      /desktop currently supports macOS and Windows \(this host is linux\)/,
    );
  },
);
