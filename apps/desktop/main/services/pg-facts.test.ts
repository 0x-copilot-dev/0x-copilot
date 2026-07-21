// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { CommandResult, RunCommandOptions } from "./exec";
import { PostgresProbeError, postgresAiStoreHasRows } from "./pg-facts";

interface Call {
  command: string;
  args: readonly string[];
  options?: RunCommandOptions;
}

function harness(result: CommandResult) {
  const calls: Call[] = [];
  const runner = (
    command: string,
    args: readonly string[],
    options?: RunCommandOptions,
  ): Promise<CommandResult> => {
    calls.push({ command, args, options });
    return Promise.resolve(result);
  };
  const run = () =>
    postgresAiStoreHasRows({
      pythonBin: "/rt/python/bin/python3",
      pythonSitePackages: "/rt/services/ai-backend/site-packages",
      pgPort: 5432,
      pgPassword: "pg+pass/x",
      runner,
    });
  return { run, calls };
}

describe("postgresAiStoreHasRows", () => {
  it("connects to atlas_ai as the superuser with the password off argv", async () => {
    const h = harness({ code: 0, stdout: "EMPTY\n", stderr: "" });
    await h.run();
    expect(h.calls[0].command).toBe("/rt/python/bin/python3");
    expect(h.calls[0].args[0]).toBe("-c");
    // conninfo is the last arg and carries NO password.
    const conninfo = h.calls[0].args.at(-1)!;
    expect(conninfo).toBe("postgresql://atlas@127.0.0.1:5432/atlas_ai");
    expect(conninfo).not.toContain("pg+pass");
    expect(h.calls[0].options?.env).toMatchObject({
      PYTHONPATH: "/rt/services/ai-backend/site-packages",
      PGPASSWORD: "pg+pass/x",
    });
  });

  it("HASDATA -> true", async () => {
    const h = harness({ code: 0, stdout: "HASDATA\n", stderr: "" });
    await expect(h.run()).resolves.toBe(true);
  });

  it("EMPTY -> false", async () => {
    const h = harness({ code: 0, stdout: "EMPTY\n", stderr: "" });
    await expect(h.run()).resolves.toBe(false);
  });

  it("tolerates preceding stdout noise, reading the last line as the verdict", async () => {
    const h = harness({
      code: 0,
      stdout: "warning: foo\nHASDATA\n",
      stderr: "",
    });
    await expect(h.run()).resolves.toBe(true);
  });

  it("throws PostgresProbeError on a non-zero exit", async () => {
    const h = harness({ code: 1, stdout: "", stderr: "could not connect" });
    await expect(h.run()).rejects.toBeInstanceOf(PostgresProbeError);
  });

  it("throws PostgresProbeError on unexpected output", async () => {
    const h = harness({ code: 0, stdout: "???\n", stderr: "" });
    await expect(h.run()).rejects.toBeInstanceOf(PostgresProbeError);
  });
});
