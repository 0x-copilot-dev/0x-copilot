// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { CommandResult, RunCommandOptions } from "./exec";
import { runBootMigration } from "./migration-runner";

interface Call {
  command: string;
  args: readonly string[];
  options?: RunCommandOptions;
}

function harness(result: CommandResult | (() => Promise<CommandResult>)) {
  const calls: Call[] = [];
  const logs: string[] = [];
  const runner = (
    command: string,
    args: readonly string[],
    options?: RunCommandOptions,
  ): Promise<CommandResult> => {
    calls.push({ command, args, options });
    return typeof result === "function" ? result() : Promise.resolve(result);
  };
  const run = () =>
    runBootMigration({
      pythonBin: "/rt/python/bin/python3",
      serviceDir: "/rt/services/ai-backend",
      sourceDatabaseUrl: "postgresql://atlas:pw@127.0.0.1:5432/atlas_ai",
      destRoot: "/user-data/agent-data/v1",
      env: { PYTHONPATH: "src:site-packages" },
      runner,
      log: (m) => logs.push(m),
    });
  return { run, calls, logs };
}

describe("runBootMigration", () => {
  it("invokes `python -m runtime_adapters.migrate --on-boot` with source + dest", async () => {
    const h = harness({
      code: 0,
      stdout: "migrated 3 conversations",
      stderr: "",
    });
    await h.run();
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0].command).toBe("/rt/python/bin/python3");
    expect(h.calls[0].args).toEqual([
      "-m",
      "runtime_adapters.migrate",
      "--on-boot",
      "--source",
      "postgres",
      "--source-database-url",
      "postgresql://atlas:pw@127.0.0.1:5432/atlas_ai",
      "--dest-root",
      "/user-data/agent-data/v1",
    ]);
    expect(h.calls[0].options?.cwd).toBe("/rt/services/ai-backend");
    expect(h.calls[0].options?.env).toEqual({
      PYTHONPATH: "src:site-packages",
    });
  });

  it("exit 0 -> file store authoritative (migrated)", async () => {
    const h = harness({ code: 0, stdout: "ok", stderr: "" });
    await expect(h.run()).resolves.toEqual({ backend: "file", migrated: true });
  });

  it("exit 2 (verify mismatch) -> Postgres fallback, reason verify-mismatch", async () => {
    const h = harness({
      code: 2,
      stdout: "",
      stderr: "VERIFY FAILED: mismatch",
    });
    await expect(h.run()).resolves.toEqual({
      backend: "postgres",
      migrated: false,
      reason: "verify-mismatch",
    });
    expect(h.logs.join("\n")).toContain("verify MISMATCH");
  });

  it("exit 1 (any error) -> Postgres fallback, reason error", async () => {
    const h = harness({ code: 1, stdout: "", stderr: "ON-BOOT IMPORT FAILED" });
    await expect(h.run()).resolves.toEqual({
      backend: "postgres",
      migrated: false,
      reason: "error",
    });
  });

  it("null exit code (killed) -> Postgres fallback, reason error", async () => {
    const h = harness({ code: null, stdout: "", stderr: "killed" });
    await expect(h.run()).resolves.toEqual({
      backend: "postgres",
      migrated: false,
      reason: "error",
    });
  });

  it("a spawn error never throws -> Postgres fallback, reason spawn-error", async () => {
    const h = harness(() => Promise.reject(new Error("ENOENT: no python")));
    await expect(h.run()).resolves.toEqual({
      backend: "postgres",
      migrated: false,
      reason: "spawn-error",
    });
    expect(h.logs.join("\n")).toContain("could not start");
  });
});
