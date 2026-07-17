#!/usr/bin/env node
/**
 * stage.mjs — stage the self-contained desktop runtime (python + postgres +
 * the three backend services with their dependencies) into
 * apps/desktop/resources/runtime/<platform>-<arch>/.
 *
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
 *   node tools/desktop-runtime/stage.mjs --platform win32 --arch x64
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --dest apps/desktop/resources
 *
 * Zero non-builtin node deps. External processes used: the system `tar`
 * (bsdtar on macOS / Windows 10+; handles .tar.gz, .txz via xz, and reads
 * the zonky .jar because a jar is a zip) and the *staged* python for pip /
 * compileall.
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
  const args = { dest: path.join(REPO_ROOT, "apps", "desktop", "resources") };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--platform") args.platform = argv[++i];
    else if (a === "--arch") args.arch = argv[++i];
    else if (a === "--dest") args.dest = path.resolve(argv[++i]);
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
// main
// ---------------------------------------------------------------------------

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
