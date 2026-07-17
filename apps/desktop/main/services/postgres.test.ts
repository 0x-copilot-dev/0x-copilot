// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { CommandResult } from "./exec";
import {
  PostgresError,
  PostgresManager,
  type PostgresFs,
  type PostgresManagerConfig,
} from "./postgres";

// Only initdb + pg_ctl exist in the zonky bundle; DB creation goes through
// the staged python + psycopg (no psql / createdb).
const PATHS = {
  initdb: "/rt/postgres/bin/initdb",
  pgCtl: "/rt/postgres/bin/pg_ctl",
};
const PYTHON_BIN = "/rt/python/bin/python3";
const SITE_PACKAGES = "/rt/services/backend/site-packages";
const DATA_DIR = "/user-data/pgdata";

interface RunCall {
  command: string;
  args: readonly string[];
  env?: Record<string, string>;
}

interface Harness {
  manager: PostgresManager;
  calls: RunCall[];
  files: Map<string, string>;
  removed: string[];
  mkdirs: string[];
}

function ok(stdout = ""): CommandResult {
  return { code: 0, stdout, stderr: "" };
}

function fail(stderr: string, code = 1): CommandResult {
  return { code, stdout: "", stderr };
}

function makeHarness(options: {
  files?: Record<string, string>;
  onRun?: (call: RunCall, calls: RunCall[]) => CommandResult | undefined;
  processAlive?: (pid: number) => boolean;
  config?: Partial<PostgresManagerConfig>;
}): Harness {
  const files = new Map<string, string>(Object.entries(options.files ?? {}));
  const removed: string[] = [];
  const mkdirs: string[] = [];
  const calls: RunCall[] = [];
  const fs: PostgresFs = {
    readFile: (path) => {
      const content = files.get(path);
      if (content === undefined) {
        const err = new Error("ENOENT") as NodeJS.ErrnoException;
        err.code = "ENOENT";
        return Promise.reject(err);
      }
      return Promise.resolve(content);
    },
    writeFile: (path, data) => {
      files.set(path, data);
      return Promise.resolve();
    },
    mkdir: (path) => {
      mkdirs.push(path);
      return Promise.resolve(undefined);
    },
    rm: (path) => {
      files.delete(path);
      removed.push(path);
      return Promise.resolve();
    },
  };
  const manager = new PostgresManager({
    paths: PATHS,
    dataDir: DATA_DIR,
    logFile: "/user-data/logs/postgres.log",
    port: 55_432,
    password: "pg-secret",
    pythonBin: PYTHON_BIN,
    pythonSitePackages: SITE_PACKAGES,
    runner: (command, args, opts) => {
      const call: RunCall = { command, args, env: opts?.env };
      calls.push(call);
      const result = options.onRun?.(call, calls);
      return Promise.resolve(result ?? ok());
    },
    fs,
    processAlive: options.processAlive ?? (() => true),
    ...options.config,
  });
  return { manager, calls, files, removed, mkdirs };
}

describe("PostgresManager.start", () => {
  it("runs initdb with contract flags + pwfile when the cluster is missing", async () => {
    const h = makeHarness({});
    await h.manager.start();

    const initdb = h.calls.find((c) => c.command === PATHS.initdb);
    expect(initdb).toBeDefined();
    expect(initdb!.args).toEqual([
      "--encoding=UTF8",
      "--locale=C",
      "-U",
      "atlas",
      "--pwfile",
      `${DATA_DIR}.pwfile`,
      "--auth=scram-sha-256",
      "-D",
      DATA_DIR,
    ]);
    // pwfile is written before initdb and deleted afterwards.
    expect(h.removed).toContain(`${DATA_DIR}.pwfile`);
    expect(h.files.has(`${DATA_DIR}.pwfile`)).toBe(false);
  });

  it("creates the log-file directory before pg_ctl start", async () => {
    // pg_ctl -l fails if logs/ does not exist yet (it runs before any service
    // log writer). start() must mkdir it first.
    const h = makeHarness({ files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" } });
    await h.manager.start();
    expect(h.mkdirs).toContain("/user-data/logs");
  });

  it("skips initdb when PG_VERSION exists", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
    });
    await h.manager.start();
    expect(h.calls.some((c) => c.command === PATHS.initdb)).toBe(false);
  });

  it("removes a stale postmaster.pid when the pid is dead", async () => {
    const h = makeHarness({
      files: {
        [`${DATA_DIR}/PG_VERSION`]: "17\n",
        [`${DATA_DIR}/postmaster.pid`]: "12345\n/other/lines\n",
      },
      processAlive: () => false,
    });
    await h.manager.start();
    expect(h.removed).toContain(`${DATA_DIR}/postmaster.pid`);
  });

  it("leaves postmaster.pid alone when the pid is alive", async () => {
    const h = makeHarness({
      files: {
        [`${DATA_DIR}/PG_VERSION`]: "17\n",
        [`${DATA_DIR}/postmaster.pid`]: "12345\n",
      },
      processAlive: () => true,
    });
    await h.manager.start();
    expect(h.removed).not.toContain(`${DATA_DIR}/postmaster.pid`);
  });

  it("starts via `pg_ctl -w -t start` with loopback-only listen_addresses (no pg_isready)", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
    });
    await h.manager.start();

    const start = h.calls.find((c) => c.command === PATHS.pgCtl);
    expect(start!.args).toContain("-w");
    expect(start!.args).toContain("start");
    expect(start!.args).toContain("-p 55432 -c listen_addresses=127.0.0.1");
    // -w blocks until ready; the bundle ships no pg_isready to poll.
    const tIndex = start!.args.indexOf("-t");
    expect(tIndex).toBeGreaterThanOrEqual(0);
    expect(start!.args[tIndex + 1]).toBe("60");
    expect(h.calls.every((c) => !c.command.includes("pg_isready"))).toBe(true);
  });

  it("honors a custom startTimeoutSeconds", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
      config: { startTimeoutSeconds: 120 },
    });
    await h.manager.start();
    const start = h.calls.find((c) => c.command === PATHS.pgCtl);
    const tIndex = start!.args.indexOf("-t");
    expect(start!.args[tIndex + 1]).toBe("120");
  });

  it("throws PostgresError when pg_ctl start fails", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
      onRun: (call) => {
        if (call.command === PATHS.pgCtl) return fail("could not start");
        return ok();
      },
    });
    await expect(h.manager.start()).rejects.toThrow(PostgresError);
  });
});

describe("PostgresManager.ensureDatabase", () => {
  it("invokes the staged python + psycopg with the maintenance DSN", async () => {
    const h = makeHarness({});
    await h.manager.ensureDatabase("atlas_backend");

    const pyCalls = h.calls.filter((c) => c.command === PYTHON_BIN);
    expect(pyCalls).toHaveLength(1);
    const call = pyCalls[0]!;
    expect(call.args[0]).toBe("-c");
    // db name + conninfo are the two positional args after the -c script.
    expect(call.args.at(-2)).toBe("atlas_backend");
    expect(call.args.at(-1)).toBe(
      "postgresql://atlas@127.0.0.1:55432/postgres",
    );
    // Password travels via PGPASSWORD env, never argv; psycopg is importable.
    expect(call.env?.PGPASSWORD).toBe("pg-secret");
    expect(call.env?.PYTHONPATH).toBe(SITE_PACKAGES);
    expect(call.args.join(" ")).not.toContain("pg-secret");
  });

  it("propagates a PostgresError when the python helper exits nonzero", async () => {
    const h = makeHarness({
      onRun: (call) => {
        if (call.command === PYTHON_BIN) return fail("connection refused");
        return ok();
      },
    });
    await expect(h.manager.ensureDatabase("atlas_ai")).rejects.toThrow(
      PostgresError,
    );
  });

  it("rejects an unsafe database name before spawning python", async () => {
    const h = makeHarness({});
    await expect(h.manager.ensureDatabase("bad; DROP TABLE x")).rejects.toThrow(
      /invalid db name/u,
    );
    expect(h.calls).toHaveLength(0);
  });
});

describe("PostgresManager.stop", () => {
  it("stops with -m fast after a successful start, and only once", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
    });
    await h.manager.start();
    await h.manager.stop();
    await h.manager.stop(); // idempotent
    const stops = h.calls.filter(
      (c) => c.command === PATHS.pgCtl && c.args.includes("stop"),
    );
    expect(stops).toHaveLength(1);
    expect(stops[0]!.args).toEqual(["-D", DATA_DIR, "-m", "fast", "stop"]);
  });

  it("is a no-op when start never ran", async () => {
    const h = makeHarness({});
    await h.manager.stop();
    expect(h.calls).toHaveLength(0);
  });
});
