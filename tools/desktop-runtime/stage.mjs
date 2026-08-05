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
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --skip-browser
 *   node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64 --include-monty
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
 * the zonky .jar because a jar is a zip), the *staged* python for pip and
 * verification, and (with --adhoc-sign) the system `codesign` + `xattr`.
 *
 * Behavior matrix:
 *   - target platform+arch == host  -> full staging: download, extract,
 *     pip install per service, prune, and verify.
 *   - cross-target (e.g. win32 on a mac) -> download + sha256 verify +
 *     extract only. The staged python cannot be executed on this host, so
 *     site-packages are NOT populated; a later run on the matching host
 *     (or a CI runner) completes the service staging.
 *
 * Idempotent: python/postgres extraction is stamped with the archive
 * sha256; per-service pip installs are stamped with a hash of
 * requirements.txt + the local shared packages. Re-runs skip work whose
 * stamp matches; service src/ trees are always refreshed (cheap copy).
 * The CLI uses --skip-browser by default because browser automation is
 * feature-gated off; `copilot install --browser` opts into Chromium.
 */

import { createHash } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { stageBrowserRuntime } from "./browser-runtime.mjs";
import macosSigning from "./macos-signing.cjs";
import { auditWorkspaceFsAddon } from "./workspace-fs-audit.mjs";

const { signAndVerifyMacAppBundle } = macosSigning;

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const MANIFEST_PATH = path.join(HERE, "manifest.json");
const MONTY_REQUIREMENTS_PATH = path.join(
  HERE,
  "features",
  "monty-requirements.txt",
);
const cacheOverride = process.env.COPILOT_DOWNLOAD_CACHE?.trim();
const CACHE_DIR =
  cacheOverride === undefined || cacheOverride === ""
    ? path.join(os.homedir(), ".cache", "enterprise-desktop-runtime")
    : path.resolve(cacheOverride);

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
    // No "migrations" / "scripts": the ai-backend runs the file-native store,
    // which has no relational schema, so neither the migration chain nor the
    // migrate/restore CLIs that drove it exist any more.
    copyDirs: ["src", "config", "skills"],
    requireHashes: false,
  },
];

// The desktop explicitly disables OTel exporting and keeps structured local
// logs instead. Do not download/install SDK, exporter, instrumentation, or
// test-only packages that no desktop process imports in that posture. Server
// and CI requirements remain untouched and retain full observability.
const DESKTOP_COMMON_EXCLUDES = new Set([
  "asgiref",
  "googleapis-common-protos",
  "grpcio",
  "opentelemetry-exporter-otlp-proto-common",
  "opentelemetry-exporter-otlp-proto-grpc",
  "opentelemetry-instrumentation",
  "opentelemetry-instrumentation-asgi",
  "opentelemetry-instrumentation-dbapi",
  "opentelemetry-instrumentation-fastapi",
  "opentelemetry-instrumentation-httpx",
  "opentelemetry-instrumentation-psycopg",
  "opentelemetry-proto",
  "opentelemetry-sdk",
  "opentelemetry-semantic-conventions",
  "opentelemetry-util-http",
  "protobuf",
  "wrapt",
]);
const DESKTOP_TEST_EXCLUDES = new Set([
  "iniconfig",
  "packaging",
  "pluggy",
  "pygments",
  "pytest",
  "pytest-asyncio",
]);

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
    skipBrowser: false,
    includeMonty: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--platform") args.platform = argv[++i];
    else if (a === "--arch") args.arch = argv[++i];
    else if (a === "--dest") args.dest = path.resolve(argv[++i]);
    else if (a === "--adhoc-sign") args.adhocSign = true;
    else if (a === "--skip-browser") args.skipBrowser = true;
    else if (a === "--include-monty") args.includeMonty = true;
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

function runAsync(cmd, argv, opts = {}) {
  const printable = [cmd, ...argv].join(" ");
  const { allowFailure = false, ...spawnOptions } = opts;
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, argv, { stdio: "inherit", ...spawnOptions });
    child.once("error", (error) => {
      reject(new Error(`${printable}: ${error.message}`));
    });
    child.once("exit", (code, signal) => {
      const status = code ?? 1;
      if (status !== 0 && !allowFailure) {
        reject(
          new Error(
            `${printable} exited with status ${status}` +
              (signal === null ? "" : ` (${signal})`),
          ),
        );
        return;
      }
      resolve(status);
    });
  });
}

const HOST_TOOLCHAIN_ENV = [
  "CC",
  "CXX",
  "CXX11",
  "CXX14",
  "CXX17",
  "CXX1X",
  "CFLAGS",
  "CPPFLAGS",
  "LDFLAGS",
];

function cleanPipBuildEnv() {
  const env = { ...process.env };
  if (env.COPILOT_PRESERVE_BUILD_ENV === "1") return env;
  const removed = HOST_TOOLCHAIN_ENV.filter(
    (name) => env[name] !== undefined && env[name] !== "",
  );
  for (const name of HOST_TOOLCHAIN_ENV) delete env[name];
  if (removed.length > 0) {
    log(
      `pip build: ignored host toolchain overrides (${removed.join(", ")}); ` +
        "set COPILOT_PRESERVE_BUILD_ENV=1 to keep them",
    );
  }
  return env;
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

function normalizeDistributionName(name) {
  return name.toLowerCase().replaceAll("_", "-").replaceAll(".", "-");
}

function desktopRequirementExcludes(svc) {
  const excluded = new Set(DESKTOP_COMMON_EXCLUDES);
  for (const name of DESKTOP_TEST_EXCLUDES) {
    // AI's runtime graph still needs packaging through non-test dependencies.
    if (svc.name === "ai-backend" && name === "packaging") continue;
    excluded.add(name);
  }
  return excluded;
}

function desktopRequirementsText(svc) {
  const reqPath = path.join(
    REPO_ROOT,
    "services",
    svc.name,
    "requirements.txt",
  );
  const excluded = desktopRequirementExcludes(svc);
  const output = [];
  let skippingContinuation = false;
  for (const line of fs.readFileSync(reqPath, "utf8").split("\n")) {
    const requirement = line.match(/^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==/);
    if (requirement !== null) {
      skippingContinuation = excluded.has(
        normalizeDistributionName(requirement[1]),
      );
      if (!skippingContinuation) output.push(line);
      continue;
    }
    if (skippingContinuation && (/^\s+/.test(line) || line === "")) {
      continue;
    }
    skippingContinuation = false;
    output.push(line);
  }
  return `${output.join("\n").trimEnd()}\n`;
}

function pipDependencyStamp(svc) {
  const parts = ["desktop-requirements-v2", desktopRequirementsText(svc)];
  for (const pkg of SHARED_PACKAGES) {
    parts.push(fs.readFileSync(path.join(pkg, "pyproject.toml"), "utf8"));
    // Include the shared packages' source so edits re-trigger the install.
    //
    // EVERY file, not just `.py`. `packages/service-contracts` ships JSON data
    // contracts alongside its modules — `work_ledger.json` is the ledger
    // vocabulary that both languages read — and hashing only `.py` made those
    // invisible here. The install then stamp-matched and was skipped, so a
    // contract change shipped STALE into each service's `site-packages` while
    // the code reading it shipped current. That is not a theoretical skew: it
    // put `KeyError: 'writers'` at module scope in `ledger_models.py` and the
    // packaged app died with "ai-backend crashed 5 times within 300s". Unit
    // tests cannot see it — they import from the source tree, where the JSON is
    // always current; only a packaged run reads the installed copy.
    const srcRoot = path.join(pkg, "src");
    const files = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__pycache__") walk(full);
        } else if (!entry.name.endsWith(".pyc")) {
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

function stageSharedPackages(sitePackages) {
  const staged = [];
  for (const pkg of SHARED_PACKAGES) {
    const sourceRoot = path.join(pkg, "src");
    for (const entry of fs.readdirSync(sourceRoot, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name.endsWith(".egg-info")) continue;
      const destination = path.join(sitePackages, entry.name);
      rmrf(destination);
      copyTree(path.join(sourceRoot, entry.name), destination);
      staged.push(entry.name);
    }
  }
  return staged;
}

/**
 * Verify that everything pinned in requirements.txt landed in site-packages
 * at exactly the pinned version (safety net for the hashed installs and the
 * only integrity check for ai-backend's unhashed pins).
 */
async function assertPinnedSetInstalled(
  pythonExe,
  sitePackages,
  reqPath,
  buildEnv,
) {
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
  await runAsync(pythonExe, ["-c", script, sitePackages, reqPath], {
    env: buildEnv,
  });
}

async function stageService(runtimeDir, svc, pythonExe, hostExec, buildEnv) {
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
      `${svc.name}: cross-target staging — skipping pip install/verification (no exec)`,
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
    const reqPath = path.join(svcDest, ".desktop-requirements.txt");
    fs.writeFileSync(reqPath, desktopRequirementsText(svc));
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
    await runAsync(pythonExe, pipArgs, { env: buildEnv });

    // These are constants-only internal packages with no dependencies or build
    // hooks. Stage their package directories directly instead of launching six
    // isolated PEP 517 builds across the three services.
    const shared = stageSharedPackages(sitePackages);
    log(`${svc.name}: staged shared packages (${shared.join(", ")})`);

    await assertPinnedSetInstalled(pythonExe, sitePackages, reqPath, buildEnv);
    fs.rmSync(reqPath, { force: true });
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

  // Eager compileall generated ~100 MiB of bytecode for modules most desktop
  // users never import. Python compiles only the actually-used startup path.
  log(`${svc.name}: bytecode deferred to first import`);
}

function removeMontyFeature(runtimeDir) {
  const serviceDir = path.join(runtimeDir, "services", "ai-backend");
  const sitePackages = path.join(serviceDir, "site-packages");
  if (fs.existsSync(sitePackages)) {
    for (const entry of fs.readdirSync(sitePackages)) {
      if (
        entry === "pydantic_monty" ||
        /^pydantic_monty-.*\.dist-info$/i.test(entry)
      ) {
        rmrf(path.join(sitePackages, entry));
      }
    }
  }
  fs.rmSync(path.join(serviceDir, ".monty-feature-stamp.json"), {
    force: true,
  });
}

async function stageMontyFeature(
  runtimeDir,
  pythonExe,
  hostExec,
  buildEnv,
  includeMonty,
) {
  const serviceDir = path.join(runtimeDir, "services", "ai-backend");
  const sitePackages = path.join(serviceDir, "site-packages");
  const stampPath = path.join(serviceDir, ".monty-feature-stamp.json");
  if (!includeMonty) {
    removeMontyFeature(runtimeDir);
    log("monty: omitted from base runtime");
    return null;
  }
  if (!hostExec) {
    removeMontyFeature(runtimeDir);
    log("monty: cross-target staging cannot install a native feature wheel");
    return null;
  }

  const wantHash = sha256File(MONTY_REQUIREMENTS_PATH);
  const installedModule = path.join(sitePackages, "pydantic_monty");
  const haveStamp = readStamp(stampPath);
  if (haveStamp?.hash !== wantHash || !fs.existsSync(installedModule)) {
    removeMontyFeature(runtimeDir);
    log("monty: installing optional Code Mode feature");
    await runAsync(
      pythonExe,
      [
        "-m",
        "pip",
        "install",
        "--no-compile",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-deps",
        "--target",
        sitePackages,
        "-r",
        MONTY_REQUIREMENTS_PATH,
      ],
      { env: buildEnv },
    );
    writeStamp(stampPath, {
      hash: wantHash,
      version: "0.0.18",
      staged_at: new Date().toISOString(),
    });
  } else {
    log("monty: optional Code Mode feature up to date (stamp match)");
  }

  await runAsync(
    pythonExe,
    [
      "-c",
      "import importlib.metadata as m, pydantic_monty; assert m.version('pydantic-monty') == '0.0.18'",
    ],
    {
      env: {
        ...buildEnv,
        PYTHONPATH: sitePackages,
      },
    },
  );
  return { version: "0.0.18", enabled: true };
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
const FAT_MACHO_MAGICS = new Set([
  0xcafebabe, 0xcafebabf, 0xbebafeca, 0xbfbafeca,
]);
const SIGNABLE_EXT = new Set([".so", ".dylib", ".bundle"]);

function machoMagic(file) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(4);
    if (fs.readSync(fd, buf, 0, 4, 0) < 4) return null;
    return buf.readUInt32BE(0);
  } catch {
    return null;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function isMachO(file) {
  const magic = machoMagic(file);
  return magic !== null && MACHO_MAGICS.has(magic);
}

function isFatMachO(file) {
  const magic = machoMagic(file);
  return magic !== null && FAT_MACHO_MAGICS.has(magic);
}

/** Executables + loadable libraries that are actually Mach-O. */
function collectSignTargets(root) {
  const targets = [];
  const appBundles = [];
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
        // Treat Chromium as one nested code-sealed unit. Its downloaded outer
        // signature is not guaranteed to be valid after archive extraction;
        // adhocSignTree verifies or repairs the complete app inside-out.
        if (e.name.endsWith(".app")) {
          appBundles.push(full);
          continue;
        }
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
  return { targets, appBundles };
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
  const BATCH = 100;
  for (let i = 0; i < targets.length; i += BATCH) {
    const batch = targets.slice(i, i + BATCH);
    if (
      spawnSync("strip", ["-x", ...batch], { stdio: "ignore" }).status === 0
    ) {
      stripped += batch.length;
      continue;
    }
    // Preserve the old best-effort behavior and isolate any unusual binary.
    for (const file of batch) {
      if (spawnSync("strip", ["-x", file], { stdio: "ignore" }).status === 0) {
        stripped++;
      }
    }
  }
  return stripped;
}

function thinDarwinBinaries(runtimeDir, arch) {
  if (process.platform !== "darwin") return { thinned: 0, bytesSaved: 0 };
  if (spawnSync("lipo", ["-version"], { stdio: "ignore" }).error) {
    log("lipo unavailable; universal binaries were not thinned");
    return { thinned: 0, bytesSaved: 0 };
  }

  const { targets } = collectSignTargets(runtimeDir);
  let thinned = 0;
  let bytesSaved = 0;
  for (const file of targets) {
    if (!isFatMachO(file)) continue;
    const before = fs.statSync(file);
    const tmp = `${file}.thin-${process.pid}`;
    const result = spawnSync("lipo", [file, "-thin", arch, "-output", tmp], {
      stdio: "pipe",
      encoding: "utf8",
    });
    if (result.status !== 0) {
      fs.rmSync(tmp, { force: true });
      const why =
        (result.stderr || "").trim().split("\n").pop() ?? "unknown error";
      fail(
        `could not thin ${path.relative(runtimeDir, file)} for ${arch}: ${why}`,
      );
    }
    fs.chmodSync(tmp, before.mode & 0o7777);
    fs.renameSync(tmp, file);
    const after = fs.statSync(file);
    thinned++;
    bytesSaved += Math.max(0, before.size - after.size);
  }
  if (thinned > 0) {
    log(
      `thinned ${thinned} universal binaries for ${arch} ` +
        `(${(bytesSaved / 1024 / 1024).toFixed(1)} MiB removed)`,
    );
  }
  return { thinned, bytesSaved };
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

  const { targets, appBundles } = collectSignTargets(runtimeDir);
  if (targets.length === 0 && appBundles.length === 0) {
    log("ad-hoc signing: no Mach-O binaries found (nothing to sign)");
    return;
  }

  let repairedAppBundles = 0;
  let preservedAppBundles = 0;
  for (const bundle of appBundles) {
    try {
      const result = signAndVerifyMacAppBundle(bundle, {
        identity: "-",
        preserveValid: true,
      });
      if (result.action === "signed") repairedAppBundles++;
      else preservedAppBundles++;
    } catch (error) {
      fail(
        `nested browser app signing failed: ` +
          `${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  if (appBundles.length > 0) {
    log(
      `nested browser signatures: ${repairedAppBundles} repaired, ` +
        `${preservedAppBundles} preserved after strict verification`,
    );
  }

  // One stamp covers the whole finalize (strip → sign): its fingerprint is the
  // FINAL signed state, so an unchanged tree skips strip+sign on warm re-stages.
  // Nested app verification intentionally precedes this check: a warm stamp
  // never bypasses strict verification of Chromium's recursive code seal.
  const stampPath = path.join(runtimeDir, ".sign-stamp.json");
  if (
    readStamp(stampPath)?.fingerprint ===
    signFingerprint([...targets, ...appBundles])
  ) {
    log(
      `ad-hoc signing: ${targets.length} binaries already signed (stamp match)`,
    );
    return;
  }

  const strippedCount = stripSymbols(targets);
  if (strippedCount) log(`stripped symbols from ${strippedCount} binaries`);

  if (targets.length > 0) {
    log(`ad-hoc signing ${targets.length} Mach-O binaries`);
  }
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
    fingerprint: signFingerprint([...targets, ...appBundles]),
    nested_app_bundles_repaired: repairedAppBundles,
    nested_app_bundles_verified: appBundles.length,
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

  const [pyArchive, pgArchive] = await Promise.all([
    download(pyEntry.url, pyEntry.sha256),
    download(pgEntry.url, pgEntry.sha256),
  ]);

  const pythonRoot = stagePython(runtimeDir, pyArchive, pyEntry);
  stagePostgres(runtimeDir, pgArchive, pgEntry);

  const pythonExe = path.join(pythonRoot, pyEntry.python_exe);
  if (hostExec) {
    run(pythonExe, ["--version"]);
  }

  const pipBuildEnv = cleanPipBuildEnv();
  try {
    await Promise.all(
      SERVICES.map((svc) =>
        stageService(runtimeDir, svc, pythonExe, hostExec, pipBuildEnv),
      ),
    );
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  }

  let monty = null;
  try {
    monty = await stageMontyFeature(
      runtimeDir,
      pythonExe,
      hostExec,
      pipBuildEnv,
      args.includeMonty,
    );
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  }

  let browser = null;
  if (args.skipBrowser) {
    rmrf(path.join(runtimeDir, "browser"));
    log(
      "browser: skipped (install later with `copilot install --browser` when needed)",
    );
  } else {
    try {
      browser = stageBrowserRuntime({
        runtimeDir,
        platform: args.platform,
        arch: args.arch,
        hostExec,
        cacheDir: CACHE_DIR,
        expected: manifest.browser,
        log,
      });
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err));
    }
  }

  // Frontend web assets (SIWE wallet page) — arch-agnostic, staged at <dest>/web.
  stageWebAssets(args.dest);

  // Read-side confinement audit — recorded in staging-manifest.json, so whether
  // the shipped tree has the atomic confined open is never inferred from silence.
  const workspaceFs = auditWorkspaceFsAddon({
    repoRoot: REPO_ROOT,
    platform: args.platform,
    arch: args.arch,
    log,
  });

  // Prune and thin on every distribution path. `--adhoc-sign` used to be the
  // only path that removed runtime cruft, leaving signed DMG builds needlessly
  // larger. Architecture thinning must precede either ad-hoc or Developer-ID
  // signing because lipo invalidates an existing code signature.
  pruneRuntimeCruft(runtimeDir);
  const thinning =
    args.platform === "darwin"
      ? thinDarwinBinaries(runtimeDir, args.arch)
      : { thinned: 0, bytesSaved: 0 };

  // Ad-hoc sign LAST: signing seals each Mach-O, so it must run after every
  // write (extraction, pip, and prune). Only on a macOS host, and only
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
    browser,
    workspace_fs: workspaceFs,
    features: {
      monty,
    },
    services: SERVICES.map((s) => ({
      name: s.name,
      site_packages: hostExec,
      require_hashes: s.requireHashes,
    })),
    native_thinning: {
      files: thinning.thinned,
      bytes_saved: thinning.bytesSaved,
    },
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
