#!/usr/bin/env node
/**
 * run-local.mjs — boot the STAGED desktop runtime end-to-end on this mac and
 * smoke-test it, exactly the way the Electron supervisor will:
 *
 *   node tools/desktop-runtime/run-local.mjs [--dest apps/desktop/resources] [--keep]
 *
 * Sequence:
 *   1. initdb into a temp dir, pg_ctl start on a free port
 *   2. create databases `backend` + `ai_backend` (via staged python+psycopg —
 *      the zonky postgres bundle ships NO psql/createdb)
 *   3. run both services' scripts/migrate.py apply with the staged interpreter
 *   4. start backend (backend_app.desktop_app:app), ai-backend (in-proc
 *      worker, postgres store), facade — all via staged `python -m uvicorn`
 *      under the single_user_desktop profile with per-run generated secrets
 *   5. health-gate all three, then smoke:
 *        GET /v1/health on backend, ai-backend, facade   (expect 200)
 *        GET {facade}/v1/auth/providers                  (expect 200 + providers list)
 *      No dev IdP anywhere: all three run *_ENVIRONMENT=production, where
 *      /v1/dev/* is never registered.
 *   6. clean shutdown: SIGTERM facade -> ai-backend -> backend, pg_ctl stop.
 *
 * --keep leaves the stack running and prints ports + env for manual poking.
 */

import { randomBytes } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");

// ---------------------------------------------------------------------------
// plumbing
// ---------------------------------------------------------------------------

function log(msg) {
  process.stdout.write(`[run-local] ${msg}\n`);
}

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok, detail });
  log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

function parseArgs(argv) {
  const args = {
    dest: path.join(REPO_ROOT, "apps", "desktop", "resources"),
    keep: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dest") args.dest = path.resolve(argv[++i]);
    else if (a === "--keep") args.keep = true;
    else {
      process.stderr.write(`unknown argument ${a}\n`);
      process.exit(2);
    }
  }
  return args;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

function tail(file, lines = 30) {
  try {
    return fs.readFileSync(file, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "(no log)";
  }
}

function runSync(cmd, argv, opts = {}) {
  const res = spawnSync(cmd, argv, { encoding: "utf8", ...opts });
  if (res.error) throw new Error(`${cmd}: ${res.error.message}`);
  if (res.status !== 0) {
    throw new Error(
      `${cmd} ${argv.join(" ")} exited ${res.status}\nstdout: ${res.stdout}\nstderr: ${res.stderr}`,
    );
  }
  return res;
}

// ---------------------------------------------------------------------------
// stack state
// ---------------------------------------------------------------------------

const state = {
  workDir: null,
  pgData: null,
  pgBin: null,
  children: [], // [{ name, proc, logFile }] in START order
  pgStarted: false,
  keep: false,
};

async function stopProcess(entry, signal = "SIGTERM", timeoutMs = 15000) {
  const { proc, name } = entry;
  if (proc.exitCode !== null || proc.signalCode !== null) return;
  proc.kill(signal);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null || proc.signalCode !== null) return;
    await sleep(200);
  }
  log(`${name} did not exit after ${signal}; sending SIGKILL`);
  proc.kill("SIGKILL");
  await sleep(500);
}

async function shutdown() {
  // SIGTERM in reverse dependency order: facade -> ai-backend -> backend.
  for (const entry of [...state.children].reverse()) {
    log(`stopping ${entry.name}`);
    await stopProcess(entry);
  }
  if (state.pgStarted) {
    log("stopping postgres (pg_ctl stop -m fast)");
    spawnSync(
      path.join(state.pgBin, "pg_ctl"),
      ["-D", state.pgData, "-m", "fast", "stop"],
      {
        stdio: "ignore",
      },
    );
    state.pgStarted = false;
  }
  if (state.workDir && !state.keep) {
    fs.rmSync(state.workDir, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// service spawning
// ---------------------------------------------------------------------------

/** Minimal clean environment: nothing from the dev shell leaks in. */
function baseEnv() {
  const env = {
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    HOME: os.homedir(),
    TMPDIR: os.tmpdir(),
    LANG: process.env.LANG ?? "en_US.UTF-8",
  };
  return env;
}

function servicePythonEnv(runtimeDir, svcName) {
  const svc = path.join(runtimeDir, "services", svcName);
  return {
    ...baseEnv(),
    PYTHONPATH: `${path.join(svc, "site-packages")}:${path.join(svc, "src")}`,
    PYTHONDONTWRITEBYTECODE: "1",
  };
}

function startService({
  name,
  runtimeDir,
  pythonExe,
  appModule,
  port,
  extraEnv,
  logDir,
}) {
  const logFile = path.join(logDir, `${name}.log`);
  const fd = fs.openSync(logFile, "a");
  const env = { ...servicePythonEnv(runtimeDir, name), ...extraEnv };
  const proc = spawn(
    pythonExe,
    ["-m", "uvicorn", appModule, "--host", "127.0.0.1", "--port", String(port)],
    { env, stdio: ["ignore", fd, fd] },
  );
  const entry = { name, proc, logFile, port };
  state.children.push(entry);
  log(`${name} starting (pid ${proc.pid}, port ${port}, log ${logFile})`);
  return entry;
}

async function waitHealthy(entry, timeoutMs = 90000) {
  const url = `http://127.0.0.1:${entry.port}/v1/health`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (entry.proc.exitCode !== null) {
      throw new Error(
        `${entry.name} exited with code ${entry.proc.exitCode} before becoming healthy.\n--- log tail ---\n${tail(entry.logFile)}`,
      );
    }
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return await res.json();
    } catch {
      /* not up yet */
    }
    await sleep(400);
  }
  throw new Error(
    `${entry.name} not healthy after ${timeoutMs / 1000}s.\n--- log tail ---\n${tail(entry.logFile)}`,
  );
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  state.keep = args.keep;

  if (process.platform !== "darwin") {
    throw new Error("run-local.mjs currently boots the darwin runtime only");
  }
  const platformKey = `darwin-${process.arch}`;
  const runtimeDir = path.join(args.dest, "runtime", platformKey);
  const stagingManifest = path.join(runtimeDir, "staging-manifest.json");
  if (!fs.existsSync(stagingManifest)) {
    throw new Error(
      `no staged runtime at ${runtimeDir} — run:\n  node tools/desktop-runtime/stage.mjs --platform darwin --arch ${process.arch}`,
    );
  }
  const staged = JSON.parse(fs.readFileSync(stagingManifest, "utf8"));
  if (!staged.host_exec) {
    throw new Error(
      `runtime at ${runtimeDir} was staged download-only (host_exec=false)`,
    );
  }

  const pythonExe = path.join(runtimeDir, staged.python.exe);
  state.pgBin = path.join(runtimeDir, "postgres", "bin");

  // Short base dir: unix socket paths must stay under ~104 bytes on macOS.
  state.workDir = fs.mkdtempSync(path.join(os.tmpdir(), "esd-"));
  state.pgData = path.join(state.workDir, "pgdata");
  const sockDir = path.join(state.workDir, "sock");
  const logDir = path.join(state.workDir, "logs");
  fs.mkdirSync(sockDir);
  fs.mkdirSync(logDir);
  log(`workdir ${state.workDir}`);

  // Per-run generated secrets — never checked in, never reused.
  const secrets = {
    authSecret: randomBytes(32).toString("hex"),
    serviceToken: randomBytes(32).toString("hex"),
    vaultSecret: randomBytes(32).toString("hex"), // >= 32 chars required by LocalTokenVault
    auditHmacKey: randomBytes(32).toString("hex"), // hex-encoded, >= 32 bytes required by audit-chain
  };

  const [pgPort, backendPort, aiPort, facadePort] = [
    await freePort(),
    await freePort(),
    await freePort(),
    await freePort(),
  ];

  // --- 1. initdb + pg_ctl start -------------------------------------------
  log(`initdb -> ${state.pgData}`);
  runSync(
    path.join(state.pgBin, "initdb"),
    [
      "-D",
      state.pgData,
      "-U",
      "postgres",
      "-A",
      "trust",
      "-E",
      "UTF8",
      "--no-locale",
      "--no-instructions",
    ],
    { env: baseEnv() },
  );
  log(`pg_ctl start on 127.0.0.1:${pgPort}`);
  runSync(
    path.join(state.pgBin, "pg_ctl"),
    [
      "-D",
      state.pgData,
      "-l",
      path.join(logDir, "postgres.log"),
      "-o",
      `-p ${pgPort} -c listen_addresses=127.0.0.1 -c unix_socket_directories=${sockDir}`,
      "-w",
      "start",
    ],
    { env: baseEnv() },
  );
  state.pgStarted = true;
  record("postgres boot", true, `port ${pgPort}`);

  // --- 2. create databases (staged python + psycopg; no psql in bundle) ---
  // Two URL flavors on purpose: the apps' psycopg pools take libpq-style
  // postgresql:// URLs, but yoyo (scripts/migrate.py) resolves the bare
  // postgresql:// scheme to the psycopg2 driver — which is not installed.
  // yoyo needs the explicit +psycopg (v3) driver marker, same as CI's
  // postgres-restore-drill workflow.
  const backendDbUrl = `postgresql://postgres@127.0.0.1:${pgPort}/backend`;
  const aiDbUrl = `postgresql://postgres@127.0.0.1:${pgPort}/ai_backend`;
  const backendMigrateUrl = `postgresql+psycopg://postgres@127.0.0.1:${pgPort}/backend`;
  const aiMigrateUrl = `postgresql+psycopg://postgres@127.0.0.1:${pgPort}/ai_backend`;
  runSync(
    pythonExe,
    [
      "-c",
      `
import psycopg
conn = psycopg.connect("postgresql://postgres@127.0.0.1:${pgPort}/postgres", autocommit=True)
for db in ("backend", "ai_backend"):
    exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone()
    if not exists:
        conn.execute(f'CREATE DATABASE "{db}"')
        print(f"created database {db}")
    else:
        print(f"database {db} already exists")
conn.close()
`,
    ],
    { env: servicePythonEnv(runtimeDir, "backend") },
  );
  record("create databases", true, "backend, ai_backend");

  // --- 3. migrations via the staged interpreter ---------------------------
  log("backend migrations: scripts/migrate.py apply");
  runSync(
    pythonExe,
    [
      path.join(runtimeDir, "services", "backend", "scripts", "migrate.py"),
      "apply",
    ],
    {
      env: {
        ...servicePythonEnv(runtimeDir, "backend"),
        BACKEND_DATABASE_URL: backendMigrateUrl,
      },
    },
  );
  record("backend migrate apply", true);

  log("ai-backend migrations: scripts/migrate.py apply");
  runSync(
    pythonExe,
    [
      path.join(runtimeDir, "services", "ai-backend", "scripts", "migrate.py"),
      "apply",
    ],
    {
      env: {
        ...servicePythonEnv(runtimeDir, "ai-backend"),
        RUNTIME_DATABASE_URL: aiMigrateUrl,
      },
    },
  );
  record("ai-backend migrate apply", true);

  // --- 4. start the three services -----------------------------------------
  // Shared desktop-profile env (see docs/deployment/profiles.md and
  // backend_app/desktop_app.py). No dev IdP: every service runs its
  // *_ENVIRONMENT=production so /v1/dev/* is never registered.
  const profileEnv = {
    ENTERPRISE_DEPLOYMENT_PROFILE: "single_user_desktop",
    OTEL_SDK_DISABLED: "true",
    ENTERPRISE_SERVICE_TOKEN: secrets.serviceToken,
  };

  const backend = startService({
    name: "backend",
    runtimeDir,
    pythonExe,
    appModule: "backend_app.desktop_app:app",
    port: backendPort,
    logDir,
    extraEnv: {
      ...profileEnv,
      BACKEND_ENVIRONMENT: "production",
      DATABASE_URL: backendDbUrl,
      ENTERPRISE_AUTH_SECRET: secrets.authSecret,
      MCP_TOKEN_VAULT_SECRET: secrets.vaultSecret,
      MCP_TOKEN_VAULT_BACKEND: "local",
      AUDIT_HMAC_KEY: secrets.auditHmacKey,
    },
  });

  const ai = startService({
    name: "ai-backend",
    runtimeDir,
    pythonExe,
    appModule: "runtime_api.app:app",
    port: aiPort,
    logDir,
    extraEnv: {
      ...profileEnv,
      RUNTIME_ENVIRONMENT: "production",
      RUNTIME_STORE_BACKEND: "postgres",
      RUNTIME_START_IN_PROCESS_WORKER: "true",
      // Migrations are a separate boot step (step 3 above) — the desktop
      // supervisor owns them, mirroring backend_app.desktop_app's contract.
      // Without this the store's startup auto-apply would re-enter yoyo with
      // the plain postgresql:// URL and crash on the psycopg2 default driver.
      RUNTIME_MIGRATIONS_AUTO_APPLY: "false",
      // Round 2 — this IS the user's machine, so surface the local-models
      // (Ollama) section. It only becomes usable once the user installs
      // Ollama (the section shows setup steps until then). Default Ollama
      // endpoint is http://localhost:11434/v1.
      RUNTIME_ENABLE_LOCAL_MODELS: "true",
      DATABASE_URL: aiDbUrl,
      AUDIT_HMAC_KEY: secrets.auditHmacKey,
      MCP_BACKEND_REGISTRY_URL: `http://127.0.0.1:${backendPort}`,
      SKILLS_BACKEND_REGISTRY_URL: `http://127.0.0.1:${backendPort}`,
    },
  });

  const facade = startService({
    name: "backend-facade",
    runtimeDir,
    pythonExe,
    appModule: "backend_facade.app:app",
    port: facadePort,
    logDir,
    extraEnv: {
      ...profileEnv,
      FACADE_ENVIRONMENT: "production",
      ENTERPRISE_AUTH_SECRET: secrets.authSecret,
      BACKEND_URL: `http://127.0.0.1:${backendPort}`,
      AI_BACKEND_URL: `http://127.0.0.1:${aiPort}`,
    },
  });

  // --- 5. health gates ------------------------------------------------------
  for (const entry of [backend, ai, facade]) {
    const health = await waitHealthy(entry);
    const profile = health.deployment_profile ?? "(none reported)";
    const ok = profile === "single_user_desktop";
    record(
      `${entry.name} /v1/health`,
      ok,
      ok
        ? `profile ${profile}`
        : `unexpected deployment_profile ${profile}: ${JSON.stringify(health)}`,
    );
    if (!ok) throw new Error(`${entry.name} healthy but wrong profile`);
  }

  // --- real smoke: facade -> backend proxy without any dev identity --------
  const providersRes = await fetch(
    `http://127.0.0.1:${facadePort}/v1/auth/providers`,
    {
      signal: AbortSignal.timeout(10000),
    },
  );
  const providersBody = await providersRes.text();
  let providersOk = providersRes.status === 200;
  let providerDetail = `HTTP ${providersRes.status}`;
  if (providersOk) {
    try {
      const parsed = JSON.parse(providersBody);
      const list = parsed.providers;
      providersOk = Array.isArray(list);
      providerDetail = providersOk
        ? `providers: [${list.map((p) => p.id ?? p.provider_id ?? "?").join(", ")}]`
        : `no providers array in ${providersBody.slice(0, 200)}`;
    } catch {
      providersOk = false;
      providerDetail = `non-JSON body: ${providersBody.slice(0, 200)}`;
    }
  } else {
    providerDetail += ` body: ${providersBody.slice(0, 300)}`;
  }
  record("facade /v1/auth/providers", providersOk, providerDetail);
  if (!providersOk) throw new Error("providers smoke failed");

  // sanity: production means no dev IdP mint anywhere.
  const devMint = await fetch(
    `http://127.0.0.1:${facadePort}/v1/dev/identity/mint`,
    {
      method: "POST",
      signal: AbortSignal.timeout(5000),
    },
  ).catch(() => null);
  const devMintOk = devMint !== null && [404, 405].includes(devMint.status);
  record(
    "dev IdP absent in production",
    devMintOk,
    devMint ? `HTTP ${devMint.status}` : "request failed",
  );

  if (args.keep) {
    log("");
    log("--keep: stack left running.");
    log(`  postgres   127.0.0.1:${pgPort} (data: ${state.pgData})`);
    log(`  backend    http://127.0.0.1:${backendPort}`);
    log(`  ai-backend http://127.0.0.1:${aiPort}`);
    log(`  facade     http://127.0.0.1:${facadePort}`);
    log(`  logs       ${logDir}`);
    log(
      `  stop with: kill <pids>; ${path.join(state.pgBin, "pg_ctl")} -D ${state.pgData} -m fast stop`,
    );
  }
}

let failed = false;
try {
  await main();
} catch (err) {
  failed = true;
  record("run", false, String(err.message ?? err));
} finally {
  if (!state.keep || failed) {
    await shutdown();
  }
  const passes = results.filter((r) => r.ok).length;
  const fails = results.length - passes;
  log("");
  log("================ SUMMARY ================");
  for (const r of results)
    log(
      `  ${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? ` — ${r.detail}` : ""}`,
    );
  log(`=========================================`);
  log(
    fails === 0
      ? `PASS (${passes}/${results.length})`
      : `FAIL (${fails} failing, ${passes} passing)`,
  );
  process.exit(fails === 0 ? 0 : 1);
}
