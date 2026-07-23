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
 *        run smoke — sign in through the REAL SIWE ramp (no dev mint),
 *          POST a conversation + run, and consume the run's SSE stream to a
 *          terminal event. Hermetic: ai-backend runs RUNTIME_FAKE_MODEL=1
 *          (deterministic fake streaming model, keyless, no network), so the
 *          run genuinely executes through the worker + graph + streamer and
 *          emits model_delta + run_completed with no key. Asserts >=1
 *          model_delta, a terminal run_completed, and NO run_failed.
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
// hermetic run smoke: real SIWE sign-in -> POST run -> consume SSE stream
// ---------------------------------------------------------------------------

/** RFC-3339 without millis — the byte shape the backend SIWE parser expects. */
function fmtTs(d) {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Sign in through the REAL SIWE ramp (nonce -> EIP-4361 sign -> verify) and
 * return a user bearer for the no-dev-mint facade. Mirrors the minimal flow in
 * tools/cli-testing/harness/siwe-session.mjs with an ephemeral wallet; the
 * single_user_desktop profile allows self-signup, so the first sign-in
 * provisions the user.
 *
 * Origin binding: the desktop backend leaves SIWE_ORIGIN unset, so the verifier
 * falls back to magic_link_base_url ("http://localhost:5173"); chain 1 is in the
 * default SIWE_ALLOWED_CHAIN_IDS; the statement is baked into the backend.
 */
async function acquireSiweBearer(facadeBase) {
  // viem is a workspace dependency (resolved by walking up to the repo-root
  // node_modules); imported lazily so a missing install never gates the
  // boot / health / providers checks above.
  const { privateKeyToAccount, generatePrivateKey } =
    await import("viem/accounts");
  const account = privateKeyToAccount(generatePrivateKey());
  const address = account.address;

  const CHAIN_ID = 1;
  const DOMAIN = "localhost:5173";
  const URI = "http://localhost:5173";
  const STATEMENT = "Sign in to Copilot"; // must match backend SIWE_STATEMENT

  const nonceRes = await fetch(`${facadeBase}/v1/auth/siwe/nonce`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ address, chain_id: CHAIN_ID }),
    signal: AbortSignal.timeout(10000),
  });
  if (!nonceRes.ok) {
    throw new Error(
      `siwe nonce HTTP ${nonceRes.status}: ${(await nonceRes.text()).slice(0, 200)}`,
    );
  }
  const nonceBody = await nonceRes.json();
  const nonce = nonceBody.nonce ?? nonceBody.value;
  if (!nonce) {
    throw new Error(
      `siwe nonce missing in ${JSON.stringify(nonceBody).slice(0, 200)}`,
    );
  }

  const issuedAt = new Date(Date.now() - 5000);
  const expiration = new Date(Date.now() + 9 * 60 * 1000);
  const message =
    `${DOMAIN} wants you to sign in with your Ethereum account:\n` +
    `${address}\n` +
    `\n` +
    `${STATEMENT}\n` +
    `\n` +
    `URI: ${URI}\n` +
    `Version: 1\n` +
    `Chain ID: ${CHAIN_ID}\n` +
    `Nonce: ${nonce}\n` +
    `Issued At: ${fmtTs(issuedAt)}\n` +
    `Expiration Time: ${fmtTs(expiration)}`;
  const signature = await account.signMessage({ message });

  const verifyRes = await fetch(`${facadeBase}/v1/auth/siwe/verify`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, signature }),
    signal: AbortSignal.timeout(10000),
  });
  if (!verifyRes.ok) {
    throw new Error(
      `siwe verify HTTP ${verifyRes.status}: ${(await verifyRes.text()).slice(0, 200)}`,
    );
  }
  const verifyBody = await verifyRes.json();
  if (!verifyBody.bearer_token) {
    throw new Error(
      `siwe verify returned no bearer_token: ${JSON.stringify(verifyBody).slice(0, 200)}`,
    );
  }
  return {
    bearer: verifyBody.bearer_token,
    address,
    userId: verifyBody.user_id,
  };
}

/**
 * Create a conversation + run and consume its SSE stream to a terminal event.
 * Returns the observed `event_type` sequence. Hermetic because ai-backend runs
 * RUNTIME_FAKE_MODEL=1 (fake streaming model, credential gate bypassed), so the
 * real worker + Deep Agents graph + streamer produce genuine events with no key.
 *
 * SSE framing (runtime_api/sse/adapter.py): each frame is
 *   event: runtime_event\n id: <seq>\n data: <RuntimeEventEnvelope JSON>\n\n
 * The event kind lives in `data.event_type`; the frame `id:` line and the
 * payload's `sequence_no` both carry the monotonic per-run sequence.
 *
 * READ CONTRACT (terminal-gated, resumable): keep reading until a TERMINAL
 * event (`run_completed`/`run_failed`/`run_cancelled`) is observed OR an overall
 * deadline elapses — never stop at `final_response`, a single connection's close,
 * or a short fixed window. A single SSE connection's body can END after
 * `final_response` but BEFORE the trailing `run_completed` is emitted on that
 * socket (observed on the slower GitHub macOS runner: the run streamed
 * `run_started → reasoning → model_delta×N → final_response`, then the read
 * window closed with no terminal event even though the run completed). When the
 * body ends without a terminal event and deadline budget remains, we RECONNECT
 * from the highest `sequence_no` seen (`after_sequence=<lastSeq>`, the documented
 * resume model — no replay of already-seen events) and keep reading. Only the
 * overall deadline or a terminal event ends the read.
 *
 * Non-throwing: a stream-open/transport failure is returned as `{ error }` (not
 * thrown) so the caller records a diagnostic and clean teardown / --keep still
 * run. On deadline timeout the full observed `events` sequence is returned so a
 * real hang is diagnosable.
 */
async function driveRunToStream(
  facadeBase,
  bearer,
  { streamTimeoutMs = 60000 } = {},
) {
  const authHeaders = {
    authorization: `Bearer ${bearer}`,
    "content-type": "application/json",
  };

  const convRes = await fetch(`${facadeBase}/v1/agent/conversations`, {
    method: "POST",
    headers: authHeaders,
    body: JSON.stringify({ title: "Tier B run smoke" }),
    signal: AbortSignal.timeout(15000),
  });
  if (!convRes.ok) {
    throw new Error(
      `create conversation HTTP ${convRes.status}: ${(await convRes.text()).slice(0, 200)}`,
    );
  }
  const conversation = await convRes.json();
  const conversationId = conversation.conversation_id;
  if (!conversationId) {
    throw new Error(
      `no conversation_id in ${JSON.stringify(conversation).slice(0, 200)}`,
    );
  }

  const runRes = await fetch(`${facadeBase}/v1/agent/runs`, {
    method: "POST",
    headers: authHeaders,
    body: JSON.stringify({
      conversation_id: conversationId,
      user_input: "Say hello.",
    }),
    signal: AbortSignal.timeout(15000),
  });
  if (!runRes.ok) {
    throw new Error(
      `create run HTTP ${runRes.status}: ${(await runRes.text()).slice(0, 200)}`,
    );
  }
  const run = await runRes.json();
  const runId = run.run_id;
  if (!runId) {
    throw new Error(`no run_id in ${JSON.stringify(run).slice(0, 200)}`);
  }

  const TERMINAL = new Set(["run_completed", "run_failed", "run_cancelled"]);
  const events = [];
  let terminal = null;
  let lastSeq = 0; // highest sequence_no seen; drives after_sequence on reconnect
  let error = null;
  const deadline = Date.now() + streamTimeoutMs;

  // Terminal-gated resumable read: reconnect from `lastSeq` whenever a single
  // connection's body ends without a terminal event, until terminal OR the
  // overall deadline. `timedOut` distinguishes a real hang (deadline hit) from
  // a clean run-completed for the caller's diagnostics.
  let timedOut = false;
  outer: while (terminal === null && Date.now() < deadline) {
    error = null; // a successful reconnect clears a prior transient drop
    const controller = new AbortController();
    const remaining = deadline - Date.now();
    const timer = setTimeout(
      () => {
        timedOut = true;
        controller.abort();
      },
      Math.max(1, remaining),
    );
    try {
      const streamRes = await fetch(
        `${facadeBase}/v1/agent/runs/${runId}/stream?after_sequence=${lastSeq}`,
        {
          headers: {
            authorization: `Bearer ${bearer}`,
            accept: "text/event-stream",
          },
          signal: controller.signal,
        },
      );
      if (!streamRes.ok) {
        error = `open stream HTTP ${streamRes.status}: ${(await streamRes.text()).slice(0, 200)}`;
        break outer; // transport-level failure — stop, stay non-throwing
      }
      const decoder = new TextDecoder();
      let buf = "";
      for await (const chunk of streamRes.body) {
        buf += decoder.decode(chunk, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = frame
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).replace(/^ /, ""))
            .join("\n");
          if (!data) continue;
          let payload;
          try {
            payload = JSON.parse(data);
          } catch {
            continue; // partial/non-JSON frame — keep reading
          }
          const type = payload.event_type;
          if (!type || type === "heartbeat") continue;
          // Advance the resume cursor from real persisted events only, so an
          // after_sequence reconnect never skips or replays an event.
          const seq = Number(payload.sequence_no);
          if (Number.isFinite(seq) && seq > lastSeq) lastSeq = seq;
          events.push(type);
          if (TERMINAL.has(type)) {
            terminal = type;
            break outer;
          }
        }
      }
      // Body ended without a terminal event. If deadline budget remains, the
      // while-guard loops us back to reconnect from lastSeq; otherwise we exit.
      // Small yield so a server that (pathologically) closes fast without
      // holding the connection open cannot busy-spin the deadline window.
      if (terminal === null && Date.now() < deadline) await sleep(100);
    } catch (err) {
      if (controller.signal.aborted) {
        // Deadline abort (timedOut) or teardown — stop reading; the while-guard
        // ends the loop. A non-deadline abort simply ends the read gracefully.
        if (timedOut) break outer;
      } else {
        // Transport error mid-stream (e.g. connection reset). Record it and try
        // to resume while deadline budget remains — a transient drop must not
        // masquerade as "no run_completed". Small backoff so a fast-failing
        // fetch cannot busy-loop the whole deadline window.
        error = String(err.message ?? err);
        if (Date.now() < deadline) await sleep(250);
      }
    } finally {
      clearTimeout(timer);
      controller.abort(); // release the upstream socket
    }
  }

  if (terminal === null && !error && timedOut) {
    error =
      `stream did not reach a terminal event within ${streamTimeoutMs / 1000}s; ` +
      `saw [${events.join(", ")}]`;
  }

  return { runId, events, terminal, error };
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
      // Account-merge runtime leg: the backend saga calls ai-backend over
      // HTTP; without this the saga fails closed at its runtime checkpoint.
      AI_BACKEND_URL: `http://127.0.0.1:${aiPort}`,
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
      // BYOK trusted-backend lane — mirror service-env.ts so the supervised
      // topology matches the real desktop app: BACKEND_BASE_URL + the shared
      // service token (profileEnv) enable the per-user policy + BYOK-key fetch at
      // run-create. Without it the lane is off (Null resolver) and BYOK runs
      // silently drop keys.
      BACKEND_BASE_URL: `http://127.0.0.1:${backendPort}`,
      // Hermetic run smoke (step 5 below): the deterministic fake model
      // (#140) makes runs execute with NO API key and NO network — it
      // streams model_delta + reasoning + final_response and completes,
      // and ModelConfigResolver treats fake mode as keyless so the
      // "Missing API key" credential gate never fires. Fail-closed: the
      // real desktop never sets this flag. Without it the run smoke would
      // need a live provider key, which this hermetic harness must not.
      RUNTIME_FAKE_MODEL: "1",
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
      // PRD-P8 D2 — same machine, so also authorise DETECTING the Ollama
      // binary and STARTING it (POST /v1/local-models/runtime/start). This is
      // what separates "Ollama not installed" from "Ollama stopped responding"
      // in the first-run card, and it is what "Restart Ollama" calls. Desktop
      // only: containerised self-host leaves it false because its
      // OLLAMA_BASE_URL points at host.docker.internal — it can neither see nor
      // spawn a host binary. Mirrors apps/desktop/main/services/service-env.ts.
      RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME: "true",
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

  // --- hermetic run smoke: sign in (real SIWE) then drive a run to stream ---
  // Self-contained try/catch (like the dev-mint probe): a failure is recorded
  // with diagnostics and surfaces in the summary/exit code, but never aborts
  // the clean teardown or the --keep handoff below.
  const facadeBase = `http://127.0.0.1:${facadePort}`;
  let bearer = null;
  try {
    const session = await acquireSiweBearer(facadeBase);
    bearer = session.bearer;
    record(
      "siwe wallet sign-in (production posture)",
      true,
      `user ${session.userId} (${session.address})`,
    );
  } catch (err) {
    record(
      "siwe wallet sign-in (production posture)",
      false,
      String(err.message ?? err),
    );
  }

  if (bearer) {
    try {
      const { runId, events, terminal, error } = await driveRunToStream(
        facadeBase,
        bearer,
      );
      const sawDelta = events.includes("model_delta");
      const sawCompleted = events.includes("run_completed");
      const sawFailed = events.includes("run_failed");
      const ok = sawDelta && sawCompleted && !sawFailed;
      record(
        "run stream smoke (hermetic fake model)",
        ok,
        ok
          ? `run ${runId} streamed ${events.length} events to ${terminal}`
          : `events=[${events.join(", ")}] terminal=${terminal ?? "none"}; ` +
              `want >=1 model_delta + run_completed and no run_failed` +
              (error ? `; read error: ${error}` : ""),
      );
    } catch (err) {
      record(
        "run stream smoke (hermetic fake model)",
        false,
        String(err.message ?? err),
      );
    }
  } else {
    record(
      "run stream smoke (hermetic fake model)",
      false,
      "skipped: no bearer from SIWE sign-in",
    );
  }

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
