import type { MigrationOutcome } from "./migration-runner";
import {
  resolveMigrationDecision,
  type StoreBackend,
} from "./migration-policy";

// The boot-time BRAIN for the first-file-boot Postgres->file carry-over. It ties
// the pure decision layer (migration-policy.ts) to the impure capabilities the
// supervisor supplies (fs/pg probes, the migration runner, the marker writer)
// and yields the single fact the boot needs: which store backend to serve THIS
// boot. Every capability is an injected thunk so the whole safety matrix is
// unit-tested against fakes — no filesystem, database, or running app.
//
// HARD SAFETY INVARIANTS (all enforced here):
//   * Never deletes Postgres data — the migration runner reads the source
//     read-only; this module never touches it.
//   * Never migrates into a non-empty file store, more than once, or when there
//     is nothing in Postgres to carry over (delegated to resolveMigrationDecision).
//   * Fail-safe, not fail-closed — ANY probe error or migration failure serves
//     the Postgres store this boot (never an empty/bricked app).
//   * Writes the "migrated" marker ONLY on a clean import, so a failed import
//     retries next boot instead of being falsely recorded as done.

export interface BootStoreBackendDeps {
  /**
   * The env-resolved backend (`resolveAiStoreBackend`). When it is not `file`
   * there is nothing to migrate into: the relational store stays authoritative
   * and this module short-circuits without probing.
   */
  readonly configuredBackend: StoreBackend;
  /** True when the destination file store already holds conversation data. */
  fileStoreHasData(): Promise<boolean>;
  /** True when Postgres `atlas_ai` holds conversation history to carry over. */
  postgresHasData(): Promise<boolean>;
  /** True when a prior boot already recorded a completed migration (marker). */
  markerExists(): Promise<boolean>;
  /** Run the Postgres->file import; maps the CLI exit code to an outcome. */
  runMigration(): Promise<MigrationOutcome>;
  /** Record a completed, verified migration so it never re-runs. */
  writeMarker(): Promise<void>;
  /** Loud boot-log sink; defaults to a no-op. */
  readonly log?: (message: string) => void;
}

export interface BootStoreBackendResult {
  /** The store backend to serve for this boot. */
  readonly backend: StoreBackend;
  /** True only when a fresh import ran and verified this boot. */
  readonly migrated: boolean;
  /** Machine-readable summary of why this outcome was chosen (for logs/tests). */
  readonly note:
    | "configured-postgres"
    | "probe-failed-fallback"
    | "store-backend-not-file"
    | "already-migrated"
    | "file-store-not-empty"
    | "postgres-empty"
    | "migrated"
    | "fallback-verify-mismatch"
    | "fallback-error"
    | "fallback-spawn-error";
}

/**
 * Decide the store backend for this boot, running the first-file-boot migration
 * only when every safety condition holds, and falling back to Postgres on any
 * uncertainty.
 */
export async function resolveBootStoreBackend(
  deps: BootStoreBackendDeps,
): Promise<BootStoreBackendResult> {
  const log = deps.log ?? (() => {});

  // Under the Postgres backend the relational store is authoritative; skip all
  // probing and never touch the file store.
  if (deps.configuredBackend !== "file") {
    return {
      backend: "postgres",
      migrated: false,
      note: "configured-postgres",
    };
  }

  // Gather facts lazily and most-decisive first: the marker and the (cheap) file
  // probe can each short-circuit before we spawn the (costly) Postgres probe, so
  // the steady-state boot after migration does no database work.
  let alreadyMigrated: boolean;
  let fileStoreHasData: boolean;
  let postgresHasData: boolean;
  try {
    alreadyMigrated = await deps.markerExists();
    fileStoreHasData = alreadyMigrated ? false : await deps.fileStoreHasData();
    postgresHasData =
      alreadyMigrated || fileStoreHasData
        ? false
        : await deps.postgresHasData();
  } catch (err) {
    // We could not establish the facts (fs/pg probe failed). The safest action
    // is to serve the still-authoritative Postgres store this boot rather than
    // risk migrating on incomplete information or starting empty.
    log(
      `migration precheck failed: ${
        err instanceof Error ? err.message : String(err)
      } — serving the Postgres store this boot (no migration)`,
    );
    return {
      backend: "postgres",
      migrated: false,
      note: "probe-failed-fallback",
    };
  }

  const decision = resolveMigrationDecision({
    storeBackend: "file",
    fileStoreHasData,
    postgresHasData,
    alreadyMigrated,
  });

  if (!decision.migrate) {
    // Every skip reason keeps the app on its configured file store: nothing to
    // carry over (postgres-empty / fresh install), the store already holds data,
    // or a prior boot already migrated.
    return { backend: "file", migrated: false, note: decision.reason };
  }

  log(
    "first-file boot with Postgres history and an empty file store — running the one-time carry-over import",
  );
  const outcome = await deps.runMigration();

  if (outcome.backend === "file") {
    try {
      await deps.writeMarker();
    } catch (err) {
      // The import succeeded but the marker could not be written. This is NOT a
      // data-safety problem: the file store now holds the imported data, so the
      // next boot's file-store-not-empty guard already prevents a re-import — a
      // missing marker is at worst a redundant idempotent re-check.
      log(
        `on-boot migration succeeded but the marker could not be written: ${
          err instanceof Error ? err.message : String(err)
        } — the file store now holds the data, so re-migration is a safe no-op`,
      );
    }
    return { backend: "file", migrated: true, note: "migrated" };
  }

  // Fail-safe: a verify mismatch or any error serves Postgres this boot and does
  // NOT write the marker, so the import is retried on the next boot.
  return {
    backend: "postgres",
    migrated: false,
    note: `fallback-${outcome.reason}` as BootStoreBackendResult["note"],
  };
}
