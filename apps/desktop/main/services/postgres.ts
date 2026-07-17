import { dirname, join } from "node:path";

import { outputTail, type CommandRunner } from "./exec";
import { PG_SUPERUSER } from "./service-env";

export class PostgresError extends Error {
  constructor(step: string, detail: string) {
    super(`postgres ${step} failed: ${detail}`);
    this.name = "PostgresError";
  }
}

export interface PostgresPaths {
  readonly initdb: string;
  readonly pgCtl: string;
  readonly pgIsReady: string;
  readonly psql: string;
}

export interface PostgresFs {
  readFile(path: string, encoding: "utf-8"): Promise<string>;
  writeFile(
    path: string,
    data: string,
    options?: { mode?: number },
  ): Promise<void>;
  mkdir(path: string, options: { recursive: boolean }): Promise<unknown>;
  rm(path: string, options: { force: boolean }): Promise<void>;
}

export interface PostgresManagerConfig {
  readonly paths: PostgresPaths;
  /** Cluster data directory, e.g. <userData>/pgdata. */
  readonly dataDir: string;
  /** Server log file passed to pg_ctl -l. */
  readonly logFile: string;
  readonly port: number;
  readonly password: string;
  readonly runner: CommandRunner;
  readonly fs: PostgresFs;
  /** kill(pid, 0)-style liveness probe; injectable for stale-pid tests. */
  readonly processAlive?: (pid: number) => boolean;
  readonly sleep?: (ms: number) => Promise<void>;
  readonly readyTimeoutMs?: number;
  readonly readyIntervalMs?: number;
  readonly now?: () => number;
}

const DB_NAME_RE = /^[a-z_][a-z0-9_]*$/u;

// Owns the embedded postgres lifecycle: one-time initdb, stale
// postmaster.pid cleanup, pg_ctl start + pg_isready gate, database
// creation, pg_ctl stop -m fast. All process/filesystem touchpoints are
// injected so the unit tests run against fakes.
export class PostgresManager {
  readonly #config: PostgresManagerConfig;
  #started = false;

  constructor(config: PostgresManagerConfig) {
    this.#config = config;
  }

  async start(): Promise<void> {
    await this.#ensureInitialized();
    await this.#cleanupStalePid();
    const { paths, dataDir, logFile, port, runner } = this.#config;
    const result = await runner(paths.pgCtl, [
      "-D",
      dataDir,
      "-l",
      logFile,
      "-o",
      `-p ${port} -c listen_addresses=127.0.0.1`,
      "-w",
      "start",
    ]);
    if (result.code !== 0) {
      throw new PostgresError("start", outputTail(result, 20));
    }
    this.#started = true;
    await this.#waitReady();
  }

  async ensureDatabase(name: string): Promise<void> {
    if (!DB_NAME_RE.test(name)) {
      throw new PostgresError("create-database", `invalid db name "${name}"`);
    }
    const exists = await this.#psql(
      `SELECT 1 FROM pg_database WHERE datname = '${name}'`,
    );
    if (exists.trim() === "1") return;
    await this.#psql(`CREATE DATABASE ${name}`);
  }

  async stop(): Promise<void> {
    if (!this.#started) return;
    this.#started = false;
    const { paths, dataDir, runner } = this.#config;
    // Best-effort on shutdown: a failure here must not block app quit.
    await runner(paths.pgCtl, ["-D", dataDir, "-m", "fast", "stop"]);
  }

  async #ensureInitialized(): Promise<void> {
    const { fs, dataDir, paths, password, runner } = this.#config;
    try {
      await fs.readFile(join(dataDir, "PG_VERSION"), "utf-8");
      return; // already initialised
    } catch (err) {
      if (!isEnoent(err)) throw err;
    }
    await fs.mkdir(dirname(dataDir), { recursive: true });
    // initdb refuses a non-empty dataDir, so the pwfile lives beside it.
    const pwfile = `${dataDir}.pwfile`;
    await fs.writeFile(pwfile, `${password}\n`, { mode: 0o600 });
    try {
      const result = await runner(paths.initdb, [
        "--encoding=UTF8",
        "--locale=C",
        "-U",
        PG_SUPERUSER,
        "--pwfile",
        pwfile,
        "--auth=scram-sha-256",
        "-D",
        dataDir,
      ]);
      if (result.code !== 0) {
        throw new PostgresError("initdb", outputTail(result, 20));
      }
    } finally {
      await fs.rm(pwfile, { force: true });
    }
  }

  async #cleanupStalePid(): Promise<void> {
    const { fs, dataDir } = this.#config;
    const pidPath = join(dataDir, "postmaster.pid");
    let raw: string;
    try {
      raw = await fs.readFile(pidPath, "utf-8");
    } catch (err) {
      if (isEnoent(err)) return;
      throw err;
    }
    const firstLine = raw.split(/\r?\n/u, 1)[0] ?? "";
    const pid = Number.parseInt(firstLine.trim(), 10);
    const alive = this.#config.processAlive ?? defaultProcessAlive;
    if (Number.isNaN(pid) || !alive(pid)) {
      // Dead postmaster left a stale pid file (crash / force-quit).
      await fs.rm(pidPath, { force: true });
    }
  }

  async #waitReady(): Promise<void> {
    const {
      paths,
      port,
      runner,
      readyTimeoutMs = 30_000,
      readyIntervalMs = 250,
    } = this.#config;
    const now = this.#config.now ?? Date.now;
    const sleep = this.#config.sleep ?? defaultSleep;
    const deadline = now() + readyTimeoutMs;
    let lastTail = "";
    for (;;) {
      const result = await runner(paths.pgIsReady, [
        "-h",
        "127.0.0.1",
        "-p",
        String(port),
      ]);
      if (result.code === 0) return;
      lastTail = outputTail(result, 5);
      if (now() >= deadline) {
        throw new PostgresError(
          "pg_isready",
          `not ready after ${readyTimeoutMs}ms: ${lastTail}`,
        );
      }
      await sleep(readyIntervalMs);
    }
  }

  async #psql(sql: string): Promise<string> {
    const { paths, port, password, runner } = this.#config;
    const result = await runner(
      paths.psql,
      [
        "-h",
        "127.0.0.1",
        "-p",
        String(port),
        "-U",
        PG_SUPERUSER,
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
      ],
      { env: { PGPASSWORD: password } },
    );
    if (result.code !== 0) {
      throw new PostgresError("psql", outputTail(result, 10));
    }
    return result.stdout;
  }
}

function defaultProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // EPERM means "alive but not ours"; only ESRCH proves death.
    return isErrnoCode(err, "EPERM");
  }
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function isEnoent(err: unknown): boolean {
  return isErrnoCode(err, "ENOENT");
}

function isErrnoCode(err: unknown, code: string): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code: string }).code === code
  );
}
