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
import { resolveBootStoreBackend } from "./boot-store-backend";
import { createCommandRunner } from "./exec";
import { fileStoreHasConversations } from "./file-store-facts";
import { waitForHealthy } from "./health";
import { runBootMigration } from "./migration-runner";
import {
  pgToFileMigrationMarkerPath,
  type StoreBackend,
} from "./migration-policy";
import { runMigrations } from "./migrations";
import { postgresAiStoreHasRows } from "./pg-facts";
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
  AI_BACKEND_DB_NAME,
  buildServiceEnv,
  databaseUrl,
  resolveAiStoreBackend,
  UVICORN_MODULES,
} from "./service-env";
import { ServiceSupervisor, type AllocatedPorts } from "./supervisor";
import type { BootSecrets } from "./boot-secrets";
import type { SecureStorageMode } from "./secure-storage-policy";

// ENOENT -> false (marker absent); any other error propagates so the boot
// store-backend resolver falls back to Postgres rather than guessing.
async function markerFileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (err) {
    if (
      typeof err === "object" &&
      err !== null &&
      "code" in err &&
      (err as { code: unknown }).code === "ENOENT"
    ) {
      return false;
    }
    throw err;
  }
}

// Record a completed, verified carry-over. The parent (the file store root) is
// created by the migrator itself, but ensure it exists so the marker write is
// robust even on an unusual ordering.
async function writeMigrationMarker(path: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${new Date().toISOString()}\n`, { mode: 0o600 });
}

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
  // Resolve the CONFIGURED ai-backend store backend ONCE at construction, from
  // the SAME env buildServiceEnv reads. File-native is the default; Postgres is
  // the explicit opt-out. This is the starting point; the EFFECTIVE backend for a
  // boot can differ when the first-file-boot migration falls back to Postgres.
  const configuredBackend = resolveAiStoreBackend(processEnv);

  const envInputs = (
    ports: AllocatedPorts,
    secrets: BootSecrets,
    storeBackendOverride?: StoreBackend,
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
    // When set, forces the ai-backend store backend for this boot (post-migration
    // gate); undefined preserves the pure env resolution.
    storeBackendOverride,
  });

  // The store backend to serve THIS boot, resolved lazily during the migrations
  // phase (postgres is up by then, ai-backend has not started yet) and reused,
  // unchanged, when the ai-backend service is created. Memoized so the migration
  // probe/import runs at most once. The first-file-boot Postgres->file carry-over
  // is gated + executed here; on any failure it falls back to `postgres` so a bad
  // import can never strand the user with an empty app.
  let effectiveStoreBackend: StoreBackend | null = null;
  let effectivePromise: Promise<StoreBackend> | null = null;
  const migrationLog = (message: string): void => {
    // Loud: the boot migration is data-sensitive, so its decisions/failures must
    // be visible in the desktop main-process log.
    console.warn(`[pg->file migration] ${message}`);
  };
  const resolveEffectiveBackend = (
    ports: AllocatedPorts,
    secrets: BootSecrets,
  ): Promise<StoreBackend> => {
    if (effectivePromise !== null) return effectivePromise;
    effectivePromise = (async () => {
      const destRoot = aiFileStoreV1Root(config.userDataDir);
      const markerPath = pgToFileMigrationMarkerPath(config.userDataDir);
      const sourceDatabaseUrl = databaseUrl({
        pgPort: ports.pg,
        pgPassword: secrets.pgPassword,
        database: AI_BACKEND_DB_NAME,
      });
      const aiSitePackages = join(
        paths.serviceDir("ai-backend"),
        "site-packages",
      );
      const result = await resolveBootStoreBackend({
        configuredBackend,
        fileStoreHasData: () =>
          fileStoreHasConversations(destRoot, { readdir }),
        postgresHasData: () =>
          postgresAiStoreHasRows({
            pythonBin: paths.pythonBin,
            pythonSitePackages: aiSitePackages,
            pgPort: ports.pg,
            pgPassword: secrets.pgPassword,
            runner,
          }),
        markerExists: () => markerFileExists(markerPath),
        runMigration: () =>
          runBootMigration({
            pythonBin: paths.pythonBin,
            serviceDir: paths.serviceDir("ai-backend"),
            sourceDatabaseUrl,
            destRoot,
            // Same env the file-mode ai-backend gets (PYTHONPATH + telemetry
            // kill-switch); the CLI reads source/dest from argv, not env.
            env: buildServiceEnv(
              "ai-backend",
              envInputs(ports, secrets, "file"),
            ),
            runner,
            log: migrationLog,
          }),
        writeMarker: () => writeMigrationMarker(markerPath),
        log: migrationLog,
      });
      migrationLog(
        `boot store backend = ${result.backend} (${result.note}` +
          `${result.migrated ? ", carried over from Postgres" : ""})`,
      );
      effectiveStoreBackend = result.backend;
      return result.backend;
    })();
    return effectivePromise;
  };

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

    runMigrations: async (service, { ports, secrets }) => {
      if (service === "ai-backend") {
        // Resolve the EFFECTIVE backend for this boot first — this is where the
        // first-file-boot Postgres->file carry-over is gated and run (postgres is
        // up; ai-backend has not started). On file, the store has no relational
        // migrations, so skip scripts/migrate.py (it would fail closed without a
        // Postgres DB env). On a Postgres fallback we DO run them so the still-
        // authoritative relational store is schema-current for this boot.
        const backend = await resolveEffectiveBackend(ports, secrets);
        if (backend === "file") return;
        return runMigrations({
          service,
          pythonBin: paths.pythonBin,
          serviceDir: paths.serviceDir(service),
          env: buildServiceEnv(service, envInputs(ports, secrets, backend)),
          runner,
        });
      }
      // The backend keeps its own Postgres migrations (identity/OAuth/vault) in
      // every mode.
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
      // By the services phase the migrations phase has already resolved the
      // effective backend for the ai-backend (its runMigrations gate awaits it),
      // so this read is populated. The `?? configuredBackend` is a defensive
      // fallback only; buildServiceEnv ignores the override for other services.
      const storeBackendOverride =
        name === "ai-backend"
          ? (effectiveStoreBackend ?? configuredBackend)
          : undefined;
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
        env: buildServiceEnv(
          name,
          envInputs(ports, secrets, storeBackendOverride),
        ),
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
