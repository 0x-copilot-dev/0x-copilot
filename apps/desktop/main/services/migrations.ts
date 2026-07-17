import { outputTail, type CommandRunner } from "./exec";
import type { SupervisedServiceName } from "./runtime-paths";

/** Stateful services with a scripts/migrate.py gate. Facade is stateless. */
export type MigratableServiceName = Exclude<
  SupervisedServiceName,
  "backend-facade"
>;

export class MigrationsFailed extends Error {
  readonly service: MigratableServiceName;
  readonly exitCode: number | null;
  readonly outputTail: string;

  constructor(
    service: MigratableServiceName,
    exitCode: number | null,
    tail: string,
  ) {
    super(
      `migrations for ${service} exited with code ${String(exitCode)}:\n${tail}`,
    );
    this.name = "MigrationsFailed";
    this.service = service;
    this.exitCode = exitCode;
    this.outputTail = tail;
  }
}

export interface RunMigrationsOptions {
  readonly service: MigratableServiceName;
  readonly pythonBin: string;
  /** Staged service dir — becomes the child's cwd. */
  readonly serviceDir: string;
  /** Full child env (buildServiceEnv output — same env the app gets). */
  readonly env: Record<string, string>;
  readonly runner: CommandRunner;
  readonly tailLines?: number;
}

// Migration gate: `<python> scripts/migrate.py apply` with cwd at the
// staged service dir. Non-zero exit throws a typed MigrationsFailed
// carrying the tail of combined output for the fatal boot screen.
export async function runMigrations(
  options: RunMigrationsOptions,
): Promise<void> {
  const result = await options.runner(
    options.pythonBin,
    ["scripts/migrate.py", "apply"],
    { cwd: options.serviceDir, env: options.env },
  );
  if (result.code !== 0) {
    throw new MigrationsFailed(
      options.service,
      result.code,
      outputTail(result, options.tailLines ?? 40),
    );
  }
}
