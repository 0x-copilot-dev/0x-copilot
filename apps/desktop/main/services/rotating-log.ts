import { dirname } from "node:path";

export interface RotatingLogFs {
  appendFile(path: string, data: string): Promise<void>;
  stat(path: string): Promise<{ size: number }>;
  rename(oldPath: string, newPath: string): Promise<void>;
  rm(path: string, options: { force: boolean }): Promise<void>;
  mkdir(path: string, options: { recursive: boolean }): Promise<unknown>;
}

export interface RotatingLogConfig {
  readonly path: string;
  readonly fs: RotatingLogFs;
  /** Rotate when the active file would exceed this. Default 10 MiB. */
  readonly maxBytes?: number;
  /** Total files kept: <name>.log, .log.1, .log.2. Default 3. */
  readonly maxFiles?: number;
}

// Size-capped rotating log sink: userData/logs/<svc>.log, 10MB x 3.
// write() is fire-and-forget but internally serialised so rotation and
// appends never interleave; flush() awaits the queue (tests + shutdown).
export class RotatingLogWriter {
  readonly #path: string;
  readonly #fs: RotatingLogFs;
  readonly #maxBytes: number;
  readonly #maxFiles: number;
  #size: number | null = null;
  #queue: Promise<void> = Promise.resolve();

  constructor(config: RotatingLogConfig) {
    this.#path = config.path;
    this.#fs = config.fs;
    this.#maxBytes = config.maxBytes ?? 10 * 1024 * 1024;
    this.#maxFiles = config.maxFiles ?? 3;
  }

  write(chunk: string): void {
    this.#queue = this.#queue
      .then(() => this.#append(chunk))
      .catch(() => {
        // Logging must never take the service down; drop on write error
        // and let the next append retry with a fresh stat.
        this.#size = null;
      });
  }

  flush(): Promise<void> {
    return this.#queue;
  }

  async #append(chunk: string): Promise<void> {
    const bytes = Buffer.byteLength(chunk, "utf-8");
    if (this.#size === null) {
      await this.#fs.mkdir(dirname(this.#path), { recursive: true });
      try {
        this.#size = (await this.#fs.stat(this.#path)).size;
      } catch {
        this.#size = 0;
      }
    }
    if (this.#size > 0 && this.#size + bytes > this.#maxBytes) {
      await this.#rotate();
      this.#size = 0;
    }
    await this.#fs.appendFile(this.#path, chunk);
    this.#size += bytes;
  }

  async #rotate(): Promise<void> {
    const oldest = `${this.#path}.${this.#maxFiles - 1}`;
    await this.#fs.rm(oldest, { force: true });
    for (let i = this.#maxFiles - 2; i >= 1; i -= 1) {
      try {
        await this.#fs.rename(`${this.#path}.${i}`, `${this.#path}.${i + 1}`);
      } catch {
        // Missing intermediate file — fine.
      }
    }
    try {
      await this.#fs.rename(this.#path, `${this.#path}.1`);
    } catch {
      // Active file vanished under us — nothing to rotate.
    }
  }
}
