// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { CommandResult } from "./exec";
import {
  PostgresError,
  PostgresManager,
  type PostgresFs,
  type PostgresManagerConfig,
} from "./postgres";

const PATHS = {
  initdb: "/rt/pgsql/bin/initdb",
  pgCtl: "/rt/pgsql/bin/pg_ctl",
  pgIsReady: "/rt/pgsql/bin/pg_isready",
  psql: "/rt/pgsql/bin/psql",
};
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
    mkdir: () => Promise.resolve(undefined),
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
    runner: (command, args, opts) => {
      const call: RunCall = { command, args, env: opts?.env };
      calls.push(call);
      const result = options.onRun?.(call, calls);
      return Promise.resolve(result ?? ok());
    },
    fs,
    processAlive: options.processAlive ?? (() => true),
    sleep: () => Promise.resolve(),
    now: Date.now,
    ...options.config,
  });
  return { manager, calls, files, removed };
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

  it("starts via pg_ctl with loopback-only listen_addresses and waits for pg_isready", async () => {
    const isReadyResults = [fail("no response"), fail("no response"), ok()];
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
      onRun: (call) => {
        if (call.command === PATHS.pgIsReady) return isReadyResults.shift();
        return ok();
      },
    });
    await h.manager.start();

    const start = h.calls.find((c) => c.command === PATHS.pgCtl);
    expect(start!.args).toContain("-w");
    expect(start!.args).toContain("start");
    expect(start!.args).toContain("-p 55432 -c listen_addresses=127.0.0.1");
    const readyCalls = h.calls.filter((c) => c.command === PATHS.pgIsReady);
    expect(readyCalls).toHaveLength(3);
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

  it("throws PostgresError when pg_isready never succeeds within budget", async () => {
    let t = 0;
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
      onRun: (call) => {
        if (call.command === PATHS.pgIsReady) return fail("still starting");
        return ok();
      },
      config: {
        readyTimeoutMs: 1000,
        readyIntervalMs: 250,
        now: () => {
          t += 300;
          return t;
        },
      },
    });
    await expect(h.manager.start()).rejects.toThrow(/not ready after/u);
  });
});

describe("PostgresManager.ensureDatabase", () => {
  it("creates the database only when it does not exist", async () => {
    const h = makeHarness({
      files: { [`${DATA_DIR}/PG_VERSION`]: "17\n" },
      onRun: (call) => {
        if (
          call.command === PATHS.psql &&
          String(call.args.at(-1)).startsWith("SELECT 1 FROM pg_database")
        ) {
          return ok(""); // does not exist
        }
        return ok();
      },
    });
    await h.manager.ensureDatabase("atlas_backend");
    const psqlCalls = h.calls.filter((c) => c.command === PATHS.psql);
    expect(psqlCalls).toHaveLength(2);
    expect(psqlCalls[1]!.args.at(-1)).toBe("CREATE DATABASE atlas_backend");
    // Password travels via PGPASSWORD env, never argv.
    expect(psqlCalls[0]!.env?.PGPASSWORD).toBe("pg-secret");
    expect(psqlCalls[0]!.args.join(" ")).not.toContain("pg-secret");
  });

  it("skips creation when the database already exists", async () => {
    const h = makeHarness({
      onRun: (call) => {
        if (call.command === PATHS.psql) return ok("1\n");
        return ok();
      },
    });
    await h.manager.ensureDatabase("atlas_ai");
    const psqlCalls = h.calls.filter((c) => c.command === PATHS.psql);
    expect(psqlCalls).toHaveLength(1);
  });

  it("rejects an unsafe database name", async () => {
    const h = makeHarness({});
    await expect(h.manager.ensureDatabase("bad; DROP TABLE x")).rejects.toThrow(
      /invalid db name/u,
    );
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
