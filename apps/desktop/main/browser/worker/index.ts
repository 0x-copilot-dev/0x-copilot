// AC8 agentic browser — worker child entrypoint.
//
// Runs as a SEPARATE Node child (the packaged Electron binary with
// `ELECTRON_RUN_AS_NODE=1`, compiled to `out/browser-worker/index.js`), NEVER in
// the renderer, preload, or Electron main thread. It is the ONLY place the
// `playwright` dependency is loaded. On boot it:
//
//   1. reads its minimal config from the environment (loopback proxy target,
//      staging root, pinned browser executable),
//   2. starts the egress policy proxy (deny-by-default) and launches Chromium
//      through it with no bypass list,
//   3. emits a single `READY {"version": "..."}` line on stdout that the
//      supervisor's health probe parses (health + version pin), and
//   4. wires a `SessionWorkerPort` over the real engine so read-only actions
//      dispatch against isolated, ephemeral-by-default contexts.
//
// The main<->worker action transport (how the broker forwards a dispatch to
// THIS process) is the next slice's seam; this entry stands up the process,
// the proxy, and the engine so the supervisor lifecycle is real. It is excluded
// from unit tests (it needs a real browser); the session + policy + supervisor
// contracts are covered by the fake-engine suites.

import { mkdir, rm, writeFile } from "node:fs/promises";

import { EgressProxy, hostsFromOrigins } from "../network-policy-proxy";
import { createPlaywrightEngine } from "../browser-engine";
import { BrowserSession } from "../browser-session";
import { ProfileStore } from "../profile-store";
import { SessionWorkerPort } from "../session-worker-port";
import { StagingArea } from "../staging";
import {
  BrowserOriginPolicySchema,
  type BrowserActionRequest,
  type BrowserOriginPolicy,
} from "../protocol";

interface WorkerEnv {
  readonly stagingRoot: string;
  readonly profilesRoot: string;
  readonly ephemeralRoot: string;
  readonly browserVersion: string;
  readonly executablePath?: string;
  readonly originPolicy: BrowserOriginPolicy;
}

function readEnv(env: NodeJS.ProcessEnv): WorkerEnv {
  const rawPolicy = env.BROWSER_ORIGIN_POLICY ?? "{}";
  const originPolicy = BrowserOriginPolicySchema.parse(JSON.parse(rawPolicy));
  return {
    stagingRoot: requireEnv(env, "BROWSER_STAGING_ROOT"),
    profilesRoot: requireEnv(env, "BROWSER_PROFILES_ROOT"),
    ephemeralRoot: requireEnv(env, "BROWSER_EPHEMERAL_ROOT"),
    browserVersion: env.BROWSER_PINNED_VERSION ?? "chromium-pinned",
    executablePath: env.BROWSER_EXECUTABLE_PATH,
    originPolicy,
  };
}

function requireEnv(env: NodeJS.ProcessEnv, key: string): string {
  const value = env[key];
  if (value === undefined || value === "") {
    throw new Error(`browser worker missing required env ${key}`);
  }
  return value;
}

export async function main(
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  const cfg = readEnv(env);
  const approvedHosts = hostsFromOrigins(cfg.originPolicy.topLevelOrigins);
  const proxy = new EgressProxy({ approvedHosts });
  const proxyAddr = await proxy.start();

  const engine = await createPlaywrightEngine({
    proxyServer: `${proxyAddr.host}:${proxyAddr.port}`,
    executablePath: cfg.executablePath,
  });

  const profileFs = {
    mkdir: (p: string, o: { recursive: boolean; mode?: number }) =>
      mkdir(p, o).then(() => undefined),
    writeFile: (p: string, d: string, o?: { mode?: number }) =>
      writeFile(p, d, o),
    readFile: () => Promise.reject(new Error("not used at worker boot")),
    rm: (p: string, o: { recursive: boolean; force: boolean }) => rm(p, o),
    exists: () => Promise.resolve(false),
  };
  const profiles = new ProfileStore({
    profilesRoot: cfg.profilesRoot,
    ephemeralRoot: cfg.ephemeralRoot,
    fs: profileFs,
    browserVersion: cfg.browserVersion,
  });
  const stagingFs = {
    mkdir: (p: string, o: { recursive: boolean; mode?: number }) =>
      mkdir(p, o).then(() => undefined),
    writeFile: (p: string, d: Uint8Array) => writeFile(p, d),
    rm: (p: string, o: { recursive: boolean; force: boolean }) => rm(p, o),
  };

  const workerPort = new SessionWorkerPort({
    createSession: async (binding: BrowserActionRequest["binding"]) => {
      const manifest = await profiles.newEphemeral(binding.workspaceId);
      const staging = new StagingArea({
        stagingRoot: cfg.stagingRoot,
        runId: binding.runId,
        fs: stagingFs,
      });
      const session = new BrowserSession({
        engine,
        manifest,
        originPolicy: cfg.originPolicy,
        staging,
        runId: binding.runId,
      });
      await session.open();
      return session;
    },
  });

  // Health line the supervisor parses (READY + pinned version).
  process.stdout.write(
    `READY ${JSON.stringify({ version: engine.version() })}\n`,
  );

  const shutdown = async (): Promise<void> => {
    await workerPort.closeAll();
    await engine.close();
    await proxy.stop();
    process.exit(0);
  };
  process.on("SIGTERM", () => void shutdown());
  process.on("SIGINT", () => void shutdown());
}

// Only auto-run when invoked as the process entrypoint.
if (process.env.BROWSER_WORKER_ENTRY === "1") {
  main().catch((err: unknown) => {
    process.stderr.write(`browser worker fatal: ${String(err)}\n`);
    process.exit(1);
  });
}
