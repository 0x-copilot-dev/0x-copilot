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
  /**
   * Machine category for a refused grant root, when that is what failed. The
   * `message` is the sentence a consent card shows a person; this is the label
   * an audit row or a test asserts on, so improving the copy never silently
   * changes what a caller matched.
   */
  readonly reason: ForbiddenRootReason | undefined;
  constructor(
    code: FsErrorCode,
    message?: string,
    reason?: ForbiddenRootReason,
  ) {
    super(message ?? code);
    this.name = "FsError";
    this.code = code;
    this.reason = reason;
  }
}

/**
 * Resource ceilings enforced BEFORE (and while) doing work, so a single
 * request can never exhaust memory, file descriptors, or wall-clock time.
 * These bound both the read surface and the slice-3 write surface
 * (`maxWriteBytes`).
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

  /** write()/edit(): hard ceiling on a single mutation's content size. A
   * larger payload fails `too_large` before any temp file is created. */
  maxWriteBytes: 8 * 1024 * 1024, // 8 MiB

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

// ---------------------------------------------------------------------------
// SENSITIVE-PATH POLICY (G2). Two independent, NON-OVERRIDABLE denylists:
//
//   (a) SENSITIVE_ROOT — a grant may NOT be minted over the filesystem root,
//       the user's home directory (or any ancestor of it), the app's own
//       userData tree (which holds the encrypted grant store + auth secrets),
//       or any tree containing a well-known credential directory. Enforced at
//       grant creation (`GrantStore.create`) — the authoritative choke point,
//       so a caller that bypasses the native picker is still blocked.
//
//   (b) SENSITIVE_FILE — within an otherwise-granted folder, the CONTENTS of
//       well-known secret files (private keys, dotenv, credential stores) are
//       never readable, regardless of grant mode. Enforced in the read path
//       (`HostFs.read` / `HostFs.grep`) so neither a direct read nor a content
//       grep can exfiltrate them. Listing/stat still see the name (this is a
//       content-read policy, not an existence-hiding one).
//
// Both lists are plain, documented constants so they are trivially auditable
// and unit-testable. They only ever REDUCE authority; nothing here can widen
// what a grant already allows.
// ---------------------------------------------------------------------------

/**
 * Directory basenames that must never appear anywhere in a grant root's path.
 * Case-insensitive. These hold credentials/keys whose exposure is catastrophic;
 * a folder grant must not straddle any of them.
 */
export const SENSITIVE_ROOT_SEGMENTS: readonly string[] = [
  ".ssh", // OpenSSH private keys, known_hosts
  ".aws", // AWS access keys / config
  ".gnupg", // GnuPG keyrings & trust db
  ".gpg", // GnuPG (alternate)
  ".password-store", // pass(1) encrypted secret store
  ".docker", // Docker config.json (registry credentials)
  ".kube", // kubeconfig (cluster credentials)
  ".azure", // Azure CLI tokens
  "keychains", // macOS ~/Library/Keychains
];

// ---------------------------------------------------------------------------
// WHY THIS LIST GREW (the mid-run "always allow" lane).
//
// Until that lane, every grant root came from a native folder DIALOG: a person
// navigated to a folder and picked it, so the denylist only had to stop the
// handful of catastrophic choices a person might make by accident. The mid-run
// card changed the producer — the folder is now named by the MODEL, shown on a
// card, and attached with one click and no OS dialog. The question the list has
// to answer is therefore no longer "what might someone pick by mistake" but
// "what could a model name that a hurried person would approve", which is a
// much wider set and includes several folders that read as innocuous.
//
// Each list below is a CLASS, closed over the platforms we ship, not a sample
// of examples. Every one of them only ever REDUCES authority.
// ---------------------------------------------------------------------------

/**
 * Top-level trees owned by the operating system or its package manager: system
 * configuration, binaries, libraries, kernel interfaces, boot state, and the
 * superuser's home. Matched against the FIRST segment of the tree being judged
 * (for Windows, the first after the drive), so everything beneath goes with it —
 * `/etc/ssl/private`, `/usr/local/bin`, `C:\Windows\System32`. On a mounted
 * volume the tree being judged is each subtree of the volume in turn, so the
 * same names are refused there too (see `classifyForbiddenRoot`).
 *
 * `private` is here because macOS firmlinks `/etc`, `/var` and `/tmp` into it,
 * so `/private/etc` is the same directory as `/etc` and must classify the same
 * way. `tmp` is here because a world-writable directory is the cheapest place
 * for anything on the machine to plant a folder worth naming on a card.
 */
export const SYSTEM_ROOT_SEGMENTS: readonly string[] = [
  // POSIX
  "bin",
  "sbin",
  "usr",
  "etc",
  "var",
  "opt",
  "lib",
  "lib32",
  "lib64",
  "libexec",
  "boot",
  "proc",
  "sys",
  "dev",
  "run",
  "srv",
  "root",
  "cores",
  "private",
  "tmp",
  // macOS
  "system",
  "library",
  // Windows (after the drive root)
  "windows",
  "winnt",
  "program files",
  "program files (x86)",
  "programdata",
  "$recycle.bin",
  "system volume information",
  "perflogs",
  "recovery",
];

/**
 * Directories whose CHILDREN are whole mounted volumes. A grant on one of these
 * (`/Volumes`) covers every disk on the machine; a grant one level in
 * (`/Volumes/Backup`) covers an entire disk. Neither is a folder — "name the
 * folder you need" is the same refusal `HostPathMessages.VOLUME_ROOT` makes on
 * the backend, kept in step here.
 *
 * These also mark where THIS machine's namespace ends and a foreign one begins;
 * see `mountedVolumeContentStart`.
 */
export const VOLUME_PARENT_SEGMENTS: readonly string[] = [
  "volumes", // macOS
  "mnt", // Linux
  "media", // Linux (removable)
  "net", // autofs
  "network", // macOS
];

/**
 * What a machine calls the directory its user accounts live in.
 *
 * FOREIGN FILESYSTEMS ONLY. The `other_user_home` rule below derives the
 * container from `ctx.homeDir`, and on this machine that derivation is exact —
 * a hard-coded list could only add wrong answers to it, refusing `/home/…` on a
 * Mac and `/Users/…` on Linux. On a MOUNTED volume there is nothing to derive
 * from: the disk came off another machine, which may keep its accounts under
 * `home` (Linux) or `Documents and Settings` (old Windows) and is a sibling of
 * nothing this process knows. So the list names the class, and is consulted
 * only where the derivation has no answer.
 */
export const HOME_CONTAINER_SEGMENTS: readonly string[] = [
  "users", // macOS, Windows
  "home", // Linux, BSD
  "documents and settings", // Windows (legacy)
];

/**
 * Where installed applications keep their own state INSIDE a user's home — and
 * therefore where session cookies, OAuth refresh tokens and credential
 * databases live. Matched against the segment directly under the home
 * directory, which is the only place these conventions apply.
 *
 * Nothing else in this file refuses them: they are inside the home (so the
 * home rule allows them, correctly — the home rule is about breadth) and they
 * are not top-level (so the system rule never sees them). `~/Library/Application
 * Support/<some app>` is exactly the kind of specific, plausible-looking path a
 * model can name and a person will approve without reading past the app name.
 */
export const APPLICATION_STATE_SEGMENTS: readonly string[] = [
  "library", // macOS
  "appdata", // Windows
  ".config", // XDG
  ".local", // XDG
  ".cache", // XDG
];

/** Machine-readable reason a candidate grant root was rejected. */
export type ForbiddenRootReason =
  | "filesystem_root"
  | "volume_root"
  | "device_path"
  | "home_directory"
  | "other_user_home"
  | "user_data_directory"
  | "system_directory"
  | "application_bundle"
  | "application_state_directory"
  | "sensitive_directory"
  | "deceptive_path";

/**
 * What a person is told when a folder cannot be granted, one sentence per
 * reason. Declared as a total `Record` over the union so a new reason cannot be
 * added without copy — a refusal with no message is the silent no-op this
 * whole gate exists to avoid.
 *
 * None of these echoes the path. They name only the CATEGORY, which is derived
 * purely from the string the caller already supplied and from no filesystem
 * access at all, so a refusal still cannot become a path oracle.
 */
export const FORBIDDEN_ROOT_MESSAGES: Record<ForbiddenRootReason, string> = {
  filesystem_root: "The whole disk can't be shared. Name a folder on it.",
  volume_root: "A whole drive can't be shared. Name a folder on it.",
  device_path: "That path names a device, not a folder.",
  home_directory:
    "Your whole home folder can't be shared. Name a folder inside it.",
  other_user_home: "That folder belongs to another user account.",
  user_data_directory: "That folder holds this app's own data and keys.",
  system_directory: "That's a system folder, not one of yours.",
  application_bundle: "That's an installed application, not a folder of files.",
  application_state_directory:
    "That folder holds installed apps' saved logins and data.",
  sensitive_directory: "That folder holds credentials.",
  deceptive_path:
    "That folder's name contains hidden characters, so it can't be shown honestly.",
};

export interface GrantRootContext {
  /** The user's home directory (canonical). */
  readonly homeDir: string;
  /** The app's userData directory (holds the grant store + auth secrets). */
  readonly userDataDir: string;
}

function splitPathSegments(p: string): string[] {
  return p.split(/[/\\]+/u).filter((s) => s.length > 0);
}

// Canonical, comparison-friendly form: separator-normalized, trailing-slash
// stripped, lower-cased. Lower-casing is defense in depth against a bypass via
// case variation on the case-insensitive host filesystems (macOS/Windows);
// on a case-sensitive fs the over-rejection risk (two dirs differing only in
// case) is negligible for a security denylist.
function normalizeRootForCompare(p: string): string {
  return splitPathSegments(p).join("/").toLowerCase();
}

function isFilesystemRoot(rawRoot: string): boolean {
  // POSIX root (`/`, `//`) or a bare Windows drive root (`C:\`, `C:/`, `C:`).
  return /^[/\\]+$/u.test(rawRoot) || /^[A-Za-z]:[/\\]*$/u.test(rawRoot);
}

/** `\\.\PhysicalDrive0`, `\\?\Volume{…}` — a namespace, never a folder. */
function isDeviceNamespace(rawRoot: string): boolean {
  return /^\\\\[.?]\\/u.test(rawRoot);
}

/** `\\server\share` (and nothing below it) — a whole network volume. */
function isUncShareRoot(rawRoot: string): boolean {
  return /^\\\\[^\\/]+[\\/]+[^\\/]+[\\/]*$/u.test(rawRoot);
}

/**
 * Characters that change what a path LOOKS like without changing what it is:
 * C0/C1 controls, the soft hyphen, zero-width spaces and joiners, and the
 * explicit bidirectional overrides/isolates that can render `…/reports/txt.exe`
 * as something else entirely.
 *
 * This is the readability half of "what is displayed is exactly what is
 * granted". A path is granted BECAUSE a person read it on a card, so a string
 * whose rendering is not its content cannot be consented to at all.
 *
 * Deliberately NOT a homoglyph check. Refusing a segment that mixes Latin with
 * Cyrillic or Greek would reject `Проект-v2` — an ordinary folder name — and
 * doing it correctly needs a full confusables table. Full-script confusables
 * are out of scope here and are recorded as such rather than half-implemented.
 */
const DECEPTIVE_CHARACTERS =
  /[\u0000-\u001F\u007F-\u009F\u00AD\u061C\u180E\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;

function isDeceptivePath(rawRoot: string): boolean {
  if (DECEPTIVE_CHARACTERS.test(rawRoot)) return true;
  if (typeof rawRoot.isWellFormed === "function" && !rawRoot.isWellFormed()) {
    return true;
  }
  // A compatibility form that BECOMES a separator or a traversal once
  // normalized (U+FF0F FULLWIDTH SOLIDUS, U+FF0E FULLWIDTH FULL STOP) reads as
  // one folder and means another. Same rule `assertSegmentSafe` applies to a
  // virtual path segment, applied here to a grant root's own segments.
  return splitPathSegments(rawRoot).some((segment) => {
    const folded = segment.normalize("NFKC");
    return (
      folded !== segment &&
      (folded.includes("/") ||
        folded.includes("\\") ||
        folded === "." ||
        folded === "..")
    );
  });
}

/** The path's own top-level segment, skipping a Windows drive root. */
function topLevelSegment(segments: readonly string[]): string {
  const first = segments[0] ?? "";
  if (/^[a-z]:$/u.test(first)) return segments[1] ?? "";
  return first;
}

// True iff `descendant` is `ancestor` itself or lives strictly beneath it.
// Both must already be `normalizeRootForCompare`-normalized.
function isAncestorOrEqual(ancestor: string, descendant: string): boolean {
  if (ancestor === "") return true; // "" == filesystem root
  return descendant === ancestor || descendant.startsWith(`${ancestor}/`);
}

/**
 * Classify a candidate grant root, or return null when it is safe to grant.
 * Pure — takes the home/userData context explicitly so it stays testable.
 *
 * TWO PASSES, because a path has two meanings.
 *
 * The first pass reads the path as THIS machine spells it. That is the whole
 * answer for a path on this machine's own filesystem.
 *
 * The second exists because a mount point is not a folder: it is the root of an
 * entire other filesystem, which contains the very same classes under the very
 * same names. Every rule keyed on a first segment or on `ctx.homeDir` therefore
 * evaporated one level below `/Volumes/<disk>` — `/Volumes/Backup/etc`,
 * `/Volumes/Backup/System/Library`, `/Volumes/Backup/Users/bob/Documents` were
 * all granted, while their host-namespace spellings were refused. A grant on a
 * Time Machine disk could hand over a snapshot of the whole machine, this user's
 * home included, one indirection away from every rule written to stop exactly
 * that. So below a mount point the classes are re-tested MOUNT-RELATIVELY, and
 * a path lands on the same rule whichever way it is spelled.
 *
 * At EVERY depth, not just directly below the mount. A backup re-roots a
 * filesystem at a depth we cannot know
 * (`/Volumes/X/Backups.backupdb/<Mac>/<snapshot>/Users/bob`), a restored copy at
 * another, a disk image at another still, and none of it is decidable from the
 * string. Assuming any directory on a mounted volume could be a filesystem root
 * is the only assumption that closes the class rather than one spelling of it.
 * The price is paid on mounted volumes only, and it is over-REFUSAL: a folder
 * named `dev` or `lib` on an external disk is refused as if it were `/dev` or
 * `/lib`, and the person names a different folder. That is the deliberate
 * direction — the alternative is a denylist that a mount point walks around.
 */
export function classifyForbiddenRoot(
  root: string,
  ctx: GrantRootContext,
): ForbiddenRootReason | null {
  // First, because a string that does not render as itself cannot be judged —
  // by this function or by the person reading it on a card.
  if (isDeceptivePath(root)) return "deceptive_path";
  if (isDeviceNamespace(root)) return "device_path";
  if (isFilesystemRoot(root)) return "filesystem_root";
  if (isUncShareRoot(root)) return "volume_root";
  // `~` is not expanded anywhere in this process (the backend refuses it too,
  // `HostPathMessages.HOME_RELATIVE`). Reaching here it is either a literal
  // directory named `~` or an unexpanded home path; both are refused, and the
  // second is what the card would have shown the user.
  if (root.startsWith("~")) return "home_directory";

  const segments = splitPathSegments(root).map((s) => s.toLowerCase());

  // Pass 1 — the path in this machine's own namespace.
  const own = classifyResolvedTree(segments, ctx, { foreign: false });
  if (own !== null) return own;

  // Pass 2 — the same path read as content of a mounted volume.
  const start = mountedVolumeContentStart(root, segments, ctx);
  if (start === null) return null;
  for (let index = start; index < segments.length; index += 1) {
    const reason = classifyResolvedTree(segments.slice(index), ctx, {
      foreign: true,
    });
    if (reason !== null) return reason;
  }
  return null;
}

/**
 * Classify one already-lower-cased segment list AS A FILESYSTEM PATH — the tree
 * it names, judged by containment against the trees that may not be granted.
 *
 * Called twice by `classifyForbiddenRoot`: once on the path itself, and once per
 * subtree of a mounted volume, where the same segments name the same classes on
 * a different filesystem. Every rule here is therefore written to hold for both
 * callers; none of them may consult the original string.
 *
 * `foreign` says which caller this is. It gates exactly one rule — the one whose
 * answer `ctx` cannot supply for another machine's disk — and nothing else, so a
 * path on this machine keeps the classification it has always had.
 */
function classifyResolvedTree(
  segments: readonly string[],
  ctx: GrantRootContext,
  { foreign }: { readonly foreign: boolean },
): ForbiddenRootReason | null {
  const norm = segments.join("/");
  const home = normalizeRootForCompare(ctx.homeDir);
  const userData = normalizeRootForCompare(ctx.userDataDir);
  const insideHome = home !== "" && isAncestorOrEqual(home, norm);
  const top = topLevelSegment(segments);

  // The home directory itself, or any ancestor of it (`/Users`, `/home`, …):
  // far too broad, and an ancestor exposes every user.
  if (isAncestorOrEqual(norm, home)) return "home_directory";

  // The app's userData tree in EITHER direction: granting it, an ancestor of
  // it, or a folder inside it could expose the encrypted grant store and the
  // auth-token vault.
  if (isAncestorOrEqual(norm, userData) || isAncestorOrEqual(userData, norm)) {
    return "user_data_directory";
  }

  // Another account's home, or anything inside one: a sibling of this home
  // under the same parent. Derived from `homeDir` rather than from a list of
  // home-parent names, so it holds wherever accounts actually live. (The
  // parent itself — `/Users`, `/home` — is already refused just above as an
  // ancestor of this home.)
  const homeParent = parentOf(home);
  if (homeParent !== null && isAncestorOrEqual(homeParent, norm) && !insideHome)
    return "other_user_home";

  // A home container this process could NOT derive, and every account tree in
  // it. Only on a foreign filesystem: the rule above is exact for this machine
  // and a list can only add wrong answers to it, but a mounted disk carries
  // another machine's container, which may be spelled differently (`/home` on a
  // Linux disk read from a Mac, `Documents and Settings` on an old Windows
  // image). Still skipped inside this user's own home, so a snapshot of their
  // OWN home keeps the answers the derived rules gave it.
  if (foreign && !insideHome && HOME_CONTAINER_SEGMENTS.includes(top)) {
    return "other_user_home";
  }

  // A mount parent (`/Volumes`) or a whole volume one level in
  // (`/Volumes/Backup`). Deeper is an ordinary folder on an external disk and
  // is judged by the mount-relative pass instead.
  if (VOLUME_PARENT_SEGMENTS.includes(top) && depthBelowTop(segments) <= 1) {
    return "volume_root";
  }

  // An installed application: `/Applications`, and any `.app` bundle at any
  // depth (a user's own `~/Applications/Thing.app` is still an application).
  // Granting one shares executable code, not documents.
  if (top === "applications" || segments.some((s) => s.endsWith(".app"))) {
    return "application_bundle";
  }

  // An OS-owned tree — UNLESS it contains this user's home. A service or
  // container account can legitimately live at `/var/lib/<app>`, and refusing
  // every folder in that person's own home would leave them with no grantable
  // folder at all. No system tree is inside a home on the desktops we ship, so
  // outside that case this exemption never fires.
  if (!insideHome && SYSTEM_ROOT_SEGMENTS.includes(top)) {
    return "system_directory";
  }

  // Installed applications' saved state, directly under the home directory.
  if (insideHome) {
    const below = norm.slice(home.length + 1).split("/");
    const first = below[0] ?? "";
    if (first !== "" && APPLICATION_STATE_SEGMENTS.includes(first)) {
      return "application_state_directory";
    }
  }

  // Any well-known credential directory anywhere along the path.
  if (segments.some((s) => SENSITIVE_ROOT_SEGMENTS.includes(s))) {
    return "sensitive_directory";
  }
  return null;
}

/** A path's leading `c:` drive segment, lower-cased; null when it has none. */
function driveLetterOf(segments: readonly string[]): string | null {
  const first = segments[0] ?? "";
  return /^[a-z]:$/u.test(first) ? first : null;
}

/**
 * Index of the first segment that is CONTENT OF A MOUNTED VOLUME, or null when
 * the path is not on one. Everything from that index down belongs to a
 * filesystem this process did not create and cannot describe with `ctx`.
 *
 * Three shapes count as a mount:
 *   - `/Volumes/<disk>/…`, `/mnt/<disk>/…`, `/media/<disk>/…`, … — content
 *     starts two segments in (the mount parent, then the volume's own name).
 *   - `\\server\share\…` — a share is a volume on another machine; the share
 *     root itself was already refused as `volume_root`.
 *   - a drive root OTHER than the one the home directory is on. `D:\Users\bob`
 *     is the same tree as `/Volumes/Backup/Users/bob`, spelled for Windows.
 *
 * The home's OWN drive is deliberately excluded: it is this machine's
 * filesystem, already fully judged by the first pass, and re-scanning it would
 * refuse `C:\Users\alice\code\dev` for containing a segment named `dev`.
 */
function mountedVolumeContentStart(
  rawRoot: string,
  segments: readonly string[],
  ctx: GrantRootContext,
): number | null {
  // `\\server\share\…`, and the forward-slash spelling of the same path, which
  // Node also reads as UNC. Device namespaces (`\\.\`, `\\?\`) never reach here
  // — `classifyForbiddenRoot` refuses them before calling this. On POSIX a
  // leading `//` is just `/`, where this only skips two segments the first pass
  // already judged.
  if (/^[/\\]{2}[^/\\]/u.test(rawRoot)) return 2;

  const drive = driveLetterOf(segments);
  const homeDrive = driveLetterOf(
    splitPathSegments(ctx.homeDir).map((s) => s.toLowerCase()),
  );
  if (drive !== null && drive !== homeDrive) return 1;

  const offset = drive === null ? 0 : 1;
  if (VOLUME_PARENT_SEGMENTS.includes(segments[offset] ?? "")) {
    return offset + 2;
  }
  return null;
}

/** The containing directory of a normalized path; null at the top level. */
function parentOf(normalized: string): string | null {
  const cut = normalized.lastIndexOf("/");
  return cut <= 0 ? null : normalized.slice(0, cut);
}

/** How many segments sit below the top-level one (drive root not counted). */
function depthBelowTop(segments: readonly string[]): number {
  const first = segments[0] ?? "";
  const offset = /^[a-z]:$/u.test(first) ? 2 : 1;
  return Math.max(segments.length - offset, 0);
}

/**
 * Throw `FsError('permission_denied')` when `root` is not a safe folder to
 * grant. The message is the SENTENCE a consent card shows the person who asked
 * — never the offending host path, so a rejection cannot become a path oracle,
 * and never a bare category name, because a refusal nobody can read is the
 * silent no-op this gate exists to avoid. The machine category rides along on
 * `FsError.reason`.
 */
export function assertGrantableRoot(root: string, ctx: GrantRootContext): void {
  const reason = classifyForbiddenRoot(root, ctx);
  if (reason !== null) {
    throw new FsError(
      "permission_denied",
      FORBIDDEN_ROOT_MESSAGES[reason],
      reason,
    );
  }
}

/**
 * True iff any segment of an ALREADY-NORMALIZED virtual path names a well-known
 * credential directory (`.ssh`, `.aws`, `.gnupg`, macOS `keychains`, …). Reuses
 * the SAME `SENSITIVE_ROOT_SEGMENTS` list the grant-creation gate uses, so the
 * two controls can never drift.
 *
 * WHY THIS EXISTS (G2, read/write time). `assertGrantableRoot` blocks MINTING a
 * grant that straddles a credential directory, but a legitimately-granted folder
 * (say the user's whole project) can still *contain* a nested `.ssh` / `.aws` /
 * `.gnupg`. The per-file content denylist (`isSensitiveFileName`) only catches
 * known secret FILENAMES — it would still expose non-matching files inside such
 * a directory (`.aws/config`, `.ssh/known_hosts`, `.gnupg/*.gpg`, …). This check
 * denies traversal INTO the sensitive directory entirely, at read AND write
 * time, so the credential tree is unreachable regardless of the leaf name. It
 * only ever REDUCES authority.
 *
 * The argument is the segment list from `normalizeVirtualPath` (root-relative,
 * already proven free of `.`/`..`/separators), so a plain per-segment match is
 * sound.
 */
export function virtualPathTraversesSensitiveDir(
  segments: readonly string[],
): boolean {
  return segments.some((s) =>
    SENSITIVE_ROOT_SEGMENTS.includes(s.toLowerCase()),
  );
}

/** True iff a single (dir OR file) leaf name is a well-known credential dir. */
export function segmentIsSensitiveDir(name: string): boolean {
  return SENSITIVE_ROOT_SEGMENTS.includes(name.toLowerCase());
}

/**
 * Filename policy for the in-grant content-read denylist. Matched against a
 * file's LEAF NAME only (case-insensitive), so a secret file is unreadable at
 * any depth. Documented as data so it is auditable and unit-testable.
 */
export const SENSITIVE_FILE_RULES = {
  /** Suffixes denoting key / certificate / keystore material. */
  suffixes: [
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pkcs12",
    ".keystore",
    ".keychain",
    ".asc",
    ".ppk",
  ],
  /** Prefixes for conventional SSH private-key files. */
  prefixes: ["id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"],
  /** Exact credential-store filenames. */
  exact: ["credentials", ".netrc", ".pgpass", ".htpasswd", ".dockercfg"],
} as const;

/**
 * True iff a file with this leaf name holds secret material and its CONTENTS
 * must never be returned to the broker caller. Covers dotenv variants
 * (`.env`, `.env.local`, …), SSH private keys, PEM/PKCS keystores, and common
 * credential stores.
 */
export function isSensitiveFileName(name: string): boolean {
  const lower = name.toLowerCase();
  if (lower === ".env" || lower.startsWith(".env.")) return true;
  if (SENSITIVE_FILE_RULES.exact.some((e) => e === lower)) return true;
  if (SENSITIVE_FILE_RULES.prefixes.some((p) => lower.startsWith(p))) {
    return true;
  }
  if (SENSITIVE_FILE_RULES.suffixes.some((s) => lower.endsWith(s))) {
    return true;
  }
  return false;
}
