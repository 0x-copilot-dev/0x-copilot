import { join } from "node:path";

// Resolves the packaged runtime layout (RESOURCE CONTRACT).
//
// The self-contained runtime is staged by tools/desktop-runtime/stage.mjs
// into a per-platform/-arch directory and consumed identically by the
// PROVEN tools/desktop-runtime/run-local.mjs smoke harness. This module is
// the packaged-app equivalent and MUST resolve the SAME tree:
//
//   <runtimeRoot>/
//     python/bin/python3            (darwin/linux; symlink -> python3.13)
//     python/python.exe             (win32; at the python/ root, NOT bin/)
//     postgres/bin/{initdb,pg_ctl}  (zonky bundle ships ONLY these two + `postgres`;
//                                    NO psql / pg_isready / createdb)
//     services/{backend,ai-backend,backend-facade}/{src,site-packages,migrations?,scripts?}
//     staging-manifest.json
//
// runtimeRoot = <base>/runtime/<platform>-<arch>, where <base> is
// process.resourcesPath (packaged) or COPILOT_RUNTIME_DIR (dev). This is
// EXACTLY the path run-local.mjs computes (`join(dest, "runtime", `${platform}-${arch}`)`),
// so the dev override stays `apps/desktop/resources` and the packaged
// extraResources maps the staged `runtime/` dir (which contains the single
// build-arch `<platform>-<arch>/` subtree) into `<resourcesPath>/runtime/`.
//
// This module only computes paths — existence checks happen at spawn time so
// unit tests never touch the real filesystem.

export type SupervisedServiceName = "backend" | "ai-backend" | "backend-facade";

export interface RuntimePathsConfig {
  /** COPILOT_RUNTIME_DIR when set (dev override, e.g. apps/desktop/resources). */
  readonly runtimeDirOverride?: string | undefined;
  /** process.resourcesPath in a packaged app. */
  readonly resourcesPath: string;
  /** process.platform; controls the .exe suffix + the python bin path. */
  readonly platform?: NodeJS.Platform;
  /** process.arch; selects the staged `<platform>-<arch>` subtree. */
  readonly arch?: NodeJS.Architecture;
}

export interface RuntimePaths {
  readonly runtimeRoot: string;
  readonly pythonBin: string;
  /**
   * The postgres bundle ships ONLY initdb + pg_ctl + the postgres server.
   * Readiness is gated by `pg_ctl -w start`; database creation goes through
   * the staged python + psycopg (there is no psql/createdb to shell out to).
   */
  readonly pgBin: {
    readonly initdb: string;
    readonly pgCtl: string;
  };
  serviceDir(name: SupervisedServiceName): string;
}

export function resolveRuntimePaths(config: RuntimePathsConfig): RuntimePaths {
  const platform = config.platform ?? process.platform;
  const arch = config.arch ?? process.arch;
  const exe = platform === "win32" ? ".exe" : "";
  const base =
    config.runtimeDirOverride !== undefined && config.runtimeDirOverride !== ""
      ? config.runtimeDirOverride
      : config.resourcesPath;
  const runtimeRoot = join(base, "runtime", `${platform}-${arch}`);
  const pgBinDir = join(runtimeRoot, "postgres", "bin");
  // python-build-standalone install_only: `python.exe` sits at the python/
  // root on Windows; on unix the interpreter is python/bin/python3.
  const pythonBin =
    platform === "win32"
      ? join(runtimeRoot, "python", "python.exe")
      : join(runtimeRoot, "python", "bin", "python3");
  return {
    runtimeRoot,
    pythonBin,
    pgBin: {
      initdb: join(pgBinDir, `initdb${exe}`),
      pgCtl: join(pgBinDir, `pg_ctl${exe}`),
    },
    serviceDir: (name) => join(runtimeRoot, "services", name),
  };
}
