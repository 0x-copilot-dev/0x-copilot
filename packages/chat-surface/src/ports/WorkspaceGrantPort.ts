// WorkspaceGrantPort — the substrate seam for granting the agent a real host folder.
//
// WHY THIS IS NOT `FilePickerPort`. The file picker is CONTENT UPLOAD: it hands
// back `name / size / type / stream()` and deliberately never exposes a path,
// because its product is bytes copied into one message. A folder grant is the
// other thing entirely — no bytes move, and what the user hands over is a
// durable capability the agent may read from later in this run and in runs after
// it, until revoked. Extending the picker would have meant either smuggling a
// path through an upload contract or losing the grant the moment the promise
// resolved. Different lifetime, different revocation story, different port.
//
// WHICH DIRECTION A PATH IS ALLOWED TO TRAVEL — the point of the whole port.
// `agent_runtime/capabilities/desktop/workspace_backend.py` exposes granted
// folders as `/workspace/<mount>/<relative>` and states the property it keeps:
// "Only mount names and root-relative virtual paths ever cross to the broker; a
// host-absolute path is never constructed or sent." That is a property of the
// READ path and it stays exactly as it is. A host-absolute path appears in ONE
// place: as the subject of a grant REQUEST — the folder the user is being asked
// to hand over, named so they can recognise it before they say yes. Approving
// turns it into a `grantId` + opaque `mount`, and every read after that is
// mount+relative as before. A path may travel toward consent; never toward bytes.
//
// The LISTING side is therefore path-free by construction. `WorkspaceGrant`
// mirrors the broker's own projection (`BrokerGrant`: `grantId` / `mount` /
// `label` / `mode`), where `mount` is an opaque per-boot id derived from the host
// root and `label` is the broker's sanitized display name. Surfaces in this
// package show the label and revoke by `grantId`; they are never handed a host
// path to render, so none of them can become a path oracle.
//
// WHO IMPLEMENTS IT: the desktop host, over IPC to the Electron capability
// broker, which owns the native folder picker, the OS confirmation, and the grant
// store. Web has no such capability and supplies NO port — which is why every
// consumer here takes the port as OPTIONAL and renders nothing when it is absent.
// A folder control on the web would be a control that cannot work.
//
// This file is types only: no browser primitive, no runtime code.

/**
 * Access a grant carries, in the broker's own vocabulary
 * (`workspace_backend.GrantMode`). Kept as the same three literals rather than a
 * friendlier local union so a host never has to translate — a translation table
 * is where "read_write_no_delete" quietly becomes "read/write".
 */
export type WorkspaceGrantMode =
  | "read_only"
  | "read_write_no_delete"
  | "read_write";

/**
 * One active grant, in the broker's path-free projection.
 *
 * `label` is what a surface prints (the broker's sanitized display name), and
 * `grantId` is what a revoke is keyed by. `mount` is presentation/debug only —
 * an opaque per-boot id, stable within a boot and non-reversible, so two grants
 * on one tree share a mount without either naming the tree.
 */
export interface WorkspaceGrant {
  readonly grantId: string;
  readonly mount: string;
  readonly label: string;
  readonly mode: WorkspaceGrantMode;
  /**
   * Whether the agent may RUN COMMANDS in this workspace
   * (PRD-shell-execution §7.3). Off for every workspace until a human turns it
   * on for that one folder, in Settings.
   *
   * It is NOT a fourth `WorkspaceGrantMode`. The three modes are ordered file
   * access and a fourth member would read as "more than read_write", which is
   * the wrong shape twice: running a command is a different kind of authority,
   * and it is not a superset of write (a read-only folder the user chooses to
   * run a build in is a coherent thing to want).
   *
   * REQUIRED so a host cannot ship a grant list whose command-capability is
   * `undefined`. A surface that has to fold `undefined` itself is a surface
   * that eventually folds it the wrong way, and the wrong way here reads as
   * "commands allowed" on a workspace nobody enabled.
   */
  readonly shellEnabled: boolean;
}

/**
 * What is being asked for.
 *
 * `path` is the reconciliation described in the header: absent means "let the
 * user choose" (the host opens its native folder picker, and the dialog IS the
 * consent), while a host-absolute path means "the agent asked for THIS folder"
 * (the mid-run ask — the host confirms that exact folder natively before minting
 * a grant). Either way the host, not this package, decides what is grantable.
 */
export interface WorkspaceGrantRequestInput {
  /** Host-absolute folder the ask names; absent/null → host picker. */
  readonly path?: string | null;
  /** Access being requested; absent → the host's own default (read-only). */
  readonly mode?: WorkspaceGrantMode;
  /** Why, in the model's words — the host may show it in its native confirm. */
  readonly reason?: string | null;
}

/**
 * The result of asking, with cancellation and failure kept apart.
 *
 * They are separate cases because collapsing them is the defect this subsystem
 * exists to prevent: a failure rendered as "nothing happened" reads to the user
 * as a decision they made. `failed` therefore carries a message the surface is
 * expected to SHOW.
 */
export type WorkspaceGrantOutcome =
  | { readonly status: "granted"; readonly grant: WorkspaceGrant }
  | { readonly status: "cancelled" }
  | { readonly status: "failed"; readonly message: string };

/** Same split, for taking access away — see {@link WorkspaceGrantOutcome}. */
export type WorkspaceRevokeOutcome =
  | { readonly status: "revoked" }
  | { readonly status: "failed"; readonly message: string };

/**
 * The result of asking to change one workspace's command permission.
 *
 * `applied` carries what the host ACTUALLY holds now, not what was asked for.
 * The host may honour a request to turn commands OFF on a grant it will not
 * honour a request to turn them ON for (a revoked or expired workspace), and a
 * toggle that reported its own optimism as success is how a control ends up
 * claiming an authority the machine never recorded. A caller compares `applied`
 * against what it asked and says so when they differ.
 */
export type WorkspaceShellAccessOutcome =
  | { readonly status: "ok"; readonly applied: boolean }
  | { readonly status: "failed"; readonly message: string };

export interface WorkspaceGrantPort {
  /**
   * Ask the user for a folder. Resolves `granted` with the minted grant,
   * `cancelled` when they dismissed the host's picker/confirm, or `failed` with
   * a message when the grant could not be created.
   */
  requestGrant(
    input?: WorkspaceGrantRequestInput,
  ): Promise<WorkspaceGrantOutcome>;

  /**
   * The CURRENT active grant set (the broker excludes revoked grants), so a
   * surface renders what access actually exists rather than what it last saw
   * itself hand out.
   */
  listGrants(): Promise<ReadonlyArray<WorkspaceGrant>>;

  /** Take one grant away. The agent loses that mount on its next read. */
  revokeGrant(grantId: string): Promise<WorkspaceRevokeOutcome>;

  /**
   * Turn command execution on or off for ONE workspace (§7.3).
   *
   * OPTIONAL, and its absence is the whole gate on the control: a host that has
   * not wired shell execution supplies no implementation, and every surface in
   * this package renders no toggle rather than a toggle that cannot work — the
   * same contract that keeps the folder bar off the web. Web will never
   * implement it; there is no shell in a browser tab.
   *
   * It states a VALUE rather than flipping one. "Toggle" makes the outcome
   * depend on state the caller does not hold, so a retry after a dropped reply,
   * or two clicks racing, can land on "on" when the user's last act was "off".
   */
  setShellEnabled?(
    grantId: string,
    enabled: boolean,
  ): Promise<WorkspaceShellAccessOutcome>;
}
