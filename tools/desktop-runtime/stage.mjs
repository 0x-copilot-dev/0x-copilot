#!/usr/bin/env node
/**
 * stage.mjs — stage the self-contained desktop runtime (python + postgres +
 * the three backend services with their dependencies) into
 * apps/desktop/resources/runtime/<platform>-<arch>/.
 *
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
 *   node tools/desktop-runtime/stage.mjs --platform win32 --arch x64
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --dest apps/desktop/resources
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --adhoc-sign
 *
 * --adhoc-sign (macOS host only): after staging, ad-hoc code-sign every
 * bundled Mach-O binary (identity "-") and strip the quarantine xattr. Apple
 * Silicon refuses to execute an UNSIGNED arm64 binary, but an ad-hoc signature
 * needs NO Apple Developer credentials — this is what lets the `copilot` CLI
 * ship the runtime through npm/bun without a notarized DMG. The electron-builder
 * packaging path leaves this OFF and signs with a real Developer ID instead
 * (build/sign-nested.js), so the flag is opt-in.
 *
 * Zero non-builtin node deps. External processes used: the system `tar`
 * (bsdtar on macOS / Windows 10+; handles .tar.gz, .txz via xz, and reads
 * the zonky .jar because a jar is a zip), the *staged* python for pip /
 * compileall, and (with --adhoc-sign) the system `codesign` + `xattr`.
 *
 * Behavior matrix:
 *   - target platform+arch == host  -> full staging: download, extract,
 *     pip install per service, prune, compileall.
 *   - cross-target (e.g. win32 on a mac) -> download + sha256 verify +
 *     extract only. The staged python cannot be executed on this host, so
 *     site-packages are NOT populated; a later run on the matching host
 *     (or a CI runner) completes the service staging.
 *
 * Idempotent: python/postgres extraction is stamped with the archive
 * sha256; per-service pip installs are stamped with a hash of
 * requirements.txt + the local shared packages. Re-runs skip work whose
 * stamp matches; service src/ trees are always refreshed (cheap copy).
 */

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const MANIFEST_PATH = path.join(HERE, "manifest.json");
const CACHE_DIR = path.join(
  os.homedir(),
  ".cache",
  "enterprise-desktop-runtime",
);

const SERVICES = [
  {
    name: "backend",
    copyDirs: ["src", "migrations", "scripts"],
    requireHashes: true,
  },
  {
    name: "backend-facade",
    copyDirs: ["src"],
    requireHashes: true,
  },
  {
    name: "ai-backend",
    copyDirs: ["src", "migrations", "scripts", "config", "skills"],
    requireHashes: false,
  },
];

const SHARED_PACKAGES = [
  path.join(REPO_ROOT, "packages", "service-contracts"),
  path.join(REPO_ROOT, "packages", "audit-chain"),
];

// ---------------------------------------------------------------------------
// small utilities
// ---------------------------------------------------------------------------

function log(msg) {
  process.stdout.write(`[stage] ${msg}\n`);
}

function fail(msg) {
  process.stderr.write(`[stage] ERROR: ${msg}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const args = {
    dest: path.join(REPO_ROOT, "apps", "desktop", "resources"),
    adhocSign: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--platform") args.platform = argv[++i];
    else if (a === "--arch") args.arch = argv[++i];
    else if (a === "--dest") args.dest = path.resolve(argv[++i]);
    else if (a === "--adhoc-sign") args.adhocSign = true;
    else fail(`unknown argument ${a}`);
  }
  if (!["darwin", "win32"].includes(args.platform ?? "")) {
    fail("--platform must be darwin or win32");
  }
  if (!["arm64", "x64"].includes(args.arch ?? "")) {
    fail("--arch must be arm64 or x64");
  }
  return args;
}

function sha256File(file) {
  const hash = createHash("sha256");
  hash.update(fs.readFileSync(file));
  return hash.digest("hex");
}

function sha256String(text) {
  return createHash("sha256").update(text).digest("hex");
}

function run(cmd, argv, opts = {}) {
  const printable = [cmd, ...argv].join(" ");
  const res = spawnSync(cmd, argv, { stdio: "inherit", ...opts });
  if (res.error) fail(`${printable}: ${res.error.message}`);
  if (res.status !== 0 && !opts.allowFailure) {
    fail(`${printable} exited with status ${res.status}`);
  }
  return res.status ?? 0;
}

function readStamp(stampPath) {
  try {
    return JSON.parse(fs.readFileSync(stampPath, "utf8"));
  } catch {
    return null;
  }
}

function writeStamp(stampPath, data) {
  fs.writeFileSync(stampPath, JSON.stringify(data, null, 2) + "\n");
}

function rmrf(p) {
  fs.rmSync(p, { recursive: true, force: true });
}

/** Recursively delete directories named `name` under root. */
function pruneDirsNamed(root, names) {
  if (!fs.existsSync(root)) return 0;
  let removed = 0;
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (names.includes(entry.name)) {
        rmrf(full);
        removed++;
      } else {
        walk(full);
      }
    }
  };
  walk(root);
  return removed;
}

/** Delete RECORD files inside *.dist-info directories (bundles are never pip-uninstalled). */
function pruneDistInfoRecords(sitePackages) {
  if (!fs.existsSync(sitePackages)) return 0;
  let removed = 0;
  for (const entry of fs.readdirSync(sitePackages, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name.endsWith(".dist-info")) {
      const record = path.join(sitePackages, entry.name, "RECORD");
      if (fs.existsSync(record)) {
        fs.rmSync(record);
        removed++;
      }
    }
  }
  return removed;
}

// ---------------------------------------------------------------------------
// download + verify
// ---------------------------------------------------------------------------

async function download(url, expectedSha) {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const filename = decodeURIComponent(new URL(url).pathname.split("/").pop());
  const cached = path.join(CACHE_DIR, filename);

  if (fs.existsSync(cached)) {
    const got = sha256File(cached);
    if (got === expectedSha) {
      log(`cache hit ${filename}`);
      return cached;
    }
    log(
      `cache sha mismatch for ${filename} (have ${got.slice(0, 12)}…); re-downloading`,
    );
    fs.rmSync(cached);
  }

  log(`downloading ${url}`);
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok) fail(`GET ${url} -> HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const got = createHash("sha256").update(buf).digest("hex");
  if (got !== expectedSha) {
    fail(
      `sha256 mismatch for ${filename}\n  expected ${expectedSha}\n  got      ${got}\n` +
        "Refusing to stage an unverified artifact.",
    );
  }
  const tmp = `${cached}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, buf);
  fs.renameSync(tmp, cached);
  log(`verified ${filename} (${(buf.length / 1e6).toFixed(1)} MB)`);
  return cached;
}

// ---------------------------------------------------------------------------
// extraction
// ---------------------------------------------------------------------------

/**
 * Extract the python-build-standalone install_only tar.gz. The archive has a
 * single top-level "python/" directory which becomes <runtimeDir>/python.
 */
function stagePython(runtimeDir, archive, entry) {
  const dest = path.join(runtimeDir, "python");
  const stampPath = path.join(dest, ".stage-stamp.json");
  const stamp = readStamp(stampPath);
  if (stamp?.sha256 === entry.sha256) {
    log("python already staged (stamp match)");
    return dest;
  }
  rmrf(dest);
  const tmp = fs.mkdtempSync(path.join(runtimeDir, ".python-extract-"));
  try {
    run("tar", ["-xf", archive, "-C", tmp]);
    const extractedRoot = path.join(tmp, entry.archive_root);
    if (!fs.existsSync(extractedRoot)) {
      fail(`archive did not contain expected root '${entry.archive_root}'`);
    }
    fs.renameSync(extractedRoot, dest);
  } finally {
    rmrf(tmp);
  }
  writeStamp(stampPath, {
    sha256: entry.sha256,
    staged_at: new Date().toISOString(),
  });
  log(`python staged -> ${path.relative(REPO_ROOT, dest)}`);
  return dest;
}

/**
 * Extract the zonky embedded-postgres jar. Two steps:
 *   1. the .jar is a zip; bsdtar extracts the single inner .txz member;
 *   2. the .txz expands to bin/ lib/ share/ with NO wrapping directory.
 */
function stagePostgres(runtimeDir, archive, entry) {
  const dest = path.join(runtimeDir, "postgres");
  const stampPath = path.join(dest, ".stage-stamp.json");
  const stamp = readStamp(stampPath);
  if (stamp?.sha256 === entry.sha256) {
    log("postgres already staged (stamp match)");
    return dest;
  }
  rmrf(dest);
  const tmp = fs.mkdtempSync(path.join(runtimeDir, ".pg-extract-"));
  try {
    // bsdtar reads zip archives, so the jar needs no unzip dependency.
    run("tar", ["-xf", archive, "-C", tmp, entry.inner_archive]);
    const inner = path.join(tmp, entry.inner_archive);
    if (!fs.existsSync(inner)) {
      fail(`jar did not contain expected member '${entry.inner_archive}'`);
    }
    fs.mkdirSync(dest, { recursive: true });
    run("tar", ["-xf", inner, "-C", dest]); // txz -> bin/ lib/ share/
  } finally {
    rmrf(tmp);
  }
  // Lean out anything a server-only bundle never needs. zonky trees ship
  // only bin/ lib/ share/ (no include/ or doc/), so these are usually no-ops
  // kept as guards against upstream layout changes.
  for (const junk of ["include", "doc"]) rmrf(path.join(dest, junk));
  for (const junk of ["doc", "man"]) rmrf(path.join(dest, "share", junk));
  const bins = fs.existsSync(path.join(dest, "bin"))
    ? fs.readdirSync(path.join(dest, "bin"))
    : [];
  writeStamp(stampPath, {
    sha256: entry.sha256,
    staged_at: new Date().toISOString(),
    bin: bins,
  });
  log(
    `postgres staged -> ${path.relative(REPO_ROOT, dest)} (bin: ${bins.join(", ")})`,
  );
  return dest;
}

// ---------------------------------------------------------------------------
// service staging
// ---------------------------------------------------------------------------

function copyTree(from, to) {
  fs.cpSync(from, to, {
    recursive: true,
    filter: (src) => {
      const base = path.basename(src);
      return (
        base !== "__pycache__" && base !== ".venv" && !base.endsWith(".pyc")
      );
    },
  });
}

function pipDependencyStamp(svc) {
  const reqPath = path.join(
    REPO_ROOT,
    "services",
    svc.name,
    "requirements.txt",
  );
  const parts = [fs.readFileSync(reqPath, "utf8")];
  for (const pkg of SHARED_PACKAGES) {
    parts.push(fs.readFileSync(path.join(pkg, "pyproject.toml"), "utf8"));
    // Include the shared packages' source so edits re-trigger the install.
    const srcRoot = path.join(pkg, "src");
    const files = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__pycache__") walk(full);
        } else if (entry.name.endsWith(".py")) {
          files.push(full);
        }
      }
    };
    walk(srcRoot);
    files.sort();
    for (const f of files) parts.push(f, fs.readFileSync(f, "utf8"));
  }
  return sha256String(parts.join("\n---\n"));
}

/**
 * Verify that everything pinned in requirements.txt landed in site-packages
 * at exactly the pinned version (safety net for the hashed installs and the
 * only integrity check for ai-backend's unhashed pins).
 */
function assertPinnedSetInstalled(pythonExe, sitePackages, svcName) {
  const reqPath = path.join(REPO_ROOT, "services", svcName, "requirements.txt");
  const script = `
import json, pathlib, sys
import importlib.metadata as md

site = sys.argv[1]
req = pathlib.Path(sys.argv[2]).read_text()
pins = {}
for raw in req.splitlines():
    line = raw.strip()
    if not line or line.startswith(("#", "-")):
        continue  # comments, --hash continuation lines, -r includes
    line = line.rstrip("\\\\").strip()  # pip-compile line continuations
    if "==" not in line:
        continue
    name, _, version = line.partition("==")
    version = version.split(";")[0].split()[0].strip()
    name = name.split("[")[0].strip().lower().replace("_", "-")
    pins[name] = version

installed = {}
for dist in md.distributions(path=[site]):
    installed[dist.metadata["Name"].lower().replace("_", "-")] = dist.version

missing = sorted(set(pins) - set(installed))
mismatched = sorted(
    f"{n}: pinned {pins[n]} installed {installed[n]}"
    for n in pins
    if n in installed and installed[n] != pins[n]
)
if missing or mismatched:
    print(json.dumps({"missing": missing, "mismatched": mismatched}))
    sys.exit(1)
print(f"pin-check OK: {len(pins)} pinned, {len(installed)} installed")
`;
  run(pythonExe, ["-c", script, sitePackages, reqPath]);
}

function stageService(runtimeDir, svc, pythonExe, hostExec) {
  const svcSrc = path.join(REPO_ROOT, "services", svc.name);
  const svcDest = path.join(runtimeDir, "services", svc.name);
  fs.mkdirSync(svcDest, { recursive: true });

  // --- source trees: always refreshed (cheap, keeps re-runs honest) -------
  for (const dir of svc.copyDirs) {
    const from = path.join(svcSrc, dir);
    if (!fs.existsSync(from)) continue;
    const to = path.join(svcDest, dir);
    rmrf(to);
    copyTree(from, to);
  }
  log(
    `${svc.name}: copied ${svc.copyDirs.filter((d) => fs.existsSync(path.join(svcSrc, d))).join(", ")}`,
  );

  if (!hostExec) {
    log(
      `${svc.name}: cross-target staging — skipping pip install/compileall (no exec)`,
    );
    return;
  }

  // --- site-packages ------------------------------------------------------
  const sitePackages = path.join(svcDest, "site-packages");
  const stampPath = path.join(svcDest, ".pip-stamp.json");
  const wantStamp = pipDependencyStamp(svc);
  const haveStamp = readStamp(stampPath);
  if (haveStamp?.hash === wantStamp && fs.existsSync(sitePackages)) {
    log(`${svc.name}: site-packages up to date (stamp match)`);
  } else {
    rmrf(sitePackages);
    fs.rmSync(stampPath, { force: true });
    const reqPath = path.join(svcSrc, "requirements.txt");
    const pipBase = [
      "-m",
      "pip",
      "install",
      "--no-compile",
      "--disable-pip-version-check",
      "--target",
      sitePackages,
    ];
    const pipArgs = [...pipBase];
    if (svc.requireHashes) pipArgs.push("--require-hashes");
    pipArgs.push("-r", reqPath);
    log(
      `${svc.name}: pip install -r requirements.txt${svc.requireHashes ? " --require-hashes" : ""}`,
    );
    run(pythonExe, pipArgs);

    // Local shared packages (unhashable local dirs — separate invocation so
    // --require-hashes above stays strict for the pinned third-party set).
    log(`${svc.name}: pip install service-contracts + audit-chain`);
    run(pythonExe, [...pipBase, "--no-deps", ...SHARED_PACKAGES]);

    assertPinnedSetInstalled(pythonExe, sitePackages, svc.name);
    writeStamp(stampPath, {
      hash: wantStamp,
      staged_at: new Date().toISOString(),
    });
  }

  // --- prune ---------------------------------------------------------------
  const prunedTests = pruneDirsNamed(sitePackages, ["tests", "__pycache__"]);
  const prunedRecords = pruneDistInfoRecords(sitePackages);
  pruneDirsNamed(path.join(svcDest, "src"), ["__pycache__"]);
  log(
    `${svc.name}: pruned ${prunedTests} tests/__pycache__ dirs, ${prunedRecords} dist-info RECORDs`,
  );

  // --- bytecode ------------------------------------------------------------
  // compileall exits 1 when ANY file fails to compile; several shipped deps
  // carry py2-only or intentionally-broken fixture files, so a nonzero exit
  // is a warning (bytecode is a startup optimization, not a correctness
  // requirement — anything uncompiled just compiles lazily at runtime).
  const status = run(
    pythonExe,
    ["-m", "compileall", "-q", sitePackages, path.join(svcDest, "src")],
    { allowFailure: true },
  );
  if (status !== 0) {
    log(
      `${svc.name}: compileall reported uncompilable files (non-fatal, see above)`,
    );
  } else {
    log(`${svc.name}: compileall OK`);
  }
}

// ---------------------------------------------------------------------------
// macOS ad-hoc signing (credential-free) — see the --adhoc-sign header note
// ---------------------------------------------------------------------------

// Mach-O magic numbers, read as a big-endian uint32. Covers thin binaries in
// both byte orders (feedface/feedfacf = BE 32/64; cefaedfe/cffaedfe = LE) and
// fat/universal archives (cafebabe/cafebabf and their byte-swapped forms).
const MACHO_MAGICS = new Set([
  0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xcafebabf,
  0xbebafeca, 0xbfbafeca,
]);
const SIGNABLE_EXT = new Set([".so", ".dylib", ".bundle"]);

function isMachO(file) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(4);
    if (fs.readSync(fd, buf, 0, 4, 0) < 4) return false;
    return MACHO_MAGICS.has(buf.readUInt32BE(0));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

/** Executables + loadable libraries that are actually Mach-O. */
function collectSignTargets(root) {
  const targets = [];
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isSymbolicLink()) continue; // sign the real file, not the symlink
      if (e.isDirectory()) {
        walk(full);
        continue;
      }
      if (!e.isFile()) continue;
      let st;
      try {
        st = fs.statSync(full);
      } catch {
        continue;
      }
      const exec = (st.mode & 0o111) !== 0;
      const ext = path.extname(e.name).toLowerCase();
      if ((exec || SIGNABLE_EXT.has(ext)) && isMachO(full)) targets.push(full);
    }
  };
  walk(root);
  return targets;
}

/** Fresh size+mtime fingerprint; recomputed after signing so warm runs skip. */
function signFingerprint(targets) {
  const parts = [];
  for (const f of targets) {
    try {
      const st = fs.statSync(f);
      parts.push(`${f}:${st.size}:${st.mtimeMs}`);
    } catch {
      parts.push(`${f}:missing`);
    }
  }
  return sha256String(parts.join("\n"));
}

function codesignAdhoc(files) {
  // --force replaces any existing signature (incl. a prior ad-hoc one);
  // --timestamp=none keeps it fully offline (no Apple timestamp server).
  return spawnSync(
    "codesign",
    ["--force", "--sign", "-", "--timestamp=none", ...files],
    { stdio: "pipe", encoding: "utf8" },
  );
}

// Stdlib + test-only packages a running service never imports. Pruned to shrink
// the staged tree; safe because none of these sit on any service's import path
// (uvicorn app modules never `import pytest`, `idlelib`, `tkinter`, …).
const STDLIB_CRUFT = [
  "idlelib",
  "ensurepip",
  "tkinter",
  "lib2to3",
  "turtledemo",
  "test",
];
const SITE_CRUFT = ["pytest", "_pytest"];

function pruneRuntimeCruft(runtimeDir) {
  let freed = 0;
  const drop = (p) => {
    if (fs.existsSync(p)) {
      rmrf(p);
      freed++;
    }
  };
  const libDir = path.join(runtimeDir, "python", "lib");
  if (fs.existsSync(libDir)) {
    const pyLib = fs.readdirSync(libDir).find((d) => d.startsWith("python3"));
    if (pyLib) for (const d of STDLIB_CRUFT) drop(path.join(libDir, pyLib, d));
  }
  for (const svc of SERVICES) {
    const sp = path.join(runtimeDir, "services", svc.name, "site-packages");
    for (const d of SITE_CRUFT) drop(path.join(sp, d));
  }
  if (freed) log(`pruned ${freed} unused stdlib/test dirs`);
}

/**
 * `strip -x` drops local/debug symbols but KEEPS globals (PyInit_*, exported
 * dylib symbols), so extensions still load. Must run before signing seals each
 * file. A missing `strip` (non-mac toolchain) is a clean no-op.
 */
function stripSymbols(targets) {
  if (spawnSync("strip", [], { stdio: "ignore" }).error) return 0;
  let stripped = 0;
  for (const f of targets) {
    if (spawnSync("strip", ["-x", f], { stdio: "ignore" }).status === 0) {
      stripped++;
    }
  }
  return stripped;
}

function adhocSignTree(runtimeDir) {
  if (process.platform !== "darwin") {
    log("--adhoc-sign ignored: only meaningful when staging on a macOS host");
    return;
  }
  // `codesign` has no --version; any exit code means it ran. Only a spawn
  // error (ENOENT) means it's missing.
  if (spawnSync("codesign", ["-h"], { stdio: "ignore" }).error) {
    fail("--adhoc-sign requires `codesign` (Xcode command line tools) on PATH");
  }

  // Slim first (deletes cruft; must precede target collection so we never sign
  // a file we're about to remove).
  pruneRuntimeCruft(runtimeDir);

  const targets = collectSignTargets(runtimeDir);
  if (targets.length === 0) {
    log("ad-hoc signing: no Mach-O binaries found (nothing to sign)");
    return;
  }

  // One stamp covers the whole finalize (strip → sign): its fingerprint is the
  // FINAL signed state, so an unchanged tree skips strip+sign on warm re-stages.
  const stampPath = path.join(runtimeDir, ".sign-stamp.json");
  if (readStamp(stampPath)?.fingerprint === signFingerprint(targets)) {
    log(
      `ad-hoc signing: ${targets.length} binaries already signed (stamp match)`,
    );
    return;
  }

  const strippedCount = stripSymbols(targets);
  if (strippedCount) log(`stripped symbols from ${strippedCount} binaries`);

  log(`ad-hoc signing ${targets.length} Mach-O binaries`);
  let signed = 0;
  const failures = [];
  const BATCH = 100;
  for (let i = 0; i < targets.length; i += BATCH) {
    const batch = targets.slice(i, i + BATCH);
    if (codesignAdhoc(batch).status === 0) {
      signed += batch.length;
      continue;
    }
    // A batch failure hides which file broke: re-sign it one at a time.
    for (const f of batch) {
      const r = codesignAdhoc([f]);
      if (r.status === 0) {
        signed++;
      } else {
        const why =
          (r.stderr || "").trim().split("\n").pop() ?? "unknown error";
        failures.push(`${path.relative(runtimeDir, f)}: ${why}`);
      }
    }
  }

  // node's fetch never sets com.apple.quarantine, so CLI-staged files aren't
  // quarantined — but strip it defensively in case a mirror/zip round-trip
  // added one, since quarantine is what triggers Gatekeeper's notarization gate.
  spawnSync("xattr", ["-dr", "com.apple.quarantine", runtimeDir], {
    stdio: "ignore",
  });

  if (failures.length) {
    log(
      `ad-hoc signing: ${signed}/${targets.length} signed, ${failures.length} FAILED:`,
    );
    for (const f of failures.slice(0, 10)) log(`  - ${f}`);
    if (failures.length > 10) log(`  … and ${failures.length - 10} more`);
    fail("ad-hoc signing failed for one or more binaries (see above)");
  }
  log(`ad-hoc signing: ${signed}/${targets.length} signed`);
  // Fingerprint the POST-sign state so an unchanged tree skips next time
  // (codesign rewrites each file, changing its size+mtime).
  writeStamp(stampPath, {
    fingerprint: signFingerprint(targets),
    signed,
    signed_at: new Date().toISOString(),
  });
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

// Stage the built frontend web assets (wallet.html + assets/) arch-agnostically
// at <dest>/web so the supervised facade can serve the SIWE wallet page
// same-origin with /v1/auth/siwe/* (FACADE_WEB_DIST_DIR -> wallet_page_routes.py;
// resolveRuntimePaths().webDir == <base>/web). Single source of truth: the built
// apps/frontend dist. In a dev checkout we build it if absent; the published
// payload ships a pre-built dist which we simply copy.
function stageWebAssets(dest) {
  const distSrc = path.join(REPO_ROOT, "apps", "frontend", "dist");
  const walletPage = path.join(distSrc, "wallet.html");
  if (!fs.existsSync(walletPage)) {
    log("building apps/frontend (dist/wallet.html missing)");
    const npm = process.platform === "win32" ? "npm.cmd" : "npm";
    run(npm, ["run", "build", "--workspace", "@0x-copilot/frontend"], {
      cwd: REPO_ROOT,
    });
  }
  if (!fs.existsSync(walletPage)) {
    fail("frontend build did not produce apps/frontend/dist/wallet.html");
  }
  const webDest = path.join(dest, "web");
  rmrf(webDest);
  fs.mkdirSync(webDest, { recursive: true });
  copyTree(distSrc, webDest);
  log(`web: staged frontend dist -> ${webDest}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const platformKey = `${args.platform}-${args.arch}`;
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));

  const pyEntry = manifest.python.platforms[platformKey];
  const pgEntry = manifest.postgres.platforms[platformKey];
  if (!pyEntry || !pgEntry) fail(`no manifest entries for ${platformKey}`);

  const hostExec =
    args.platform === process.platform && args.arch === process.arch;
  const runtimeDir = path.join(args.dest, "runtime", platformKey);
  fs.mkdirSync(runtimeDir, { recursive: true });

  // Invalidate the completion marker up front: staging-manifest.json is the
  // "this runtime is runnable" signal (isStaged reads it). Removing it now means
  // a run that fails partway leaves the tree looking un-staged rather than
  // falsely runnable, so callers re-stage instead of launching a half-updated tree.
  fs.rmSync(path.join(runtimeDir, "staging-manifest.json"), { force: true });

  log(`staging ${platformKey} -> ${runtimeDir} (host exec: ${hostExec})`);

  const pyArchive = await download(pyEntry.url, pyEntry.sha256);
  const pgArchive = await download(pgEntry.url, pgEntry.sha256);

  const pythonRoot = stagePython(runtimeDir, pyArchive, pyEntry);
  stagePostgres(runtimeDir, pgArchive, pgEntry);

  const pythonExe = path.join(pythonRoot, pyEntry.python_exe);
  if (hostExec) {
    run(pythonExe, ["--version"]);
  }

  for (const svc of SERVICES) {
    stageService(runtimeDir, svc, pythonExe, hostExec);
  }

  // Frontend web assets (SIWE wallet page) — arch-agnostic, staged at <dest>/web.
  stageWebAssets(args.dest);

  // Ad-hoc sign LAST: signing seals each Mach-O, so it must run after every
  // write (extraction, pip, prune, compileall). Only on a macOS host, and only
  // when staging for this host's arch (nothing else is executable here).
  const signed = args.adhocSign && hostExec && args.platform === "darwin";
  if (args.adhocSign && !signed) {
    log(
      `--adhoc-sign skipped (host ${process.platform}-${process.arch}, target ${platformKey}): sign on a matching macOS host`,
    );
  }
  if (signed) adhocSignTree(runtimeDir);

  const stagingManifest = {
    platform: args.platform,
    arch: args.arch,
    host_exec: hostExec,
    python: {
      distribution: manifest.python.distribution,
      version: manifest.python.version,
      release: manifest.python.release,
      sha256: pyEntry.sha256,
      exe: path.join("python", pyEntry.python_exe),
    },
    postgres: {
      distribution: manifest.postgres.distribution,
      version: manifest.postgres.postgres_version,
      artifact_version: manifest.postgres.version,
      sha256: pgEntry.sha256,
    },
    services: SERVICES.map((s) => ({
      name: s.name,
      site_packages: hostExec,
      require_hashes: s.requireHashes,
    })),
    adhoc_signed: signed,
    staged_at: new Date().toISOString(),
  };
  fs.writeFileSync(
    path.join(runtimeDir, "staging-manifest.json"),
    JSON.stringify(stagingManifest, null, 2) + "\n",
  );
  log(
    `done: ${platformKey}${hostExec ? "" : " (download+extract only; run on a matching host to populate site-packages)"}`,
  );
}

await main();
