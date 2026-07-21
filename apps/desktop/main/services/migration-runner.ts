import { outputTail, type CommandRunner } from "./exec";

// First-file-boot data carry-over runner: invokes the ai-backend's offline
// migration CLI (`python -m runtime_adapters.migrate --on-boot`) through the
// SAME injected CommandRunner the relational migration gate uses, and maps its
// exit-code contract onto a store-backend decision for THIS boot.
//
// The CLI's exit contract (see services/ai-backend/src/runtime_adapters/
// migrate.py) is the fail-safe boundary:
//   * 0 — migrated (or nothing to migrate); the file store is authoritative.
//   * 2 — a verify mismatch: the import is NOT trustworthy.
//   * 1 (or any other / null) — any other failure (unreachable source, disk
//         error, killed).
//
// This runner NEVER throws and NEVER deletes anything: any non-success outcome
// resolves to "serve the Postgres store this boot" so a failed import can never
// strand the user with an empty app or lose the still-authoritative Postgres
// data. The source Postgres store is read-only throughout.

/** Why the runner fell back to the Postgres store for this boot. */
export type MigrationFallbackReason =
  | "verify-mismatch"
  | "error"
  | "spawn-error";

/**
 * The store backend to serve for this boot, derived from the migration outcome.
 * `file` only on a clean (exit 0) import; every failure mode is a `postgres`
 * fallback carrying the machine-readable reason for the boot log.
 */
export type MigrationOutcome =
  | { readonly backend: "file"; readonly migrated: true }
  | {
      readonly backend: "postgres";
      readonly migrated: false;
      readonly reason: MigrationFallbackReason;
    };

export interface RunBootMigrationOptions {
  readonly pythonBin: string;
  /** Staged ai-backend service dir — becomes the child's cwd (PYTHONPATH=src). */
  readonly serviceDir: string;
  /** DATABASE_URL of the Postgres `atlas_ai` source (read-only). */
  readonly sourceDatabaseUrl: string;
  /** Destination file-store root (`<userData>/agent-data/v1`). */
  readonly destRoot: string;
  /** Full child env (buildServiceEnv output) — PYTHONPATH + telemetry kill-switch. */
  readonly env: Record<string, string>;
  readonly runner: CommandRunner;
  /** Loud boot-log sink; defaults to a no-op for tests. */
  readonly log?: (message: string) => void;
  readonly tailLines?: number;
}

/**
 * Run the first-file-boot Postgres->file import and map the CLI exit code onto a
 * store-backend decision. Fail-safe by construction: a spawn error or any
 * non-zero exit resolves to the Postgres store for this boot (never throws).
 */
export async function runBootMigration(
  options: RunBootMigrationOptions,
): Promise<MigrationOutcome> {
  const log = options.log ?? (() => {});
  const args = [
    "-m",
    "runtime_adapters.migrate",
    "--on-boot",
    "--source",
    "postgres",
    "--source-database-url",
    options.sourceDatabaseUrl,
    "--dest-root",
    options.destRoot,
  ];

  let result;
  try {
    result = await options.runner(options.pythonBin, args, {
      cwd: options.serviceDir,
      env: options.env,
    });
  } catch (err) {
    // The child could not even be spawned (missing interpreter, EACCES, …).
    // Never let a boot activity crash the boot: fall back to Postgres.
    log(
      `on-boot migration could not start: ${
        err instanceof Error ? err.message : String(err)
      } — serving the Postgres store this boot`,
    );
    return { backend: "postgres", migrated: false, reason: "spawn-error" };
  }

  if (result.code === 0) {
    const tail = outputTail(result, options.tailLines ?? 40);
    if (tail !== "") log(`on-boot migration complete: ${tail}`);
    return { backend: "file", migrated: true };
  }

  const tail = outputTail(result, options.tailLines ?? 40);
  if (result.code === 2) {
    log(
      `on-boot migration verify MISMATCH (exit 2) — the import is not ` +
        `trustworthy; serving the Postgres store this boot:\n${tail}`,
    );
    return { backend: "postgres", migrated: false, reason: "verify-mismatch" };
  }
  log(
    `on-boot migration FAILED (exit ${String(result.code)}) — serving the ` +
      `Postgres store this boot:\n${tail}`,
  );
  return { backend: "postgres", migrated: false, reason: "error" };
}
