import { join } from "node:path";

// Resolves the packaged runtime layout (RESOURCE CONTRACT):
//
//   <resourcesPath>/runtime/
//     python/bin/python3(.exe)
//     pgsql/bin/{initdb,pg_ctl,pg_isready,psql}(.exe)
//     services/{backend,ai-backend,backend-facade}/{src,site-packages,migrations?,scripts?}
//
// In dev, ATLAS_RUNTIME_DIR points at apps/desktop/resources (i.e. it
// substitutes for <resourcesPath>); the staged tree below it is identical.
// This module only computes paths — existence checks happen at spawn time
// so unit tests never touch the real filesystem.

export type SupervisedServiceName = "backend" | "ai-backend" | "backend-facade";

export interface RuntimePathsConfig {
  /** ATLAS_RUNTIME_DIR when set (dev override). */
  readonly runtimeDirOverride?: string | undefined;
  /** process.resourcesPath in a packaged app. */
  readonly resourcesPath: string;
  /** process.platform; controls the .exe suffix. */
  readonly platform?: NodeJS.Platform;
}

export interface RuntimePaths {
  readonly runtimeRoot: string;
  readonly pythonBin: string;
  readonly pgBin: {
    readonly initdb: string;
    readonly pgCtl: string;
    readonly pgIsReady: string;
    readonly psql: string;
  };
  serviceDir(name: SupervisedServiceName): string;
}

export function resolveRuntimePaths(config: RuntimePathsConfig): RuntimePaths {
  const platform = config.platform ?? process.platform;
  const exe = platform === "win32" ? ".exe" : "";
  const base =
    config.runtimeDirOverride !== undefined && config.runtimeDirOverride !== ""
      ? config.runtimeDirOverride
      : config.resourcesPath;
  const runtimeRoot = join(base, "runtime");
  const pgBinDir = join(runtimeRoot, "pgsql", "bin");
  return {
    runtimeRoot,
    pythonBin: join(runtimeRoot, "python", "bin", `python3${exe}`),
    pgBin: {
      initdb: join(pgBinDir, `initdb${exe}`),
      pgCtl: join(pgBinDir, `pg_ctl${exe}`),
      pgIsReady: join(pgBinDir, `pg_isready${exe}`),
      psql: join(pgBinDir, `psql${exe}`),
    },
    serviceDir: (name) => join(runtimeRoot, "services", name),
  };
}
