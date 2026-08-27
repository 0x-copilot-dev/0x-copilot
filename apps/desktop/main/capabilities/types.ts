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
 * Stable identity of the selected grant root.  The canonical path is only a
 * locator held in Electron main; writes additionally require this identity to
 * match at use time so a directory substitution cannot inherit a grant.
 */
export interface GrantRootIdentity {
  readonly volumeId: string;
  readonly fileId: string;
}

/**
 * Internal grant record — includes the canonical, realpath-resolved host
 * `root`. NEVER serialize this straight to the renderer; project through
 * `toRendererGrant` first.
 */
export interface Grant {
  readonly grantId: string;
  /** Canonical absolute directory (symlinks resolved via realpath). */
  readonly root: string;
  /**
   * Added by the v2 workspace authority.  Older grants without a captured
   * identity remain readable but are never eligible for a write permit.
   */
  readonly rootIdentity?: GrantRootIdentity;
  /** Local signed-in profile that selected the root. Required for writes. */
  readonly profileId?: string;
  /** Installation/device that owns this local grant. Required for writes. */
  readonly deviceId?: string;
  /** Root-relative capability subsets; empty means no subpath is writable. */
  readonly allowedPathPrefixes?: readonly string[];
  /** Epoch millis. Expired grants are treated as revoked for authority checks. */
  readonly expiresAt?: number;
  readonly mode: GrantMode;
  /**
   * Per-workspace SHELL EXECUTION enablement (PRD-shell-execution §7.3).
   *
   * OFF BY DEFAULT, and `false` is the ONLY value `GrantStore.create` ever
   * mints. It is a SEPARATE flag rather than a fourth `GrantMode` because the
   * three modes describe FILE ACCESS and are ordered (`MODE_RANK`); a fourth
   * member would imply `read_write < execute`, which is not true — running a
   * command is not more file access, it is a different kind of authority
   * entirely, and it is not a superset of anything (see PRD §7.3).
   *
   * REQUIRED, not optional, and that is the point. "Absent" is the common case
   * — every grant minted before this field existed lacks it — and an optional
   * boolean makes `undefined` a third state that each reader has to remember to
   * fold to `false`. Absence is folded ONCE, at the decode seam
   * (`coerceGrant`), so nothing downstream can be handed `undefined` and get it
   * wrong. The compiler then forces every construction site to say which it is.
   *
   * The one way this becomes `true` is `GrantStore.setShellEnabled`, reached
   * only from the Settings toggle over its own IPC channel. No runtime call, no
   * broker verb, and no attach-time parameter can set it — see
   * `CreateGrantInput`.
   */
  readonly shellEnabled: boolean;
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
  /**
   * Whether this workspace may run commands (§7.3). Carried to the renderer
   * because the Settings toggle has to render the CURRENT state of a security
   * flag; a toggle that shows what the renderer last asked for rather than what
   * main actually holds is the "pill that outlives its grant" defect wearing a
   * different hat. It is a boolean, not a path, so it cannot become a path
   * oracle — `RendererGrant` stays path-free, and `type PathFree<T>` still
   * guards that.
   */
  readonly shellEnabled: boolean;
}

export function toRendererGrant(grant: Grant): RendererGrant {
  return {
    grantId: grant.grantId,
    mode: grant.mode,
    label: grant.label,
    status: grant.status,
    // A grant that authorizes nothing advertises nothing. `status` here is the
    // EFFECTIVE one (`GrantStore.list` reports an expired grant as revoked), so
    // an expired workspace's toggle reads off rather than showing a live-looking
    // "commands allowed" over authority that has lapsed.
    shellEnabled: grant.status === "active" && grant.shellEnabled,
  };
}

/**
 * Broker-audience projection — the grant shape returned over the loopback
 * broker's grant-management routes (`/v1/grants/list`, `/v1/grants/snapshot`)
 * to the semi-trusted runtime worker. `mount` is an OPAQUE, per-boot,
 * non-reversible handle to the grant's virtual root, so the worker can tell
 * which grants share a physical tree without deriving that tree from the id.
 *
 * `root` — the deliberate reversal of G1, for the BROKER audience only
 * ---------------------------------------------------------------------
 * G1 originally withheld the host path here so a compromised worker could not
 * learn where a grant pointed. That stopped buying anything once host reads
 * moved to deepagents' `FilesystemBackend`: the worker now resolves and reads
 * real host paths itself, so it necessarily holds them. Withholding the root
 * only hid it from the component that needed it, and the cost was concrete —
 * the worker could not turn a granted folder into an `allow` rule, so every
 * read of a folder the user had EXPLICITLY ATTACHED still stopped and asked
 * again. Attaching a folder bought the user nothing.
 *
 * `RendererGrant` is UNCHANGED and stays path-free. The renderer never reads
 * files, has no use for a host path, and is the likelier exfiltration surface;
 * that projection is guarded at compile time by `type PathFree<T>` in
 * `WorkspaceGrantPort.test.ts`. The reversal is scoped to the audience that
 * performs the read.
 */
export interface BrokerGrant {
  readonly grantId: string;
  readonly mode: GrantMode;
  readonly label: string;
  readonly status: GrantStatus;
  /** Opaque per-boot virtual-root id. NEVER the host path. */
  readonly mount: string;
  /**
   * The grant's canonical host root — present ONLY while the grant is active.
   *
   * It crosses at all because the worker builds the filesystem `allow` rule
   * from it. A revoked or expired grant authorizes nothing, so its root buys
   * the worker nothing either; sending it would hand out the location of a
   * folder the user has explicitly detached, on a route ("list everything on
   * record") whose natural future caller is exactly the code that would turn
   * those rows into allow rules. Absent means the same thing an older broker
   * meant: no rule, so that folder keeps asking.
   */
  readonly root?: string;
  /**
   * Whether this workspace may run commands (§7.3) — present ONLY while the
   * grant is active, for the same reason `root` is.
   *
   * This is THE READ PATH. PRD §7.1 lists four independent prerequisites for
   * `run_command` to exist at all, and prerequisite 3 is this field, read off
   * the active-grant snapshot the worker already pins at run start. No new IPC
   * verb, no broker `ADVERTISED_METHODS` entry, and specifically no execution
   * verb over the broker: the broker reports what the user decided, it does not
   * run anything.
   *
   * ABSENT MEANS OFF. An older Electron main that does not send it decodes on
   * the Python side to `shell_enabled=False` (`BrokerGrant` there defaults it,
   * and the model is `extra="ignore"`), so a version skew degrades to "this
   * workspace cannot run commands" — never to "it can".
   */
  readonly shellEnabled?: boolean;
}

/**
 * The C2 host-session grant projection — deliberately NARROWER than
 * `BrokerGrant`, and the one place the root reversal must not reach.
 *
 * `/internal/workspace/v2/host-sessions` bootstraps the worker's private write
 * authority. The ai-backend asserts field-by-field that this response carries
 * no read capability, permit, prepared reference, device identity, root or
 * path (`broker_client._assert_host_session_wire_is_private`), and it fails the
 * whole session closed if one appears. Projecting these grants through
 * `toBrokerGrant` therefore did not just widen a contract — it broke the
 * channel, silently and only outside the tests, because that assertion is what
 * the real wire meets.
 *
 * `shellEnabled` is excluded for exactly that reason and is not an oversight.
 * `_assert_host_session_wire_is_private` allow-lists SIX grant keys by name
 * (`grantId`/`grant_id`, `mount`, `mode`, `label`, `status`) and raises
 * `BrokerProtocolError` on any seventh. Projecting the shell flag here would
 * fail every live host session closed while every test that builds the payload
 * by hand stayed green. The flag has no business here anyway: this bootstrap
 * carries WRITE authority for staged effects, and shell enablement is read off
 * `/v1/grants/snapshot` (see `BrokerGrant.shellEnabled`).
 */
export type HostSessionGrant = Omit<BrokerGrant, "root" | "shellEnabled">;

/** Path-free projection of a `GrantSnapshot` for the broker audience. */
export interface BrokerGrantSnapshot {
  readonly snapshotId: string;
  readonly capturedAt: number;
  readonly grants: readonly BrokerGrant[];
}

/**
 * Project an internal `Grant` to its broker-audience view. `mount` is supplied
 * by the broker (it owns the per-boot salt used to derive the opaque id).
 *
 * This function is the single place that decides WHICH fields cross to the
 * worker, which is why the G1 reversal is exactly one line here: `root` now
 * crosses, because the worker performs the read and cannot allow-list a folder
 * it cannot name. See `BrokerGrant` for why that is safe and what stayed
 * path-free. Keep this the only projection site — the renderer's equivalent
 * (`toRendererGrant`) must NOT gain a root.
 */
export function toBrokerGrant(grant: Grant, mount: string): BrokerGrant {
  const projected: BrokerGrant = {
    grantId: grant.grantId,
    mode: grant.mode,
    label: grant.label,
    status: grant.status,
    mount,
  };
  // Only a grant that still authorizes something carries its root. `status` is
  // already the effective one — `GrantStore.list` reports an expired grant as
  // revoked — so expiry is covered by the same test.
  return grant.status === "active"
    ? { ...projected, root: grant.root, shellEnabled: grant.shellEnabled }
    : projected;
}

/**
 * Project an internal `Grant` for the C2 host-session bootstrap: `BrokerGrant`
 * minus the root, always. Separate from `toBrokerGrant` because the audiences
 * differ — see `HostSessionGrant`.
 */
export function toHostSessionGrant(
  grant: Grant,
  mount: string,
): HostSessionGrant {
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

// ---------------------------------------------------------------------------
// SLICE 3 — filesystem WRITE operations contract (implemented in `host-fs.ts`,
// exposed over the authenticated loopback broker). Every result carries VIRTUAL
// (root-relative, POSIX) paths only — never a host absolute path. Writes are
// gated on grant MODE (see `MODE_RANK`): write/edit/mkdir need
// `read_write_no_delete`; delete/move need `read_write`. Every write goes
// through the SAME resolve-before-authorize + atomic-open validation pipeline
// as the reads, and file replacements are atomic (temp-in-same-dir → fsync →
// rename), so a mutation is all-or-nothing.
// ---------------------------------------------------------------------------

/** Result of `write` (create-or-overwrite a regular file). */
export interface HostWriteResult {
  /** Virtual (root-relative, POSIX) path written. */
  readonly path: string;
  readonly bytesWritten: number;
  /** True when the file did not previously exist (created), false on overwrite. */
  readonly created: boolean;
}

/** Result of `edit` (atomic full-content replacement of an EXISTING file). */
export interface HostEditResult {
  readonly path: string;
  readonly bytesWritten: number;
}

/** Result of `mkdir` (create a single directory whose parent already exists). */
export interface HostMkdirResult {
  readonly path: string;
  /** True when the directory was created, false when it already existed. */
  readonly created: boolean;
}

/** Result of `delete` (unlink a file or rmdir an EMPTY directory). */
export interface HostDeleteResult {
  readonly path: string;
  readonly type: "file" | "dir";
}

/** Result of `move`/`rename` within a single grant tree. */
export interface HostMoveResult {
  readonly from: string;
  readonly to: string;
  readonly type: "file" | "dir";
}
