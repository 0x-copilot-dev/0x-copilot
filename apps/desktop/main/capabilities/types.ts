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
// SLICE 2 (NOT built here) — filesystem operations contract.
//
// The next slice adds authenticated broker methods that actually touch the
// filesystem, each gated by (a) a resolved grant snapshot and (b) careful
// path validation (traversal / symlink / junction / ADS / TOCTOU) performed
// AT the broker, never in the renderer. The shape below is a placeholder to
// pin the interface direction — it is intentionally NOT implemented in this
// slice. Do not wire it up without the path-validation layer.
// ---------------------------------------------------------------------------
export interface HostFolderFsCapabilityTODO {
  // stat(grantId, relPath): Promise<HostStat>;
  // list(grantId, relPath): Promise<HostDirEntry[]>;
  // read(grantId, relPath, range?): Promise<Uint8Array>;
  // glob(grantId, pattern): Promise<string[]>;
  // grep(grantId, pattern, opts): Promise<HostGrepHit[]>;
  // write(grantId, relPath, bytes): Promise<void>;   // mode >= read_write_no_delete
  // mkdir(grantId, relPath): Promise<void>;           // mode >= read_write_no_delete
  // delete(grantId, relPath): Promise<void>;          // mode === read_write
  // move(grantId, fromRel, toRel): Promise<void>;      // mode === read_write
  readonly _todoSlice2?: never;
}
