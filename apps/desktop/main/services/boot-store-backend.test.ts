// @vitest-environment node
import { describe, expect, it, vi, type Mock } from "vitest";

import type { StoreBackend } from "./migration-policy";
import {
  resolveBootStoreBackend,
  type BootStoreBackendDeps,
} from "./boot-store-backend";
import type { MigrationOutcome } from "./migration-runner";

const FILE_OK: MigrationOutcome = { backend: "file", migrated: true };
const VERIFY_MISMATCH: MigrationOutcome = {
  backend: "postgres",
  migrated: false,
  reason: "verify-mismatch",
};
const ERROR_OUTCOME: MigrationOutcome = {
  backend: "postgres",
  migrated: false,
  reason: "error",
};

interface Spies {
  fileStoreHasData: Mock<() => Promise<boolean>>;
  postgresHasData: Mock<() => Promise<boolean>>;
  markerExists: Mock<() => Promise<boolean>>;
  runMigration: Mock<() => Promise<MigrationOutcome>>;
  writeMarker: Mock<() => Promise<void>>;
}

interface Overrides {
  configuredBackend?: StoreBackend;
  fileStoreHasData?: boolean | (() => Promise<boolean>);
  postgresHasData?: boolean | (() => Promise<boolean>);
  markerExists?: boolean | (() => Promise<boolean>);
  runMigration?: MigrationOutcome | (() => Promise<MigrationOutcome>);
  writeMarker?: () => Promise<void>;
  log?: (message: string) => void;
}

function deps(overrides: Overrides = {}): {
  deps: BootStoreBackendDeps;
  spies: Spies;
} {
  const asThunk = <T>(v: T | (() => Promise<T>)): (() => Promise<T>) =>
    typeof v === "function"
      ? (v as () => Promise<T>)
      : () => Promise.resolve(v);

  const spies: Spies = {
    fileStoreHasData: vi.fn(asThunk(overrides.fileStoreHasData ?? false)),
    postgresHasData: vi.fn(asThunk(overrides.postgresHasData ?? true)),
    markerExists: vi.fn(asThunk(overrides.markerExists ?? false)),
    runMigration: vi.fn(asThunk(overrides.runMigration ?? FILE_OK)),
    writeMarker: vi.fn(overrides.writeMarker ?? (() => Promise.resolve())),
  };
  return {
    spies,
    deps: {
      configuredBackend: overrides.configuredBackend ?? "file",
      fileStoreHasData: () => spies.fileStoreHasData(),
      postgresHasData: () => spies.postgresHasData(),
      markerExists: () => spies.markerExists(),
      runMigration: () => spies.runMigration(),
      writeMarker: () => spies.writeMarker(),
      log: overrides.log,
    },
  };
}

describe("resolveBootStoreBackend", () => {
  it("migrate success -> file store + marker written", async () => {
    const { deps: d, spies } = deps({ runMigration: FILE_OK });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "file",
      migrated: true,
      note: "migrated",
    });
    expect(spies.runMigration).toHaveBeenCalledOnce();
    expect(spies.writeMarker).toHaveBeenCalledOnce();
  });

  it("verify mismatch (exit 2) -> Postgres fallback + NO marker", async () => {
    const { deps: d, spies } = deps({ runMigration: VERIFY_MISMATCH });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "postgres",
      migrated: false,
      note: "fallback-verify-mismatch",
    });
    expect(spies.writeMarker).not.toHaveBeenCalled();
  });

  it("migration error (exit 1) -> Postgres fallback + NO marker", async () => {
    const { deps: d, spies } = deps({ runMigration: ERROR_OUTCOME });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "postgres",
      migrated: false,
      note: "fallback-error",
    });
    expect(spies.writeMarker).not.toHaveBeenCalled();
  });

  it("already migrated (marker present) -> file, no migration, no pg probe", async () => {
    const { deps: d, spies } = deps({ markerExists: true });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "file",
      migrated: false,
      note: "already-migrated",
    });
    expect(spies.runMigration).not.toHaveBeenCalled();
    // Steady-state boot after migration does NO database work.
    expect(spies.postgresHasData).not.toHaveBeenCalled();
    expect(spies.fileStoreHasData).not.toHaveBeenCalled();
  });

  it("empty Postgres (fresh install) -> file, no migration", async () => {
    const { deps: d, spies } = deps({ postgresHasData: false });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "file",
      migrated: false,
      note: "postgres-empty",
    });
    expect(spies.runMigration).not.toHaveBeenCalled();
  });

  it("file store already has data -> file, no migration, no pg probe", async () => {
    const { deps: d, spies } = deps({ fileStoreHasData: true });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "file",
      migrated: false,
      note: "file-store-not-empty",
    });
    expect(spies.runMigration).not.toHaveBeenCalled();
    expect(spies.postgresHasData).not.toHaveBeenCalled();
  });

  it("configured Postgres backend -> postgres, no probing at all", async () => {
    const { deps: d, spies } = deps({ configuredBackend: "postgres" });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "postgres",
      migrated: false,
      note: "configured-postgres",
    });
    expect(spies.markerExists).not.toHaveBeenCalled();
    expect(spies.fileStoreHasData).not.toHaveBeenCalled();
    expect(spies.postgresHasData).not.toHaveBeenCalled();
    expect(spies.runMigration).not.toHaveBeenCalled();
  });

  it("a probe error is fail-safe -> Postgres this boot, no migration", async () => {
    const { deps: d, spies } = deps({
      postgresHasData: () => Promise.reject(new Error("pg unreachable")),
    });
    const result = await resolveBootStoreBackend(d);
    expect(result).toEqual({
      backend: "postgres",
      migrated: false,
      note: "probe-failed-fallback",
    });
    expect(spies.runMigration).not.toHaveBeenCalled();
  });

  it("a marker-write failure after a clean import still serves file (self-correcting)", async () => {
    const { deps: d, spies } = deps({
      runMigration: FILE_OK,
      writeMarker: () => Promise.reject(new Error("EACCES")),
    });
    const result = await resolveBootStoreBackend(d);
    // The import succeeded; the file store now holds the data, so file wins even
    // though the marker could not be persisted.
    expect(result).toEqual({
      backend: "file",
      migrated: true,
      note: "migrated",
    });
    expect(spies.writeMarker).toHaveBeenCalledOnce();
  });

  it("does not migrate into a non-empty file store even with Postgres data pending", async () => {
    const { deps: d, spies } = deps({
      fileStoreHasData: true,
      postgresHasData: true,
    });
    const result = await resolveBootStoreBackend(d);
    expect(result.backend).toBe("file");
    expect(result.migrated).toBe(false);
    expect(spies.runMigration).not.toHaveBeenCalled();
  });
});
