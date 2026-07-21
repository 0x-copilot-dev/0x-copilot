// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  pgToFileMigrationMarkerPath,
  resolveMigrationDecision,
  type MigrationFacts,
} from "./migration-policy";

// A fully-eligible baseline: booting on file, empty file store, Postgres has
// history, never migrated. Each test flips exactly one fact to pin one branch.
function eligible(overrides: Partial<MigrationFacts> = {}): MigrationFacts {
  return {
    storeBackend: "file",
    fileStoreHasData: false,
    postgresHasData: true,
    alreadyMigrated: false,
    ...overrides,
  };
}

describe("resolveMigrationDecision", () => {
  it("migrates when file is active, file store empty, Postgres has data, not yet migrated", () => {
    expect(resolveMigrationDecision(eligible())).toEqual({ migrate: true });
  });

  it("skips under the Postgres backend — there is nothing to import into", () => {
    expect(
      resolveMigrationDecision(eligible({ storeBackend: "postgres" })),
    ).toEqual({ migrate: false, reason: "store-backend-not-file" });
  });

  it("skips when a prior boot already migrated (marker present)", () => {
    expect(
      resolveMigrationDecision(eligible({ alreadyMigrated: true })),
    ).toEqual({
      migrate: false,
      reason: "already-migrated",
    });
  });

  it("skips when the file store already holds data (never clobber real history)", () => {
    expect(
      resolveMigrationDecision(eligible({ fileStoreHasData: true })),
    ).toEqual({
      migrate: false,
      reason: "file-store-not-empty",
    });
  });

  it("skips a fresh install where Postgres has no history to carry over", () => {
    expect(
      resolveMigrationDecision(eligible({ postgresHasData: false })),
    ).toEqual({
      migrate: false,
      reason: "postgres-empty",
    });
  });

  it("the Postgres backend wins over every other fact", () => {
    // Even with file data AND a marker AND no pg data, backend != file is the
    // first, most-decisive gate.
    expect(
      resolveMigrationDecision({
        storeBackend: "postgres",
        fileStoreHasData: true,
        postgresHasData: false,
        alreadyMigrated: true,
      }),
    ).toEqual({ migrate: false, reason: "store-backend-not-file" });
  });

  it("the marker wins even if the file store was later emptied", () => {
    // alreadyMigrated is checked before fileStoreHasData/postgresHasData so a
    // user who clears their file store post-migration is not re-imported.
    expect(
      resolveMigrationDecision(
        eligible({ alreadyMigrated: true, fileStoreHasData: false }),
      ),
    ).toEqual({ migrate: false, reason: "already-migrated" });
  });

  it("never migrates into a non-empty file store even with pending Postgres data", () => {
    // The core data-safety guarantee: fileStoreHasData beats postgresHasData.
    expect(
      resolveMigrationDecision(
        eligible({ fileStoreHasData: true, postgresHasData: true }),
      ),
    ).toEqual({ migrate: false, reason: "file-store-not-empty" });
  });

  it("is exhaustive — every fact combination yields a decision", () => {
    for (const storeBackend of ["file", "postgres"] as const) {
      for (const fileStoreHasData of [false, true]) {
        for (const postgresHasData of [false, true]) {
          for (const alreadyMigrated of [false, true]) {
            const decision = resolveMigrationDecision({
              storeBackend,
              fileStoreHasData,
              postgresHasData,
              alreadyMigrated,
            });
            // The one and only combination that migrates.
            const shouldMigrate =
              storeBackend === "file" &&
              !alreadyMigrated &&
              !fileStoreHasData &&
              postgresHasData;
            expect(decision.migrate).toBe(shouldMigrate);
          }
        }
      }
    }
  });
});

describe("pgToFileMigrationMarkerPath", () => {
  it("places the marker inside the file store root so it travels with the store", () => {
    expect(pgToFileMigrationMarkerPath("/user-data")).toBe(
      "/user-data/agent-data/v1/.pg-to-file-migrated",
    );
  });
});
