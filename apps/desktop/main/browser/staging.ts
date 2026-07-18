// AC8 agentic browser — per-run artifact staging.
//
// Screenshots (and, in a later slice, downloads) land in a per-RUN staging
// directory OUTSIDE any browser profile, with generated filenames. The move to
// the AC4 object store is DEFERRED — this foundation returns an opaque staging
// ref (`stg://<runId>/<name>`) that a later slice resolves to an AC4 artifact.
// Cleanup removes the whole run directory on session close / cancel.

export interface StagingFsPort {
  mkdir(
    path: string,
    opts: { recursive: boolean; mode?: number },
  ): Promise<void>;
  writeFile(path: string, data: Uint8Array): Promise<void>;
  rm(path: string, opts: { recursive: boolean; force: boolean }): Promise<void>;
}

export interface StagingAreaConfig {
  readonly stagingRoot: string;
  readonly runId: string;
  readonly fs: StagingFsPort;
  readonly randomId?: () => string;
  readonly join?: (...parts: string[]) => string;
}

export interface StagedArtifact {
  /** Opaque staging ref — resolved to an AC4 artifact in a later slice. */
  readonly ref: string;
  readonly byteLength: number;
  /** Absolute staging path the bytes were written to (worker-internal). */
  readonly path: string;
}

/** Kinds of bytes that may be staged under a run directory. */
export type StagedKind = "screenshot" | "download";

/** Only lowercase alphanumerics survive as a staged-file extension. */
function safeExtension(ext: string | undefined): string {
  if (ext === undefined) return "bin";
  const cleaned = ext.replace(/[^a-z0-9]/giu, "").toLowerCase();
  return cleaned === "" ? "bin" : cleaned.slice(0, 16);
}

export class StagingArea {
  readonly #cfg: StagingAreaConfig;
  readonly #randomId: () => string;
  readonly #join: (...parts: string[]) => string;
  readonly #dir: string;
  #ensured = false;

  constructor(cfg: StagingAreaConfig) {
    this.#cfg = cfg;
    this.#randomId =
      cfg.randomId ?? (() => Math.random().toString(36).slice(2, 12));
    this.#join = cfg.join ?? ((...p) => p.join("/"));
    this.#dir = this.#join(cfg.stagingRoot, cfg.runId);
  }

  /**
   * Stage bytes under a GENERATED filename inside the per-run directory; returns
   * an opaque `stg://` ref. The filename is `<kind>_<randomId>.<ext>` with the
   * extension sanitized to `[a-z0-9]`, so a caller-supplied (e.g. site-suggested)
   * extension can never inject a path separator or traversal. The full path is
   * asserted to stay CONTAINED within the run directory before any write.
   */
  async stage(
    kind: StagedKind,
    bytes: Uint8Array,
    opts?: { ext?: string },
  ): Promise<StagedArtifact> {
    if (!this.#ensured) {
      await this.#cfg.fs.mkdir(this.#dir, { recursive: true, mode: 0o700 });
      this.#ensured = true;
    }
    const name = `${kind}_${this.#randomId()}.${safeExtension(opts?.ext)}`;
    const path = this.#join(this.#dir, name);
    // Defense in depth: never write outside the run directory even if `join`
    // or `name` were somehow manipulated.
    if (!this.#isContained(path)) {
      throw new Error("staging path escapes the run directory");
    }
    await this.#cfg.fs.writeFile(path, bytes);
    return {
      ref: `stg://${this.#cfg.runId}/${name}`,
      byteLength: bytes.byteLength,
      path,
    };
  }

  /** True when `path` is the run directory itself or a direct child of it. */
  #isContained(path: string): boolean {
    if (path === this.#dir) return false;
    const prefixes = [`${this.#dir}/`, `${this.#dir}\\`];
    if (!prefixes.some((p) => path.startsWith(p))) return false;
    const rest = path.slice(this.#dir.length + 1);
    // A contained artifact is a single leaf: no nested dirs, no traversal.
    return !rest.includes("/") && !rest.includes("\\") && !rest.includes("..");
  }

  /** Remove the whole run staging directory. Best-effort. */
  async cleanup(): Promise<void> {
    try {
      await this.#cfg.fs.rm(this.#dir, { recursive: true, force: true });
    } catch {
      // Best-effort; swept on next restart.
    }
  }
}
