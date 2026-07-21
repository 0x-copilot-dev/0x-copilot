import { spawn } from "node:child_process";
import {
  appendFile,
  chmod,
  mkdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { join } from "node:path";

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
  buildServiceEnv,
  resolveAiStoreBackend,
  UVICORN_MODULES,
} from "./service-env";
import { ServiceSupervisor, type AllocatedPorts } from "./supervisor";
import type { BootSecrets } from "./boot-secrets";
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
  const runner = createCommandRunner();
  const logsDir = join(config.userDataDir, "logs");
  const fsAdapter = { readFile, writeFile, mkdir, rm, chmod };
  // Resolve the ai-backend store backend ONCE at supervisor construction, from
  // the SAME env buildServiceEnv reads (so the store branch and this gate can
  // never diverge). File-native is the default; when it is active the ai-backend
  // has no Postgres DB env, so its relational migration gate must be skipped.
  const aiStoreBackend = resolveAiStoreBackend(processEnv);

  const envInputs = (
    ports: AllocatedPorts,
    secrets: BootSecrets,
  ): Parameters<typeof buildServiceEnv>[1] => ({
    secrets,
    pgPort: ports.pg,
    backendPort: ports.backend,
    aiBackendPort: ports.aiBackend,
    facadePort: ports.facade,
    processEnv,
    userDataDir: config.userDataDir,
    // Staged frontend web assets (wallet.html + assets/); the facade serves the
    // SIWE wallet page from here (FACADE_WEB_DIST_DIR).
    webDir: paths.webDir,
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
        runner,
        fs: {
          readFile: (path, encoding) => readFile(path, encoding),
          writeFile,
          mkdir,
          rm,
        },
      }),

    runMigrations: (service, { ports, secrets }) => {
      // The file-native store has no relational migrations. When it is active
      // the ai-backend has no Postgres DB env, so scripts/migrate.py would fail
      // closed ("set RUNTIME_DATABASE_URL"); skip its gate. The backend keeps
      // its own Postgres migrations (identity/OAuth/vault) in every mode.
      if (service === "ai-backend" && aiStoreBackend === "file") {
        return Promise.resolve();
      }
      return runMigrations({
        service,
        pythonBin: paths.pythonBin,
        serviceDir: paths.serviceDir(service),
        env: buildServiceEnv(service, envInputs(ports, secrets)),
        runner,
      });
    },

    createService: (name, { ports, secrets, onFatal }) => {
      const port = portFor(name, ports);
      const log = new RotatingLogWriter({
        path: join(logsDir, `${name}.log`),
        fs: { appendFile, stat, rename, rm, mkdir },
      });
      return new PythonService({
        name,
        command: paths.pythonBin,
        args: [
          "-m",
          "uvicorn",
          `${UVICORN_MODULES[name]}:app`,
          "--host",
          "127.0.0.1",
          "--port",
          String(port),
        ],
        cwd: paths.serviceDir(name),
        env: buildServiceEnv(name, envInputs(ports, secrets)),
        spawnFn: spawn as unknown as SpawnFn,
        log,
        onFatal,
      });
    },

    waitForHealthy: (name, baseUrl) =>
      waitForHealthy({ service: name, baseUrl }),
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
