"use strict";

const { spawnSync: systemSpawnSync } = require("node:child_process");

const PRESERVED_METADATA = [
  "identifier",
  "entitlements",
  "requirements",
  "runtime",
];

function outputDetail(result) {
  if (result.error) return result.error.message;
  const stderr = String(result.stderr ?? "").trim();
  if (stderr) return stderr;
  const stdout = String(result.stdout ?? "").trim();
  if (stdout) return stdout;
  return `exit status ${String(result.status)}`;
}

function invokeCodesign(spawnSync, args) {
  return spawnSync("codesign", args, {
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
  });
}

function verifyMacAppBundle(bundle, { spawnSync = systemSpawnSync } = {}) {
  const result = invokeCodesign(spawnSync, [
    "--verify",
    "--deep",
    "--strict",
    bundle,
  ]);
  return {
    valid: !result.error && result.status === 0,
    detail: outputDetail(result),
  };
}

/**
 * Sign an entire nested macOS app as one code-sealed unit, then fail closed
 * unless Apple's strict recursive verifier accepts the result.
 *
 * `--deep` is intentional here: Chromium contains versioned frameworks,
 * helper apps, dylibs, and helper executables. Signing only its outer binary
 * leaves those nested seals inconsistent. Metadata preservation keeps each
 * helper's identifier, entitlements, requirements, and runtime version.
 */
function signAndVerifyMacAppBundle(
  bundle,
  {
    identity,
    hardenedRuntime = false,
    preserveValid = false,
    spawnSync = systemSpawnSync,
    timestamp = false,
  } = {},
) {
  if (!bundle) throw new TypeError("bundle is required");
  if (!identity) throw new TypeError("signing identity is required");

  if (preserveValid) {
    const upstream = verifyMacAppBundle(bundle, { spawnSync });
    if (upstream.valid) {
      return { action: "preserved", verification: upstream };
    }
  }

  const args = ["--force", "--deep", "--sign", identity];
  if (hardenedRuntime) args.push("--options", "runtime");
  args.push(timestamp ? "--timestamp" : "--timestamp=none");

  const preservedMetadata = [...PRESERVED_METADATA];
  // Ad-hoc staging should retain existing code flags. Release signing instead
  // supplies `--options runtime`, so it must be allowed to replace them.
  if (!hardenedRuntime) preservedMetadata.push("flags");
  args.push(`--preserve-metadata=${preservedMetadata.join(",")}`, bundle);

  const signed = invokeCodesign(spawnSync, args);
  if (signed.error || signed.status !== 0) {
    throw new Error(
      `failed to re-sign nested app ${bundle}: ${outputDetail(signed)}`,
    );
  }

  const verification = verifyMacAppBundle(bundle, { spawnSync });
  if (!verification.valid) {
    throw new Error(
      `re-signed nested app failed strict verification ${bundle}: ` +
        verification.detail,
    );
  }
  return { action: "signed", verification };
}

module.exports = {
  signAndVerifyMacAppBundle,
  verifyMacAppBundle,
};
