"use strict";

// electron-builder afterPack hook (runs AFTER the app is packed, BEFORE the
// bundle is code-signed). It batch-signs every nested Mach-O binary under
// Contents/Resources/runtime — the bundled CPython (.so/.dylib + the
// interpreter) and PostgreSQL binaries — with the hardened-runtime flag, so
// electron-builder's subsequent outer signature (and notarization) stays
// valid.
//
// It NO-OPS cleanly on unsigned local builds: when
// CSC_IDENTITY_AUTO_DISCOVERY=false, or no Developer ID identity is
// resolvable, it logs and skips so `dist:*` still produces a runnable
// (unsigned) app for local iteration.

const { execFileSync, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

// Mach-O + fat-binary magic numbers (first 4 bytes, either endianness).
const MACH_O_MAGIC = new Set([
  0xfeedface, 0xcefaedfe, 0xfeedfacf, 0xcffaedfe, 0xcafebabe, 0xbebafeca,
]);

function isMachO(file) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(4);
    const read = fs.readSync(fd, buf, 0, 4, 0);
    if (read < 4) return false;
    return (
      MACH_O_MAGIC.has(buf.readUInt32BE(0)) || MACH_O_MAGIC.has(buf.readUInt32LE(0))
    );
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function walkFiles(dir, out) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) continue; // sign real files, not symlinks
    if (entry.isDirectory()) walkFiles(full, out);
    else if (entry.isFile()) out.push(full);
  }
}

function resolveIdentity() {
  if (process.env.CSC_IDENTITY_AUTO_DISCOVERY === "false") return null;
  if (process.env.CSC_NAME) return process.env.CSC_NAME;
  // Fall back to the first Developer ID Application identity in the keychain.
  try {
    const out = execFileSync(
      "security",
      ["find-identity", "-v", "-p", "codesigning"],
      { encoding: "utf8" },
    );
    const match = out.match(/"(Developer ID Application:[^"]+)"/u);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}

exports.default = async function signNested(context) {
  if (context.electronPlatformName !== "darwin") return;

  const identity = resolveIdentity();
  if (!identity) {
    console.log(
      "[sign-nested] no signing identity (CSC_IDENTITY_AUTO_DISCOVERY=false or no Developer ID cert) — skipping nested signing (unsigned build).",
    );
    return;
  }

  const productFilename = context.packager.appInfo.productFilename;
  const runtimeDir = path.join(
    context.appOutDir,
    `${productFilename}.app`,
    "Contents",
    "Resources",
    "runtime",
  );
  if (!fs.existsSync(runtimeDir)) {
    console.log(
      `[sign-nested] no runtime dir at ${runtimeDir} — nothing to sign.`,
    );
    return;
  }

  const files = [];
  walkFiles(runtimeDir, files);
  const machO = files.filter(isMachO);
  if (machO.length === 0) {
    console.log("[sign-nested] no Mach-O binaries found under runtime/.");
    return;
  }

  console.log(
    `[sign-nested] signing ${machO.length} nested Mach-O binaries with "${identity}"…`,
  );
  const baseArgs = [
    "--force",
    "--options",
    "runtime",
    "--timestamp",
    "--sign",
    identity,
  ];
  const BATCH = 40;
  for (let i = 0; i < machO.length; i += BATCH) {
    const batch = machO.slice(i, i + BATCH);
    const result = spawnSync("codesign", [...baseArgs, ...batch], {
      stdio: ["ignore", "ignore", "pipe"],
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new Error(
        `[sign-nested] codesign failed (batch ${i / BATCH}): ${result.stderr}`,
      );
    }
  }
  console.log(`[sign-nested] signed ${machO.length} nested binaries.`);
};
