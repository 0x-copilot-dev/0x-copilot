import assert from "node:assert/strict";
import test from "node:test";

import macosSigning from "./macos-signing.cjs";

const { signAndVerifyMacAppBundle } = macosSigning;

function scriptedSpawn(results, calls) {
  return (command, args, options) => {
    calls.push({ args, command, options });
    const result = results.shift();
    assert.ok(result, "unexpected codesign invocation");
    return { stderr: "", stdout: "", ...result };
  };
}

test("repairs an invalid upstream app and strictly verifies the result", () => {
  const calls = [];
  const result = signAndVerifyMacAppBundle("/runtime/Browser.app", {
    identity: "-",
    preserveValid: true,
    spawnSync: scriptedSpawn(
      [
        { status: 1, stderr: "invalid upstream seal" },
        { status: 0 },
        { status: 0 },
      ],
      calls,
    ),
  });

  assert.equal(result.action, "signed");
  assert.deepEqual(calls[0].args, [
    "--verify",
    "--deep",
    "--strict",
    "/runtime/Browser.app",
  ]);
  assert.deepEqual(calls[1].args, [
    "--force",
    "--deep",
    "--sign",
    "-",
    "--timestamp=none",
    "--preserve-metadata=identifier,entitlements,requirements,runtime,flags",
    "/runtime/Browser.app",
  ]);
  assert.deepEqual(calls[2].args, calls[0].args);
});

test("preserves an upstream app only after strict verification succeeds", () => {
  const calls = [];
  const result = signAndVerifyMacAppBundle("/runtime/Browser.app", {
    identity: "-",
    preserveValid: true,
    spawnSync: scriptedSpawn([{ status: 0 }], calls),
  });

  assert.equal(result.action, "preserved");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args[0], "--verify");
});

test("release signing replaces the nested seal with hardened runtime options", () => {
  const calls = [];
  const result = signAndVerifyMacAppBundle("/runtime/Browser.app", {
    hardenedRuntime: true,
    identity: "Developer ID Application: Example",
    spawnSync: scriptedSpawn([{ status: 0 }, { status: 0 }], calls),
    timestamp: true,
  });

  assert.equal(result.action, "signed");
  assert.deepEqual(calls[0].args, [
    "--force",
    "--deep",
    "--sign",
    "Developer ID Application: Example",
    "--options",
    "runtime",
    "--timestamp",
    "--preserve-metadata=identifier,entitlements,requirements,runtime",
    "/runtime/Browser.app",
  ]);
  assert.deepEqual(calls[1].args, [
    "--verify",
    "--deep",
    "--strict",
    "/runtime/Browser.app",
  ]);
});

test("a signing failure remains fatal", () => {
  const calls = [];
  assert.throws(
    () =>
      signAndVerifyMacAppBundle("/runtime/Browser.app", {
        identity: "-",
        preserveValid: true,
        spawnSync: scriptedSpawn(
          [
            { status: 1, stderr: "invalid upstream seal" },
            { status: 1, stderr: "signing denied" },
          ],
          calls,
        ),
      }),
    /failed to re-sign nested app.*signing denied/u,
  );
  assert.equal(calls.length, 2);
});

test("a failed post-sign strict verification remains fatal", () => {
  const calls = [];
  assert.throws(
    () =>
      signAndVerifyMacAppBundle("/runtime/Browser.app", {
        identity: "-",
        preserveValid: true,
        spawnSync: scriptedSpawn(
          [
            { status: 1, stderr: "invalid upstream seal" },
            { status: 0 },
            { status: 1, stderr: "nested helper seal invalid" },
          ],
          calls,
        ),
      }),
    /failed strict verification.*nested helper seal invalid/u,
  );
  assert.equal(calls.length, 3);
});
