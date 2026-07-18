// Capability / host-folder grant domain types (AC5 slice 1).
//
// A "grant" is the user's explicit, revocable authority for the agent to
// touch ONE folder tree on the host machine at a given access level. This
// slice builds the grant model, the native picker, and the authenticated
// broker skeleton — NO filesystem operations yet (slice 2).
//
// SECURITY INVARIANT: the canonical host `root` path lives ONLY in the main
// process and, over the loopback broker, in the intended child process that
// holds the out-of-band broker token. It NEVER crosses the renderer IPC
// boundary. The renderer only ever sees `RendererGrant` (grantId + mode +
// label + status). See `toRendererGrant`.

/**
 * Access level a grant confers. Ordered least → most authority. Enforcement
 * of these modes against actual reads/writes lands in slice 2 (the FS-ops
 * broker methods); this slice only records the chosen mode on the grant.
 *
 * - `read_only`            — stat/list/read only.
 * - `read_write_no_delete` — read + create/modify, but no delete/unlink/move-out.
 * - `read_write`           — full read + write including delete.
 */
export type GrantMode = "read_only" | "read_write_no_delete" | "read_write";

export const GRANT_MODES: readonly GrantMode[] = [
  "read_only",
  "read_write_no_delete",
  "read_write",
];

export type GrantStatus = "active" | "revoked";

/**
 * Internal grant record — includes the canonical, realpath-resolved host
 * `root`. NEVER serialize this straight to the renderer; project through
 * `toRendererGrant` first.
 */
export interface Grant {
  readonly grantId: string;
  /** Canonical absolute directory (symlinks resolved via realpath). */
  readonly root: string;
  readonly mode: GrantMode;
  /** Sanitized display label (folder basename or a renderer-supplied hint). */
  readonly label: string;
  readonly status: GrantStatus;
  /** Epoch millis. */
  readonly createdAt: number;
  /** Epoch millis; bumped on revoke. */
  readonly updatedAt: number;
}

/**
 * Renderer-safe projection — the ONLY grant shape allowed across IPC. Carries
 * no host path and no broker token.
 */
export interface RendererGrant {
  readonly grantId: string;
  readonly mode: GrantMode;
  readonly label: string;
  readonly status: GrantStatus;
}

export function toRendererGrant(grant: Grant): RendererGrant {
  return {
    grantId: grant.grantId,
    mode: grant.mode,
    label: grant.label,
    status: grant.status,
  };
}

/**
 * Broker-audience projection — the grant shape returned over the loopback
 * broker's grant-management routes (`/v1/grants/list`, `/v1/grants/snapshot`)
 * to the semi-trusted runtime worker. Like `RendererGrant` it carries NO host
 * `root`; the worker keys every FS op off `grantId`, and `mount` is an OPAQUE,
 * per-boot, non-reversible handle to the grant's virtual root so the worker can
 * tell which grants share a physical tree WITHOUT ever learning that tree. The
 * canonical `root` stays main-side for internal FS resolution only (G1).
 */
export interface BrokerGrant {
  readonly grantId: string;
  readonly mode: GrantMode;
  readonly label: string;
  readonly status: GrantStatus;
  /** Opaque per-boot virtual-root id. NEVER the host path. */
  readonly mount: string;
}

/** Path-free projection of a `GrantSnapshot` for the broker audience. */
export interface BrokerGrantSnapshot {
  readonly snapshotId: string;
  readonly capturedAt: number;
  readonly grants: readonly BrokerGrant[];
}

/**
 * Project an internal `Grant` to its broker-audience view. `mount` is supplied
 * by the broker (it owns the per-boot salt used to derive the opaque id); this
 * function is the single place that decides WHICH fields cross to the worker —
 * and `root` is not one of them.
 */
export function toBrokerGrant(grant: Grant, mount: string): BrokerGrant {
  return {
    grantId: grant.grantId,
    mode: grant.mode,
    label: grant.label,
    status: grant.status,
    mount,
  };
}

/**
 * Immutable per-run snapshot of the active grants, pinned when a run starts
 * so that a revoke mid-run cannot retroactively widen or narrow what that run
 * already resolved. The broker hands one of these to an intended child.
 */
export interface GrantSnapshot {
  readonly snapshotId: string;
  readonly capturedAt: number;
  readonly grants: readonly Grant[];
}

/**
 * The read-side surface the broker needs. Decouples the broker from the
 * concrete `GrantStore` so tests can supply a fake provider.
 */
export interface GrantProvider {
  /** Every grant, active and revoked (full internal view, includes root). */
  listAll(): Promise<readonly Grant[]>;
  /** Immutable snapshot of only the active grants. */
  snapshotActive(): Promise<GrantSnapshot>;
}

// ---------------------------------------------------------------------------
// SLICE 2 — filesystem READ operations contract (implemented in `host-fs.ts`,
// exposed over the authenticated loopback broker for the runtime-worker
// audience only). Every result carries VIRTUAL paths (relative to the grant
// root, POSIX separators) — never a host absolute path — so nothing here can
// become a host-path oracle even for the token-holding worker.
//
// Reads only. write/mkdir/delete/move (mode >= read_write_no_delete) are
// slice 3 and intentionally absent.
// ---------------------------------------------------------------------------

/** Kind of a directory entry (symlinks are reported, never followed). */
export type HostEntryType = "file" | "dir" | "symlink" | "other";

/** Result of `stat` on a file or directory under a grant root. */
export interface HostStatResult {
  readonly type: "file" | "dir";
  readonly size: number;
  readonly mtimeMs: number;
  /** Leaf name only (never a full host path). */
  readonly name: string;
}

/** One child from a `list`. */
export interface HostDirEntry {
  readonly name: string;
  readonly type: HostEntryType;
}

export interface HostListResult {
  readonly entries: readonly HostDirEntry[];
  /** True when the entry ceiling stopped enumeration early. */
  readonly truncated: boolean;
}

/** Result of a bounded `read`. Bytes are base64 for JSON transport. */
export interface HostReadResult {
  readonly base64: string;
  /** Full size of the underlying file. */
  readonly size: number;
  readonly offset: number;
  readonly bytesRead: number;
  /** True when the file was larger than the byte cap from `offset`. */
  readonly truncated: boolean;
}

export interface HostGlobResult {
  /** Virtual (root-relative, POSIX) paths that matched. */
  readonly paths: readonly string[];
  readonly truncated: boolean;
  /** Entries inspected during the walk (for observability). */
  readonly scanned: number;
}

export interface HostGrepHit {
  /** Virtual (root-relative, POSIX) path of the matching file. */
  readonly path: string;
  /** 1-based line number. */
  readonly line: number;
  /** 1-based column of the first match on the line. */
  readonly column: number;
  /** Bounded snippet of the matching line. */
  readonly preview: string;
}

export interface HostGrepResult {
  readonly hits: readonly HostGrepHit[];
  readonly truncated: boolean;
  readonly filesScanned: number;
}
