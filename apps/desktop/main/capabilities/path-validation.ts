// Host-path validation for the capability broker's filesystem read ops
// (AC5 slice 2). This module is PURE — no filesystem access, no Electron — so
// the syntactic layer is exhaustively unit-testable in isolation. The
// symlink / TOCTOU layer that DOES touch the disk lives in `host-fs.ts` and
// leans on the primitives here.
//
// THREAT MODEL. The caller is the semi-trusted runtime-worker child (it holds
// the out-of-band broker token). It may be buggy or actively hostile and it
// may race us on the filesystem. Every request names a `grant_id` plus a
// *virtual* path that must resolve to a location strictly inside that grant's
// canonical root. The renderer never reaches this surface at all.
//
// Two independent gates, in this order (never reordered):
//   1. SYNTAX (here): normalize the virtual path and reject anything that is
//      not a plain relative path of ordinary name segments — NUL, control
//      chars, absolute/drive/UNC roots, `..`, `.`-confusables, Windows
//      reserved device names, alternate-data-stream `:` segments, trailing
//      dot/space, lone surrogates, over-long/over-deep paths.
//   2. AUTHORIZATION (`host-fs.ts`): realpath the candidate so EVERY symlink
//      is resolved BEFORE we decide, then require the resolved real path to be
//      contained by the realpath'd grant root. Resolve-before-authorize is the
//      rule — we never authorize a lexical path and follow links afterwards.

import { isAbsolute, relative, sep } from "node:path";

/**
 * Stable machine-readable failure codes. NONE of these ever carry a host path
 * in the accompanying message — a validation failure must not become a path
 * oracle for the caller.
 */
export type FsErrorCode =
  | "invalid_path" // syntactic rejection (traversal, reserved, encoding, …)
  | "invalid_request" // malformed op params (bad pattern, bad range, …)
  | "grant_required" // unknown or revoked grant
  | "permission_denied" // resolved outside the root, symlink/TOCTOU escape, or insufficient mode
  | "not_found" // path does not exist under the root
  | "not_a_directory" // list/glob/grep target is not a directory
  | "not_a_file" // read target is not a regular file
  | "too_large" // read target exceeds the hard byte ceiling
  | "unsupported"; // op not enabled / not implemented

/**
 * Error raised by every validation and filesystem-op failure. The `message`
 * is intentionally generic and MUST NOT include the offending host path; the
 * machine `code` is the contract the broker maps to an HTTP status.
 */
export class FsError extends Error {
  readonly code: FsErrorCode;
  constructor(code: FsErrorCode, message?: string) {
    super(message ?? code);
    this.name = "FsError";
    this.code = code;
  }
}

/**
 * Resource ceilings enforced BEFORE (and while) doing work, so a single
 * request can never exhaust memory, file descriptors, or wall-clock time.
 * These bound the read surface; write ceilings arrive with slice 3.
 */
export const FS_LIMITS = {
  /** Max segments in a virtual path (depth). */
  maxPathDepth: 64,
  /** Max bytes in a single normalized path segment (POSIX NAME_MAX-ish). */
  maxSegmentBytes: 255,
  /** Max bytes in the whole virtual path. */
  maxPathBytes: 4096,

  /** read(): default cap when the caller does not ask for a smaller window. */
  defaultReadBytes: 1024 * 1024, // 1 MiB
  /** read(): hard ceiling; a caller cannot request more than this per call. */
  maxReadBytes: 8 * 1024 * 1024, // 8 MiB

  /** list(): max directory entries returned before truncation. */
  maxDirEntries: 10_000,

  /** glob()/grep(): max directory tree depth walked below the root. */
  maxWalkDepth: 32,
  /** glob()/grep(): max filesystem entries inspected across the whole walk. */
  maxWalkEntries: 200_000,
  /** glob(): max matched paths returned before truncation. */
  maxGlobResults: 5_000,
  /** glob()/grep(): wall-clock budget for one call. */
  walkDeadlineMs: 5_000,

  /** grep(): files larger than this are skipped (not scanned). */
  maxGrepFileBytes: 4 * 1024 * 1024,
  /** grep(): lines longer than this are skipped (ReDoS / memory guard). */
  maxGrepLineBytes: 64 * 1024,
  /** grep(): max hits returned before truncation. */
  maxGrepMatches: 5_000,
  /** grep(): preview text length per hit. */
  grepPreviewChars: 240,
} as const;

// Windows reserved device basenames. A name is reserved if its portion before
// the first `.` matches (case-insensitively) — `CON`, `NUL.txt`, `COM3.log`
// are all reserved on Windows and confusably dangerous everywhere.
const WINDOWS_RESERVED = new Set([
  "con",
  "prn",
  "aux",
  "nul",
  "com1",
  "com2",
  "com3",
  "com4",
  "com5",
  "com6",
  "com7",
  "com8",
  "com9",
  "lpt1",
  "lpt2",
  "lpt3",
  "lpt4",
  "lpt5",
  "lpt6",
  "lpt7",
  "lpt8",
  "lpt9",
]);

function hasControlChar(s: string): boolean {
  for (const ch of s) {
    const code = ch.codePointAt(0) ?? 0;
    if (code < 0x20 || (code >= 0x7f && code <= 0x9f)) return true;
  }
  return false;
}

function isReservedDeviceName(segment: string): boolean {
  const base = segment.split(".")[0]?.toLowerCase() ?? "";
  return WINDOWS_RESERVED.has(base);
}

/**
 * Reject a single already-split segment or throw `FsError('invalid_path')`.
 * Applied to the raw segment AND to its NFKC form so a Unicode-confusable
 * separator (e.g. U+FF0F FULLWIDTH SOLIDUS) or dot (U+FF0E) cannot smuggle a
 * `/` or `..` past us.
 */
function assertSegmentSafe(segment: string): void {
  if (segment.length === 0) {
    // Empty segment (a `//` or leading/trailing separator) — reject rather
    // than silently collapse, so intent stays explicit.
    throw new FsError("invalid_path", "empty path segment");
  }
  if (Buffer.byteLength(segment, "utf-8") > FS_LIMITS.maxSegmentBytes) {
    throw new FsError("invalid_path", "path segment too long");
  }
  // Lone surrogates / malformed UTF-16 — reject bad encodings outright.
  if (typeof segment.isWellFormed === "function" && !segment.isWellFormed()) {
    throw new FsError("invalid_path", "path segment is not well-formed");
  }
  for (const candidate of [segment, segment.normalize("NFKC")]) {
    if (candidate === "." || candidate === "..") {
      throw new FsError("invalid_path", "path traversal segment");
    }
    if (candidate.includes("/") || candidate.includes("\\")) {
      throw new FsError("invalid_path", "separator inside path segment");
    }
    if (candidate.includes(":")) {
      // Alternate data stream (`file.txt:stream`) or a drive-ish `C:` segment.
      throw new FsError("invalid_path", "colon in path segment");
    }
    if (hasControlChar(candidate)) {
      throw new FsError("invalid_path", "control character in path");
    }
    if (isReservedDeviceName(candidate)) {
      throw new FsError("invalid_path", "reserved device name");
    }
    // Windows silently strips a trailing dot or space, so `secret.` and
    // `secret ` alias `secret` — reject the confusable form.
    if (/[ .]$/u.test(candidate)) {
      throw new FsError("invalid_path", "trailing dot or space in segment");
    }
  }
}

/**
 * Parse an untrusted virtual path into clean, ordinary name segments, or throw
 * `FsError('invalid_path')`. NO filesystem access. An empty / `.` / `/` input
 * denotes the grant root itself and yields `[]`.
 *
 * Accepts both `/` and `\` as separators (so a Windows-style path is validated
 * on POSIX too) and rejects any absolute, drive-letter, or UNC root: the path
 * is ALWAYS interpreted relative to the grant root, never the filesystem root.
 */
export function normalizeVirtualPath(raw: unknown): string[] {
  if (typeof raw !== "string") {
    throw new FsError("invalid_path", "path must be a string");
  }
  if (raw.includes("\u0000")) {
    throw new FsError("invalid_path", "NUL in path");
  }
  if (Buffer.byteLength(raw, "utf-8") > FS_LIMITS.maxPathBytes) {
    throw new FsError("invalid_path", "path too long");
  }
  // Absolute (POSIX `/…`), Windows drive (`C:\…` / `C:/…`), and UNC (`\\host`)
  // roots all escape the "relative to the grant root" contract.
  if (/^[/\\]/u.test(raw)) {
    throw new FsError("invalid_path", "absolute path not allowed");
  }
  if (/^[A-Za-z]:/u.test(raw)) {
    throw new FsError("invalid_path", "drive-letter path not allowed");
  }

  const segments: string[] = [];
  for (const part of raw.split(/[/\\]+/u)) {
    if (part === "") {
      // Trailing separator or run collapsed by the split — skip leading/
      // trailing empties, but an interior empty cannot occur because the split
      // is greedy. A bare "" input already returned [] via the checks below.
      continue;
    }
    // A single "." is a harmless no-op segment; drop it. ".." and confusable
    // dots are caught inside assertSegmentSafe.
    if (part === ".") continue;
    assertSegmentSafe(part);
    segments.push(part);
  }
  if (segments.length > FS_LIMITS.maxPathDepth) {
    throw new FsError("invalid_path", "path too deep");
  }
  return segments;
}

/**
 * Containment test used AFTER symlink resolution: `child` must equal `root` or
 * live strictly beneath it. Both arguments MUST already be realpath-resolved
 * (canonical, symlink-free, canonical-case) so that a plain string comparison
 * is sound and `/root-evil` is never mistaken for a child of `/root`.
 *
 * Throws `FsError('permission_denied')` on escape.
 */
export function assertWithinRoot(root: string, child: string): void {
  if (child === root) return;
  const rel = relative(root, child);
  if (
    rel === "" ||
    rel === ".." ||
    rel.startsWith(`..${sep}`) ||
    isAbsolute(rel)
  ) {
    throw new FsError("permission_denied", "path escapes the grant root");
  }
}

/**
 * Ordering of grant modes, least → most authority. Used to gate ops: a read op
 * requires `read_only`; slice-3 writes will require higher. Fail closed — an
 * unknown mode never satisfies anything.
 */
const MODE_RANK: Record<string, number> = {
  read_only: 0,
  read_write_no_delete: 1,
  read_write: 2,
};

/** True iff a grant of `granted` mode satisfies an op needing `required`. */
export function modeSatisfies(required: string, granted: string): boolean {
  const need = MODE_RANK[required];
  const have = MODE_RANK[granted];
  if (need === undefined || have === undefined) return false;
  return have >= need;
}
