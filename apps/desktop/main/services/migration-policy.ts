import { join } from "node:path";

import { AI_FILE_STORE_V1_SEGMENTS } from "./service-env";

// Pure decision layer for the first-file-boot Postgres->file data-continuity
// migration (AC2b N1). The desktop supervisor gathers the impure facts (is the
// active store file? is the file root empty? does Postgres hold history? has a
// prior boot already migrated?) and hands them here; this module decides whether
// to run the migration. Keeping the decision pure means every branch — including
// the ones that protect existing data — is exhaustively unit-tested without a
// filesystem, a database, or a running app.
//
// The migration is a one-time carry-over: it exists so an existing install whose
// history lives in the Postgres `atlas_ai` DB does not start empty the first time
// it boots on the (now default) file store. It must NEVER run against a file
// store that already holds data.

export type StoreBackend = "file" | "postgres";

export interface MigrationFacts {
  /**
   * The ai-backend store backend active for THIS boot, as resolved by
   * `resolveAiStoreBackend`. Migration only makes sense when the app is booting
   * on the file store; under Postgres the relational store is authoritative and
   * there is nothing to import into.
   */
  readonly storeBackend: StoreBackend;
  /**
   * True when the destination file store root already holds conversation data.
   * This is the primary data-safety guard: the carry-over must never write into
   * a non-empty file store (it would risk duplicating or masking real history).
   */
  readonly fileStoreHasData: boolean;
  /**
   * True when the Postgres `atlas_ai` database holds conversation history that
   * would otherwise be stranded. False on a brand-new install (no schema / no
   * rows), which is the common case and correctly skips the migration.
   */
  readonly postgresHasData: boolean;
  /**
   * True when a prior boot already completed (or attempted and marked) the
   * migration — the on-disk marker exists. Ensures the import runs at most once,
   * even if the user later empties the file store on purpose.
   */
  readonly alreadyMigrated: boolean;
}

export type MigrationSkipReason =
  | "store-backend-not-file"
  | "already-migrated"
  | "file-store-not-empty"
  | "postgres-empty";

export type MigrationDecision =
  | { readonly migrate: true }
  | { readonly migrate: false; readonly reason: MigrationSkipReason };

/**
 * Decide whether the first-file-boot Postgres->file migration should run.
 *
 * Runs the import ONLY when every safety condition holds: the app is booting on
 * the file store, the file store is empty/new, Postgres has history to carry
 * over, and no prior boot has already migrated. The skip checks are ordered
 * most-decisive first and each returns a machine-readable reason so the caller
 * can log exactly why a boot did or did not migrate.
 *
 * Data-safety invariants encoded here:
 *   - never migrate under the Postgres backend (nothing to import into);
 *   - never migrate more than once (the marker wins even if the store was later
 *     emptied);
 *   - never migrate into a file store that already has data;
 *   - never migrate when there is nothing in Postgres to carry over.
 */
export function resolveMigrationDecision(
  facts: MigrationFacts,
): MigrationDecision {
  if (facts.storeBackend !== "file") {
    return { migrate: false, reason: "store-backend-not-file" };
  }
  if (facts.alreadyMigrated) {
    return { migrate: false, reason: "already-migrated" };
  }
  if (facts.fileStoreHasData) {
    return { migrate: false, reason: "file-store-not-empty" };
  }
  if (!facts.postgresHasData) {
    return { migrate: false, reason: "postgres-empty" };
  }
  return { migrate: true };
}

/** Relative segments of the one-time-migration marker under userData. */
export const PG_TO_FILE_MIGRATION_MARKER_SEGMENTS = [
  ...AI_FILE_STORE_V1_SEGMENTS,
  ".pg-to-file-migrated",
] as const;

/**
 * Absolute path of the marker file whose presence records that the one-time
 * Postgres->file carry-over already ran: `<userData>/agent-data/v1/
 * .pg-to-file-migrated`. Placed INSIDE the file store root so it travels with
 * the store — if the store is relocated or reset, the marker goes with it, and a
 * genuinely fresh store correctly has no marker. The supervisor writes it after
 * a successful (verified) migration and reads it into `alreadyMigrated`.
 */
export function pgToFileMigrationMarkerPath(userDataDir: string): string {
  return join(userDataDir, ...PG_TO_FILE_MIGRATION_MARKER_SEGMENTS);
}
