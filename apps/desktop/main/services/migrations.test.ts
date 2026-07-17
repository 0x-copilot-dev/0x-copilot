// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { CommandResult } from "./exec";
import { MigrationsFailed, runMigrations } from "./migrations";

describe("runMigrations", () => {
  it("spawns `<python> scripts/migrate.py apply` with cwd + env", async () => {
    const calls: Array<{
      command: string;
      args: readonly string[];
      cwd?: string;
      env?: Record<string, string>;
    }> = [];
    await runMigrations({
      service: "backend",
      pythonBin: "/rt/python/bin/python3",
      serviceDir: "/rt/services/backend",
      env: { DATABASE_URL: "postgresql://x" },
      runner: (command, args, opts) => {
        calls.push({ command, args, cwd: opts?.cwd, env: opts?.env });
        return Promise.resolve({ code: 0, stdout: "ok", stderr: "" });
      },
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({
      command: "/rt/python/bin/python3",
      args: ["scripts/migrate.py", "apply"],
      cwd: "/rt/services/backend",
      env: { DATABASE_URL: "postgresql://x" },
    });
  });

  it("throws typed MigrationsFailed with the output tail on non-zero exit", async () => {
    const lines = Array.from({ length: 60 }, (_, i) => `line-${i}`);
    const result: CommandResult = {
      code: 3,
      stdout: lines.join("\n"),
      stderr: "FATAL: relation broken",
    };
    let caught: unknown = null;
    try {
      await runMigrations({
        service: "ai-backend",
        pythonBin: "py",
        serviceDir: "/svc",
        env: {},
        runner: () => Promise.resolve(result),
        tailLines: 10,
      });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(MigrationsFailed);
    const failure = caught as MigrationsFailed;
    expect(failure.service).toBe("ai-backend");
    expect(failure.exitCode).toBe(3);
    // Tail keeps the LAST lines including stderr, drops the early ones.
    expect(failure.outputTail).toContain("FATAL: relation broken");
    expect(failure.outputTail).toContain("line-59");
    expect(failure.outputTail).not.toContain("line-0\n");
    expect(failure.outputTail.split("\n")).toHaveLength(10);
  });

  it("treats a null exit code (killed) as failure", async () => {
    await expect(
      runMigrations({
        service: "backend",
        pythonBin: "py",
        serviceDir: "/svc",
        env: {},
        runner: () =>
          Promise.resolve({ code: null, stdout: "", stderr: "killed" }),
      }),
    ).rejects.toThrow(MigrationsFailed);
  });
});
