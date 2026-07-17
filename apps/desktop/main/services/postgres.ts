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
  /**
   * Staged python interpreter. The zonky postgres bundle ships NO psql /
   * createdb, so database creation goes through python + psycopg (exactly as
   * the proven tools/desktop-runtime/run-local.mjs does).
   */
  readonly pythonBin: string;
  /**
   * PYTHONPATH value that makes `import psycopg` resolve — the backend
   * service's staged site-packages directory.
   */
  readonly pythonSitePackages: string;
  readonly runner: CommandRunner;
  readonly fs: PostgresFs;
  /** kill(pid, 0)-style liveness probe; injectable for stale-pid tests. */
  readonly processAlive?: (pid: number) => boolean;
  /** pg_ctl -w start wait budget in seconds (pg_ctl -t). Default 60. */
  readonly startTimeoutSeconds?: number;
}

const DB_NAME_RE = /^[a-z_][a-z0-9_]*$/u;

// Idempotent "create if absent" run by the staged interpreter. The db name is
// pre-validated against DB_NAME_RE in TS, and the password never reaches argv
// (libpq reads PGPASSWORD from the environment).
const ENSURE_DB_SCRIPT = `
import sys
import psycopg

name, conninfo = sys.argv[1], sys.argv[2]
with psycopg.connect(conninfo, autocommit=True) as conn:
    exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
    ).fetchone()
    if exists is None:
        conn.execute(f'CREATE DATABASE "{name}"')
        print(f"created database {name}")
    else:
        print(f"database {name} already exists")
`;

// Owns the embedded postgres lifecycle: one-time initdb, stale
// postmaster.pid cleanup, `pg_ctl -w start` (which blocks until the server
// accepts connections — the bundle has no pg_isready), database creation via
// python + psycopg, and `pg_ctl stop -m fast`. All process/filesystem
// touchpoints are injected so the unit tests run against fakes.
export class PostgresManager {
  readonly #config: PostgresManagerConfig;
  #started = false;

  constructor(config: PostgresManagerConfig) {
    this.#config = config;
  }

  async start(): Promise<void> {
    // pg_ctl -l opens the server log file directly; its parent dir (the
    // supervisor's logs/) is otherwise created lazily by the service log
    // writers, which only run AFTER postgres. Ensure it exists first.
    await this.#config.fs.mkdir(dirname(this.#config.logFile), {
      recursive: true,
    });
    await this.#ensureInitialized();
    await this.#cleanupStalePid();
    const { paths, dataDir, logFile, port, runner, startTimeoutSeconds } =
      this.#config;
    // `-w` blocks until the postmaster is accepting connections (pg_ctl's own
    // PQping loop); `-t` bounds that wait. No separate pg_isready gate — the
    // zonky bundle does not ship it, and run-local.mjs proves `-w` is enough.
    const result = await runner(paths.pgCtl, [
      "-D",
      dataDir,
      "-l",
      logFile,
      "-o",
      `-p ${port} -c listen_addresses=127.0.0.1`,
      "-w",
      "-t",
      String(startTimeoutSeconds ?? 60),
      "start",
    ]);
    if (result.code !== 0) {
      throw new PostgresError("start", outputTail(result, 20));
    }
    this.#started = true;
  }

  async ensureDatabase(name: string): Promise<void> {
    if (!DB_NAME_RE.test(name)) {
      throw new PostgresError("create-database", `invalid db name "${name}"`);
    }
    const { pythonBin, pythonSitePackages, port, password, runner } =
      this.#config;
    // Connect to the always-present `postgres` maintenance database as the
    // bootstrap superuser; PGPASSWORD keeps the secret out of argv.
    const conninfo = `postgresql://${PG_SUPERUSER}@127.0.0.1:${port}/postgres`;
    const result = await runner(
      pythonBin,
      ["-c", ENSURE_DB_SCRIPT, name, conninfo],
      {
        env: {
          PYTHONPATH: pythonSitePackages,
          PYTHONDONTWRITEBYTECODE: "1",
          PGPASSWORD: password,
        },
      },
    );
    if (result.code !== 0) {
      throw new PostgresError("create-database", outputTail(result, 20));
    }
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
