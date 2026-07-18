import { constants, type Dir, type Dirent, type Stats } from "node:fs";
import {
  lstat as fsLstat,
  open as fsOpen,
  opendir as fsOpendir,
  realpath as fsRealpath,
  type FileHandle,
} from "node:fs/promises";
import { basename, join } from "node:path";

import {
  assertWithinRoot,
  FS_LIMITS,
  FsError,
  normalizeVirtualPath,
} from "./path-validation";
import type {
  HostDirEntry,
  HostGlobResult,
  HostGrepHit,
  HostGrepResult,
  HostListResult,
  HostReadResult,
  HostStatResult,
} from "./types";

// Filesystem READ operations for the capability broker (AC5 slice 2). Every
// method takes a grant's canonical `root` (resolved by the broker from the
// grant store — the renderer never supplies it) plus an untrusted *virtual*
// path or pattern, and refuses to touch anything that does not resolve to a
// location strictly inside that root. NO write/mkdir/delete/move — those are
// slice 3.
//
// PATH VALIDATION ALGORITHM (applied on every op, order is load-bearing):
//   1. Syntactic normalize (`normalizeVirtualPath`) — reject NUL, control
//      chars, absolute/drive/UNC roots, `..`, `.`-confusables, reserved device
//      names, `:`/ADS segments, trailing dot/space, lone surrogates, and
//      over-long / over-deep paths. No disk access.
//   2. Resolve-BEFORE-authorize — `realpath` the candidate so EVERY symlink is
//      collapsed first, then require the resolved real path to be contained by
//      the realpath'd root (`assertWithinRoot`). A symlink that leaves the root
//      is denied here, not after we have already read through it.
//   3. lstat gate — reject a final-component symlink and any non-regular /
//      non-directory type (fifo, socket, block/char device) before opening, so
//      an `open()` can never block on a device or FIFO.
//   4. Atomic open + revalidate (TOCTOU) — open the resolved real path by
//      handle with a symlink-refusing flag, then re-check identity and
//      containment against the handle. See `#openAtomic` for the per-platform
//      guarantee and the honestly-stated residual on Linux/Windows.
//   5. Operate on the HANDLE (fd-pinned), never re-deriving from the path, and
//      stop at the byte / entry / depth / result / duration ceilings.

/**
 * macOS `O_NOFOLLOW_ANY` (0x20000000, since 10.15). Unlike POSIX `O_NOFOLLOW`
 * — which only refuses a symlink as the FINAL component — this refuses a
 * symlink in ANY component and does so atomically inside the kernel path walk.
 * Because we always open the fully realpath-resolved (symlink-free) target,
 * a legitimate open never trips it; a mid-flight swap of ANY ancestor to a
 * symlink makes the open fail with ELOOP. That is our TOCTOU closure on darwin.
 */
const O_NOFOLLOW_ANY = 0x20000000;

export interface HostFsDeps {
  realpath(path: string): Promise<string>;
  lstat(path: string): Promise<Stats>;
  open(path: string, flags: number): Promise<FileHandle>;
  opendir(path: string): Promise<Dir>;
  now(): number;
  platform: NodeJS.Platform;
  /**
   * TEST-ONLY seam. Invoked after resolve+authorize+lstat-gate but immediately
   * BEFORE the atomic open, so an adversarial test can swap a path component
   * (e.g. replace a real directory with a symlink pointing outside the root)
   * and prove the use-time guard denies rather than escapes. NEVER set in
   * production — `defaultHostFsDeps()` leaves it undefined.
   */
  afterResolve?: (realTargetPath: string) => Promise<void>;
}

export function defaultHostFsDeps(): HostFsDeps {
  return {
    realpath: (p) => fsRealpath(p),
    lstat: (p) => fsLstat(p),
    open: (p, flags) => fsOpen(p, flags),
    opendir: (p) => fsOpendir(p),
    now: () => Date.now(),
    platform: process.platform,
  };
}

interface ResolvedTarget {
  readonly rootReal: string;
  readonly targetReal: string;
  readonly relPosix: string;
  readonly isDir: boolean;
  readonly isFile: boolean;
}

export interface ReadOptions {
  readonly offset?: number;
  readonly maxBytes?: number;
}

export interface GlobOptions {
  readonly maxResults?: number;
}

export interface GrepOptions {
  /** Restrict scanned files to those whose relative path matches this glob. */
  readonly pathGlob?: string;
  /** Treat `pattern` as a JS regular expression instead of a literal string. */
  readonly isRegex?: boolean;
  /** Regex flags (only when `isRegex`). `g`/`u` are added internally. */
  readonly flags?: string;
  readonly maxMatches?: number;
}

export class HostFs {
  readonly #deps: HostFsDeps;

  constructor(deps: HostFsDeps = defaultHostFsDeps()) {
    this.#deps = deps;
  }

  /** stat a file or directory under the grant root. */
  async stat(root: string, virtualPath: string): Promise<HostStatResult> {
    const target = await this.#resolve(root, virtualPath, "any");
    const { fh, fst } = await this.#openAtomic(target, target.isDir);
    try {
      return {
        type: fst.isDirectory() ? "dir" : "file",
        size: fst.size,
        mtimeMs: fst.mtimeMs,
        name: basename(target.targetReal),
      };
    } finally {
      await closeQuietly(fh);
    }
  }

  /** List the immediate children of a directory under the grant root. */
  async list(root: string, virtualPath: string): Promise<HostListResult> {
    const target = await this.#resolve(root, virtualPath, "dir");
    const { fh } = await this.#openAtomic(target, true);
    try {
      const dir = await this.#deps.opendir(target.targetReal);
      const entries: HostDirEntry[] = [];
      let truncated = false;
      try {
        let dent = await dir.read();
        while (dent !== null) {
          if (entries.length >= FS_LIMITS.maxDirEntries) {
            truncated = true;
            break;
          }
          entries.push({ name: dent.name, type: direntType(dent) });
          dent = await dir.read();
        }
      } finally {
        await dir.close().catch(() => {});
      }
      return { entries, truncated };
    } finally {
      await closeQuietly(fh);
    }
  }

  /** Read (a bounded window of) a regular file under the grant root. */
  async read(
    root: string,
    virtualPath: string,
    opts: ReadOptions = {},
  ): Promise<HostReadResult> {
    const target = await this.#resolve(root, virtualPath, "file");
    const { fh, fst } = await this.#openAtomic(target, false);
    try {
      const size = fst.size;
      const offset = clampInt(opts.offset ?? 0, 0, size);
      const requested = opts.maxBytes ?? FS_LIMITS.defaultReadBytes;
      if (!Number.isInteger(requested) || requested < 0) {
        throw new FsError(
          "invalid_request",
          "maxBytes must be a non-negative integer",
        );
      }
      const cap = Math.min(requested, FS_LIMITS.maxReadBytes);
      const remaining = Math.max(0, size - offset);
      const toRead = Math.min(cap, remaining);
      const buf = Buffer.allocUnsafe(toRead);
      let read = 0;
      while (read < toRead) {
        const { bytesRead } = await fh.read(
          buf,
          read,
          toRead - read,
          offset + read,
        );
        if (bytesRead === 0) break;
        read += bytesRead;
      }
      return {
        base64: buf.subarray(0, read).toString("base64"),
        size,
        offset,
        bytesRead: read,
        truncated: remaining > cap,
      };
    } finally {
      await closeQuietly(fh);
    }
  }

  /** Match a glob pattern against the file tree under the grant root. */
  async glob(
    root: string,
    pattern: string,
    opts: GlobOptions = {},
  ): Promise<HostGlobResult> {
    const rootReal = await this.#deps.realpath(root);
    const matcher = globToRegExp(pattern);
    const maxResults = clampInt(
      opts.maxResults ?? FS_LIMITS.maxGlobResults,
      1,
      FS_LIMITS.maxGlobResults,
    );
    const paths: string[] = [];
    let truncated = false;
    let scanned = 0;
    const deadline = this.#deps.now() + FS_LIMITS.walkDeadlineMs;

    const walkTruncated = await this.#walk(rootReal, deadline, (relPosix) => {
      scanned += 1;
      if (matcher.test(relPosix)) {
        if (paths.length >= maxResults) {
          truncated = true;
          return "stop";
        }
        paths.push(relPosix);
      }
      return "continue";
    });
    if (walkTruncated) truncated = true;

    return { paths, truncated, scanned };
  }

  /** Search file contents under the grant root for a pattern. */
  async grep(
    root: string,
    pattern: string,
    opts: GrepOptions = {},
  ): Promise<HostGrepResult> {
    const rootReal = await this.#deps.realpath(root);
    const matcher = compileContentMatcher(pattern, opts);
    const pathFilter =
      opts.pathGlob !== undefined ? globToRegExp(opts.pathGlob) : null;
    const maxMatches = clampInt(
      opts.maxMatches ?? FS_LIMITS.maxGrepMatches,
      1,
      FS_LIMITS.maxGrepMatches,
    );
    const hits: HostGrepHit[] = [];
    let truncated = false;
    let filesScanned = 0;
    const deadline = this.#deps.now() + FS_LIMITS.walkDeadlineMs;

    const candidates: Array<{ real: string; rel: string }> = [];
    const walkTruncated = await this.#walk(
      rootReal,
      deadline,
      (relPosix, realPath, isFile) => {
        if (!isFile) return "continue";
        if (pathFilter !== null && !pathFilter.test(relPosix))
          return "continue";
        candidates.push({ real: realPath, rel: relPosix });
        return "continue";
      },
    );
    if (walkTruncated) truncated = true;

    for (const cand of candidates) {
      if (this.#deps.now() > deadline || hits.length >= maxMatches) {
        truncated = true;
        break;
      }
      // Re-validate + open each file atomically (a walk-time swap of an
      // ancestor is caught here before we read any bytes).
      let handle: { fh: FileHandle; fst: Stats };
      try {
        handle = await this.#openAtomic(
          {
            rootReal,
            targetReal: cand.real,
            relPosix: cand.rel,
            isDir: false,
            isFile: true,
          },
          false,
        );
      } catch {
        continue; // vanished, swapped, or denied — skip this file
      }
      filesScanned += 1;
      try {
        if (handle.fst.size > FS_LIMITS.maxGrepFileBytes) continue;
        const buf = Buffer.allocUnsafe(handle.fst.size);
        let read = 0;
        while (read < buf.length) {
          const { bytesRead } = await handle.fh.read(
            buf,
            read,
            buf.length - read,
            read,
          );
          if (bytesRead === 0) break;
          read += bytesRead;
        }
        const text = buf.subarray(0, read).toString("utf-8");
        const lines = text.split("\n");
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i];
          if (line.length > FS_LIMITS.maxGrepLineBytes) continue;
          const col = matcher(line);
          if (col >= 0) {
            if (hits.length >= maxMatches) {
              truncated = true;
              break;
            }
            hits.push({
              path: cand.rel,
              line: i + 1,
              column: col + 1,
              preview: line.slice(0, FS_LIMITS.grepPreviewChars),
            });
          }
        }
      } finally {
        await closeQuietly(handle.fh);
      }
    }

    return { hits, truncated, filesScanned };
  }

  // --- internals ---

  /**
   * Bounded, symlink-refusing directory walk beneath `rootReal`. Calls
   * `visit(relPosix, realPath, isFile)` for every regular file and directory
   * (never a symlink — those are skipped, never followed, so the walk cannot
   * leave the root). Returns true if a ceiling (depth / entries / deadline /
   * visitor "stop") truncated the walk.
   */
  async #walk(
    rootReal: string,
    deadline: number,
    visit: (
      relPosix: string,
      realPath: string,
      isFile: boolean,
    ) => "continue" | "stop",
  ): Promise<boolean> {
    let scanned = 0;
    const stack: Array<{ real: string; rel: string; depth: number }> = [
      { real: rootReal, rel: "", depth: 0 },
    ];
    while (stack.length > 0) {
      const frame = stack.pop();
      if (frame === undefined) break;
      let dir: Dir;
      try {
        dir = await this.#deps.opendir(frame.real);
      } catch {
        continue; // directory vanished mid-walk
      }
      try {
        let dent = await dir.read();
        while (dent !== null) {
          if (
            this.#deps.now() > deadline ||
            scanned >= FS_LIMITS.maxWalkEntries
          ) {
            return true;
          }
          scanned += 1;
          const childRel =
            frame.rel === "" ? dent.name : `${frame.rel}/${dent.name}`;
          const childReal = join(frame.real, dent.name);
          if (dent.isSymbolicLink()) {
            // Never follow or match a symlink during a walk.
            dent = await dir.read();
            continue;
          }
          if (dent.isDirectory()) {
            if (visit(childRel, childReal, false) === "stop") return true;
            if (frame.depth + 1 <= FS_LIMITS.maxWalkDepth) {
              stack.push({
                real: childReal,
                rel: childRel,
                depth: frame.depth + 1,
              });
            }
          } else if (dent.isFile()) {
            if (visit(childRel, childReal, true) === "stop") return true;
          }
          dent = await dir.read();
        }
      } finally {
        await dir.close().catch(() => {});
      }
    }
    return false;
  }

  /**
   * Steps 1–3 of the algorithm: syntactic normalize, resolve-before-authorize,
   * and the lstat type gate. Returns the resolved real path (symlink-free,
   * proven inside the root) plus its kind. Throws `FsError` on any rejection.
   */
  async #resolve(
    root: string,
    virtualPath: string,
    expect: "file" | "dir" | "any",
  ): Promise<ResolvedTarget> {
    const segments = normalizeVirtualPath(virtualPath);
    const rootReal = await this.#deps.realpath(root);
    const candidate =
      segments.length === 0 ? rootReal : join(rootReal, ...segments);

    let targetReal: string;
    try {
      targetReal = await this.#deps.realpath(candidate);
    } catch (err) {
      if (errCode(err) === "ENOENT" || errCode(err) === "ENOTDIR") {
        throw new FsError("not_found", "path does not exist");
      }
      throw new FsError("permission_denied", "path could not be resolved");
    }

    // Symlinks are resolved above; authorize the REAL path.
    assertWithinRoot(rootReal, targetReal);

    const pre = await this.#deps.lstat(targetReal);
    if (pre.isSymbolicLink()) {
      // Defensive: realpath should have collapsed this. A symlink final
      // component is never read through.
      throw new FsError("permission_denied", "target is a symlink");
    }
    const isDir = pre.isDirectory();
    const isFile = pre.isFile();
    if (!isDir && !isFile) {
      throw new FsError("permission_denied", "unsupported file type");
    }
    if (expect === "dir" && !isDir) {
      throw new FsError("not_a_directory", "target is not a directory");
    }
    if (expect === "file" && !isFile) {
      throw new FsError("not_a_file", "target is not a regular file");
    }
    return {
      rootReal,
      targetReal,
      relPosix: segments.join("/"),
      isDir,
      isFile,
    };
  }

  /**
   * Step 4: open the resolved real path by handle with a symlink-refusing
   * flag, then revalidate identity and containment against the open handle.
   *
   * PER-PLATFORM GUARANTEE:
   *   - darwin: `O_NOFOLLOW_ANY` makes the kernel reject a symlink in ANY
   *     component atomically during the open path-walk. A mid-flight swap of
   *     any ancestor to a symlink → ELOOP → denied. TOCTOU is fully closed.
   *   - other: `O_NOFOLLOW` closes only the FINAL-component race atomically.
   *     Intermediate-component swaps are caught by the post-open recheck below
   *     (fstat-vs-lstat identity AND realpath-recheck containment), which is
   *     the conservative denial applied here — NOT atomic. Full closure needs
   *     `openat2(RESOLVE_BENEATH)` (Linux) via a native module, which this
   *     read-only slice deliberately does not add.
   */
  async #openAtomic(
    target: ResolvedTarget,
    asDirectory: boolean,
  ): Promise<{ fh: FileHandle; fst: Stats }> {
    // TEST-ONLY: allow an adversarial swap between resolve and use.
    if (this.#deps.afterResolve !== undefined) {
      await this.#deps.afterResolve(target.targetReal);
    }

    let fh: FileHandle;
    try {
      fh = await this.#deps.open(
        target.targetReal,
        openReadFlags(this.#deps.platform, asDirectory),
      );
    } catch (err) {
      throw mapOpenError(err, asDirectory);
    }

    try {
      const fst = await fh.stat();
      // Identity recheck: the handle's inode must still match the path's inode.
      const lst = await this.#deps.lstat(target.targetReal);
      if (fst.dev !== lst.dev || fst.ino !== lst.ino) {
        throw new FsError("permission_denied", "target changed after open");
      }
      // Containment recheck: re-resolve and require no drift and still-inside.
      const reReal = await this.#deps.realpath(target.targetReal);
      assertWithinRoot(target.rootReal, reReal);
      if (reReal !== target.targetReal) {
        throw new FsError(
          "permission_denied",
          "target path drifted after open",
        );
      }
      return { fh, fst };
    } catch (err) {
      await closeQuietly(fh);
      throw err;
    }
  }
}

function openReadFlags(
  platform: NodeJS.Platform,
  asDirectory: boolean,
): number {
  let flags = constants.O_RDONLY;
  if (asDirectory) flags |= constants.O_DIRECTORY;
  if (platform === "darwin") {
    flags |= O_NOFOLLOW_ANY;
  } else {
    // POSIX final-component guard; intermediates handled by the post-open
    // recheck. On Windows O_NOFOLLOW is a no-op (0) — the recheck carries it.
    flags |= constants.O_NOFOLLOW;
  }
  return flags;
}

function mapOpenError(err: unknown, asDirectory: boolean): FsError {
  const code = errCode(err);
  switch (code) {
    case "ELOOP":
    case "EMLINK":
      // O_NOFOLLOW / O_NOFOLLOW_ANY tripped a symlink — a TOCTOU swap or a
      // symlink component. Deny.
      return new FsError("permission_denied", "symlink in resolved path");
    case "ENOTDIR":
      return asDirectory
        ? new FsError("not_a_directory", "component is not a directory")
        : new FsError("permission_denied", "path component changed type");
    case "EISDIR":
      return new FsError("not_a_file", "target is a directory");
    case "ENOENT":
      return new FsError("not_found", "path does not exist");
    case "EACCES":
    case "EPERM":
      return new FsError("permission_denied", "access denied by OS");
    default:
      return new FsError("permission_denied", "open failed");
  }
}

function direntType(dent: Dirent): HostDirEntry["type"] {
  if (dent.isDirectory()) return "dir";
  if (dent.isFile()) return "file";
  if (dent.isSymbolicLink()) return "symlink";
  return "other";
}

async function closeQuietly(fh: FileHandle): Promise<void> {
  await fh.close().catch(() => {});
}

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  const v = Math.floor(value);
  if (v < min) return min;
  if (v > max) return max;
  return v;
}

function errCode(err: unknown): string | undefined {
  if (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    typeof (err as { code: unknown }).code === "string"
  ) {
    return (err as { code: string }).code;
  }
  return undefined;
}

function escapeRegExpChar(ch: string): string {
  return /[.*+?^${}()|[\]\\]/u.test(ch) ? `\\${ch}` : ch;
}

/**
 * Compile a glob into an anchored RegExp matched against a file's POSIX path
 * relative to the grant root. Supports `**` (any run of segments incl.
 * separators), `*` (any run within a segment), and `?` (one char within a
 * segment). Only emits `.*`, `[^/]*`, `[^/]`, and escaped literals — strictly
 * linear, so no catastrophic backtracking. Throws `FsError('invalid_request')`
 * on a malformed / traversing / over-long pattern.
 */
function globToRegExp(pattern: string): RegExp {
  if (typeof pattern !== "string" || pattern.length === 0) {
    throw new FsError("invalid_request", "empty glob pattern");
  }
  if (pattern.length > 1024 || pattern.includes("\u0000")) {
    throw new FsError("invalid_request", "invalid glob pattern");
  }
  if (/^[/\\]/u.test(pattern)) {
    throw new FsError("invalid_request", "glob must be relative");
  }
  for (const seg of pattern.split(/[/\\]+/u)) {
    if (seg === "..") {
      throw new FsError("invalid_request", "glob traversal not allowed");
    }
  }
  let re = "";
  for (let i = 0; i < pattern.length; i += 1) {
    const c = pattern[i];
    if (c === "*" && pattern[i + 1] === "*") {
      const after = pattern[i + 2];
      if (after === "/" || after === "\\") {
        // `**/` matches zero or more leading directory segments (including
        // none), so `**/x` also matches a top-level `x`.
        re += "(?:.*/)?";
        i += 2;
      } else {
        // A trailing / mid-segment `**` spans anything, separators included.
        re += ".*";
        i += 1;
      }
    } else if (c === "*") {
      re += "[^/]*";
    } else if (c === "?") {
      re += "[^/]";
    } else if (c === "\\") {
      // Treat backslash as a separator too (Windows-style patterns).
      re += "/";
    } else {
      re += escapeRegExpChar(c);
    }
  }
  return new RegExp(`^${re}$`, "u");
}

/**
 * Build a content matcher returning the 0-based column of the first match in a
 * line, or -1. Default is a literal (fixed-string) search — the ReDoS-safe
 * path. `isRegex` opts into a JS RegExp; per-line and per-file byte caps plus
 * the walk deadline bound the damage a pathological regex can do, but a single
 * catastrophic pattern on one long line is a residual we accept for the
 * semi-trusted worker caller (documented).
 */
function compileContentMatcher(
  pattern: string,
  opts: GrepOptions,
): (line: string) => number {
  if (typeof pattern !== "string" || pattern.length === 0) {
    throw new FsError("invalid_request", "empty grep pattern");
  }
  if (pattern.length > 4096 || pattern.includes("\u0000")) {
    throw new FsError("invalid_request", "invalid grep pattern");
  }
  if (opts.isRegex === true) {
    let flags = opts.flags ?? "";
    if (/[^gimsuy]/u.test(flags)) {
      throw new FsError("invalid_request", "invalid regex flags");
    }
    if (!flags.includes("u")) flags += "u";
    let re: RegExp;
    try {
      re = new RegExp(pattern, flags);
    } catch {
      throw new FsError("invalid_request", "invalid regex pattern");
    }
    return (line) => {
      re.lastIndex = 0;
      const m = re.exec(line);
      return m === null ? -1 : m.index;
    };
  }
  return (line) => line.indexOf(pattern);
}
