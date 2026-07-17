import { spawn as nodeSpawn } from "node:child_process";

// Minimal short-lived-command abstraction shared by postgres.ts and
// migrations.ts. Long-running children (uvicorn) use python-service.ts
// instead. Tests inject a fake CommandRunner; production uses
// createCommandRunner() over child_process.spawn.

export interface CommandResult {
  readonly code: number | null;
  readonly stdout: string;
  readonly stderr: string;
}

export interface RunCommandOptions {
  readonly cwd?: string;
  readonly env?: Record<string, string>;
  readonly timeoutMs?: number;
}

export type CommandRunner = (
  command: string,
  args: readonly string[],
  options?: RunCommandOptions,
) => Promise<CommandResult>;

export function createCommandRunner(
  spawnFn: typeof nodeSpawn = nodeSpawn,
): CommandRunner {
  return (command, args, options = {}) =>
    new Promise<CommandResult>((resolve, reject) => {
      const child = spawnFn(command, [...args], {
        cwd: options.cwd,
        env: options.env,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stdout = "";
      let stderr = "";
      let settled = false;
      let timer: NodeJS.Timeout | null = null;
      if (options.timeoutMs !== undefined) {
        timer = setTimeout(() => {
          child.kill("SIGKILL");
        }, options.timeoutMs);
        timer.unref?.();
      }
      child.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      child.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      child.on("error", (err) => {
        if (settled) return;
        settled = true;
        if (timer !== null) clearTimeout(timer);
        reject(err);
      });
      child.on("close", (code) => {
        if (settled) return;
        settled = true;
        if (timer !== null) clearTimeout(timer);
        resolve({ code, stdout, stderr });
      });
    });
}

/** Last `lines` lines of combined output, for typed error payloads. */
export function outputTail(result: CommandResult, lines: number): string {
  const combined = `${result.stdout}\n${result.stderr}`.trim();
  if (combined === "") return "";
  const all = combined.split(/\r?\n/u);
  return all.slice(Math.max(0, all.length - lines)).join("\n");
}
