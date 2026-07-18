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

  /** Stage bytes under a generated filename; returns an opaque `stg://` ref. */
  async stage(kind: "screenshot", bytes: Uint8Array): Promise<StagedArtifact> {
    if (!this.#ensured) {
      await this.#cfg.fs.mkdir(this.#dir, { recursive: true, mode: 0o700 });
      this.#ensured = true;
    }
    const name = `${kind}_${this.#randomId()}.bin`;
    await this.#cfg.fs.writeFile(this.#join(this.#dir, name), bytes);
    return {
      ref: `stg://${this.#cfg.runId}/${name}`,
      byteLength: bytes.byteLength,
    };
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
