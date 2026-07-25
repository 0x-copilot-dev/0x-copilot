// OS-facing production composition for the desktop browser subsystem.
//
// Electron main owns this module. It spawns the browser worker with a curated
// environment and authenticated IPC, supervises version/health/restarts, then
// exposes the loopback broker only after the child is proven healthy. It does
// not import Playwright or any browser-process object.

import { randomBytes } from "node:crypto";
import { spawn as nodeSpawn, type SpawnOptions } from "node:child_process";
import { join } from "node:path";

import { BrowserBroker, type BrowserBrokerHandle } from "./browser-broker";
import {
  BrowserWorkerSupervisor,
  type BrowserWorkerState,
  type WorkerChildLike,
} from "./browser-supervisor";
import { MainBrowserReadAuthority } from "./read-authority";
import {
  PINNED_CHROMIUM_VERSION,
  BrowserOriginPolicySchema,
  type BrowserOriginPolicy,
} from "./protocol";
import {
  BROWSER_WORKER_RPC_TOKEN_ENV,
  BrowserWorkerRpcClient,
  type BrowserWorkerIpcChild,
} from "./worker-rpc";

const WORKER_READY_TIMEOUT_MS = 20_000;
const WORKER_RPC_TOKEN_BYTES = 32;
const ORIGIN_POLICY_ENV = "BROWSER_ORIGIN_POLICY";

const WORKER_ENV_ALLOWLIST = [
  "PATH",
  "HOME",
  "USERPROFILE",
  "SYSTEMROOT",
  "TEMP",
  "TMP",
  "TMPDIR",
  "LANG",
  "LC_ALL",
  // Playwright is a packaged dependency. These two remain useful for a
  // staged/dev runtime and are main-owned deployment configuration.
  "NODE_PATH",
  "PLAYWRIGHT_BROWSERS_PATH",
] as const;

export type SpawnBrowserWorker = (
  command: string,
  args: readonly string[],
  options: SpawnOptions,
) => BrowserWorkerIpcChild;

export interface ProductionDesktopBrowserConfig {
  readonly userDataDir: string;
  readonly workerEntryPath: string;
  readonly electronExecutable: string;
  /** Canonical Electron-main-private path validated from the staged manifest. */
  readonly browserExecutablePath: string;
  readonly processEnv: Readonly<Record<string, string | undefined>>;
  readonly platform?: NodeJS.Platform;
  readonly spawn?: SpawnBrowserWorker;
  readonly log?: (message: string) => void;
  readonly onStateChange?: (state: BrowserWorkerState, reason?: string) => void;
}

export interface ProductionDesktopBrowserSubsystem {
  readonly broker: BrowserBroker;
  readonly supervisor: BrowserWorkerSupervisor;
  readonly workerPort: BrowserWorkerRpcClient;
  start(): Promise<BrowserBrokerHandle>;
  stop(): Promise<void>;
}

export function createProductionDesktopBrowserSubsystem(
  config: ProductionDesktopBrowserConfig,
): ProductionDesktopBrowserSubsystem {
  const originPolicy = readOriginPolicy(config.processEnv);
  const rpcToken = randomBytes(WORKER_RPC_TOKEN_BYTES).toString("base64url");
  const workerPort = new BrowserWorkerRpcClient({ token: rpcToken });
  const spawnWorker = config.spawn ?? defaultSpawn;
  const readySignals = new WeakMap<WorkerChildLike, Promise<void>>();
  const platform = config.platform ?? process.platform;

  const supervisor = new BrowserWorkerSupervisor({
    expectedVersion: PINNED_CHROMIUM_VERSION,
    spawn: () => {
      const child = spawnWorker(
        config.electronExecutable,
        [config.workerEntryPath],
        {
          env: browserWorkerEnv({
            config,
            originPolicy,
            rpcToken,
          }),
          stdio: ["ignore", "pipe", "pipe", "ipc"],
          windowsHide: true,
          detached: platform !== "win32",
        },
      );
      workerPort.attach(child);
      readySignals.set(child, waitForReady(child));
      return child;
    },
    probeHealth: async (child) => {
      const ready = readySignals.get(child);
      if (ready === undefined) {
        return { healthy: false, version: "unavailable" };
      }
      await ready;
      return workerPort.health();
    },
    onStateChange: config.onStateChange,
    log: config.log,
    killTree: (pid) => killWorkerTree(pid, platform),
  });
  const readAuthority = new MainBrowserReadAuthority({ originPolicy });
  const broker = new BrowserBroker({
    worker: workerPort,
    readAuthority,
    privateEffects: workerPort,
  });

  return {
    broker,
    supervisor,
    workerPort,
    async start() {
      try {
        await supervisor.start();
        if (!supervisor.isHealthy()) {
          throw new Error("browser worker did not become healthy");
        }
        return await broker.start();
      } catch (err) {
        if (broker.isRunning()) await broker.stop();
        await supervisor.stop();
        workerPort.dispose();
        throw err;
      }
    },
    async stop() {
      // Stop accepting new AI requests first. Closing sessions over the live
      // authenticated channel is best-effort; supervisor teardown is the hard
      // authority revocation and reaps Chromium descendants.
      await broker.stop();
      try {
        await workerPort.closeAll();
      } catch {
        // Worker already exited or became unavailable.
      }
      await supervisor.stop();
      workerPort.dispose();
    },
  };
}

export function readOriginPolicy(
  env: Readonly<Record<string, string | undefined>>,
): BrowserOriginPolicy {
  const raw = env[ORIGIN_POLICY_ENV];
  if (raw === undefined || raw.trim() === "") {
    throw new Error(
      "desktop browser requires a main-owned BROWSER_ORIGIN_POLICY",
    );
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    throw new Error("desktop browser origin policy is invalid");
  }
  const policy = BrowserOriginPolicySchema.parse(decoded);
  if (policy.topLevelOrigins.length === 0) {
    throw new Error(
      "desktop browser origin policy must approve at least one exact origin",
    );
  }
  return policy;
}

function browserWorkerEnv(input: {
  readonly config: ProductionDesktopBrowserConfig;
  readonly originPolicy: BrowserOriginPolicy;
  readonly rpcToken: string;
}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of WORKER_ENV_ALLOWLIST) {
    const value = input.config.processEnv[key];
    if (value !== undefined && value !== "") env[key] = value;
  }
  const browserRoot = join(input.config.userDataDir, "browser");
  env.ELECTRON_RUN_AS_NODE = "1";
  env.BROWSER_WORKER_ENTRY = "1";
  env[BROWSER_WORKER_RPC_TOKEN_ENV] = input.rpcToken;
  env.BROWSER_STAGING_ROOT = join(browserRoot, "staging");
  env.BROWSER_PROFILES_ROOT = join(browserRoot, "profiles");
  env.BROWSER_EPHEMERAL_ROOT = join(browserRoot, "ephemeral");
  env.BROWSER_ORIGIN_POLICY = JSON.stringify(input.originPolicy);
  env.BROWSER_EXECUTABLE_PATH = input.config.browserExecutablePath;
  return env;
}

function waitForReady(child: BrowserWorkerIpcChild): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let buffered = "";
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("browser worker readiness timed out"));
    }, WORKER_READY_TIMEOUT_MS);
    child.stdout?.on("data", (chunk) => {
      if (settled) return;
      buffered += chunk.toString();
      const lines = buffered.split(/\r?\n/u);
      buffered = lines.pop() ?? "";
      if (lines.some((line) => line.startsWith("READY "))) {
        settled = true;
        clearTimeout(timeout);
        resolve();
      }
    });
    child.on("exit", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error("browser worker exited before readiness"));
    });
    child.on("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error("browser worker failed before readiness"));
    });
  });
}

function defaultSpawn(
  command: string,
  args: readonly string[],
  options: SpawnOptions,
): BrowserWorkerIpcChild {
  return nodeSpawn(command, [...args], options) as BrowserWorkerIpcChild;
}

function killWorkerTree(pid: number, platform: NodeJS.Platform): void {
  try {
    if (platform === "win32") {
      const killer = nodeSpawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
        stdio: "ignore",
        windowsHide: true,
      });
      killer.unref();
      return;
    }
    // Workers are detached into their own process group. A negative pid reaps
    // the worker plus Chromium/crashpad/proxy descendants.
    process.kill(-pid, "SIGKILL");
  } catch {
    // Best effort after the direct child has already been signalled.
  }
}
