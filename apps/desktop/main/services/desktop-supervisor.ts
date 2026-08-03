import { spawn } from "node:child_process";
import {
  access,
  appendFile,
  chmod,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { dirname, join } from "node:path";

import type { SafeStorageLike } from "../auth/secret-storage";
import { loadOrCreateBootSecrets } from "./boot-secrets";
import { createCommandRunner } from "./exec";
import { waitForHealthy } from "./health";
import { runMigrations } from "./migrations";
import { allocateFreePorts } from "./ports";
import { PostgresManager } from "./postgres";
import { PythonService, type SpawnFn } from "./python-service";
import { RotatingLogWriter } from "./rotating-log";
import {
  resolveRuntimePaths,
  type SupervisedServiceName,
} from "./runtime-paths";
import {
  aiFileStoreV1Root,
  buildServiceEnv,
  databaseUrl,
  UVICORN_MODULES,
} from "./service-env";
import { ServiceSupervisor, type AllocatedPorts } from "./supervisor";
import type { BootSecrets } from "./boot-secrets";
import { LocalServiceIdentityRegistry } from "./local-service-identity";
import { MacosWorkspaceConfinement } from "./macos-workspace-confinement";
import type { SecureStorageMode } from "./secure-storage-policy";

export interface DesktopSupervisorConfig {
  /** app.getPath("userData") — secrets, pgdata and logs live here. */
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /**
   * Secure-storage policy for boot secrets. `"file"` (the default) writes a
   * chmod-600 blob and never touches the OS keychain; `"keychain"` (Settings
   * opt-in) encrypts via safeStorage. Existing blobs always load by their own
   * marker regardless of this value.
   */
  readonly secureStorageMode?: SecureStorageMode;
  /** process.resourcesPath (packaged) — ignored when the override is set. */
  readonly resourcesPath: string;
  /** COPILOT_RUNTIME_DIR (dev staged runtime, apps/desktop/resources). */
  readonly runtimeDirOverride?: string | undefined;
  readonly processEnv?: Readonly<Record<string, string | undefined>>;
  readonly platform?: NodeJS.Platform;
  readonly arch?: NodeJS.Architecture;
  /** Main-created identities shared with main-owned local-authority brokers. */
  readonly localServiceIdentities?: LocalServiceIdentityRegistry;
  /**
   * Present only after the packaged macOS C2 launch gate verified Seatbelt.
   * Supplying it never falls back to an unconstrained Python child.
   */
  readonly workspaceChildConfinement?: MacosWorkspaceConfinement;
}

// Composes the pure orchestrator (supervisor.ts) with the real OS-facing
// adapters. This is the only services/ module that touches node:fs,
// node:child_process and node:net directly — everything it composes is
// unit-tested against fakes.
export function createDesktopSupervisor(
  config: DesktopSupervisorConfig,
): ServiceSupervisor {
  const paths = resolveRuntimePaths({
    resourcesPath: config.resourcesPath,
    runtimeDirOverride: config.runtimeDirOverride,
    platform: config.platform,
    arch: config.arch,
  });
  const processEnv = config.processEnv ?? process.env;
  const timingStartedAt = process.hrtime.bigint();
  const logTiming = (event: string): void => {
    if (processEnv.COPILOT_BOOT_TIMINGS !== "1") return;
    const elapsedMs =
      Number(process.hrtime.bigint() - timingStartedAt) / 1_000_000;
    console.log(`[boot-detail] ${event} ${elapsedMs.toFixed(1)}ms`);
  };
  const localServiceIdentities =
    config.localServiceIdentities ?? new LocalServiceIdentityRegistry();
  const workspaceChildConfinement = config.workspaceChildConfinement;
  const runner = createCommandRunner();
  const logsDir = join(config.userDataDir, "logs");
  const fsAdapter = { readFile, writeFile, mkdir, rm, chmod };
  const envInputs = (
    name: SupervisedServiceName,
    ports: AllocatedPorts,
    secrets: BootSecrets,
  ): Parameters<typeof buildServiceEnv>[1] => ({
    secrets,
    pgPort: ports.pg,
    backendPort: ports.backend,
    aiBackendPort: ports.aiBackend,
    facadePort: ports.facade,
    processEnv,
    localServiceIdentity: localServiceIdentities.forService(name),
    userDataDir: config.userDataDir,
    // Staged frontend web assets (wallet.html + assets/); the facade serves the
    // SIWE wallet page from here (FACADE_WEB_DIST_DIR).
    webDir: paths.webDir,
    browserBroker: {
      enabled:
        processEnv.RUNTIME_ENABLE_DESKTOP_BROWSER?.trim().toLowerCase() ===
        "true",
      baseUrl: processEnv.DESKTOP_BROWSER_BROKER_URL,
      token: processEnv.DESKTOP_BROWSER_BROKER_TOKEN,
      audience: processEnv.DESKTOP_BROWSER_BROKER_AUDIENCE,
    },
    workspaceBroker: {
      enabled:
        processEnv.RUNTIME_ENABLE_DESKTOP_WORKSPACE?.trim().toLowerCase() ===
        "true",
      baseUrl: processEnv.DESKTOP_WORKSPACE_BROKER_URL,
      token: processEnv.DESKTOP_WORKSPACE_BROKER_TOKEN,
      audience: processEnv.DESKTOP_WORKSPACE_BROKER_AUDIENCE,
    },
    workspaceAttestation: {
      publicKey: processEnv.DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY,
      payload: processEnv.DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD,
      signature: processEnv.DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE,
    },
  });

  return new ServiceSupervisor({
    loadSecrets: () =>
      loadOrCreateBootSecrets({
        userDataDir: config.userDataDir,
        safeStorage: config.safeStorage,
        fs: fsAdapter,
        mode: config.secureStorageMode ?? "file",
      }),

    allocatePorts: (count) => allocateFreePorts(count),

    createPostgres: ({ port, password }) =>
      new PostgresManager({
        paths: paths.pgBin,
        dataDir: join(config.userDataDir, "pgdata"),
        logFile: join(logsDir, "postgres.log"),
        port,
        password,
        // No psql/createdb in the bundle: databases are created with the
        // staged interpreter + psycopg from the backend's site-packages.
        pythonBin: paths.pythonBin,
        pythonSitePackages: join(paths.serviceDir("backend"), "site-packages"),
        // Used solely by `copilot doctor` to separate this live app's database
        // from a postmaster stranded by a crashed/force-quit predecessor.
        ownerPid: process.pid,
        runner,
        fs: {
          readFile: (path, encoding) => readFile(path, encoding),
          writeFile,
          mkdir,
          rm,
        },
      }),

    runMigrations: async (service, { ports, secrets }) => {
      if (service === "ai-backend") {
        // The ai-backend runs the file-native store — JSONL session folders the
        // adapter provisions itself on open. There is no relational schema, so
        // there is nothing to migrate.
        logTiming(`${service}.migrations`);
        return;
      }
      // The backend still owns Postgres migrations (identity / OAuth / vault):
      // it is the only remaining reason the desktop boots a postmaster.
      await runMigrations({
        service,
        pythonBin: paths.pythonBin,
        serviceDir: paths.serviceDir(service),
        env: buildServiceEnv(service, envInputs(service, ports, secrets)),
        runner,
      });
      logTiming(`${service}.migrations`);
    },

    createService: (name, { ports, secrets, onFatal }) => {
      const port = portFor(name, ports);
      const log = new RotatingLogWriter({
        path: join(logsDir, `${name}.log`),
        fs: { appendFile, stat, rename, rm, mkdir },
      });
      const command = paths.pythonBin;
      const args = [
        "-m",
        "uvicorn",
        `${UVICORN_MODULES[name]}:app`,
        "--host",
        "127.0.0.1",
        "--port",
        String(port),
      ] as const;
      const confined =
        workspaceChildConfinement === undefined
          ? { command, args }
          : workspaceChildConfinement.wrap(command, args);
      return new PythonService({
        name,
        command: confined.command,
        args: confined.args,
        cwd: paths.serviceDir(name),
        env: buildServiceEnv(name, envInputs(name, ports, secrets)),
        spawnFn:
          workspaceChildConfinement?.spawnFor(
            name,
            spawn as unknown as SpawnFn,
          ) ?? (spawn as unknown as SpawnFn),
        log,
        onFatal,
      });
    },

    waitForHealthy: async (name, baseUrl) => {
      await waitForHealthy({ service: name, baseUrl });
      logTiming(`${name}.healthy`);
      workspaceChildConfinement?.noteHealthy(name);
    },
  });
}

function portFor(name: SupervisedServiceName, ports: AllocatedPorts): number {
  switch (name) {
    case "backend":
      return ports.backend;
    case "ai-backend":
      return ports.aiBackend;
    case "backend-facade":
      return ports.facade;
  }
}
