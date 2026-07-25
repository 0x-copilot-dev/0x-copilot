import {
  createHmac,
  randomBytes as nodeRandomBytes,
  timingSafeEqual,
} from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

import { HostFs } from "./host-fs";
import {
  FS_LIMITS,
  FsError,
  modeSatisfies,
  type FsErrorCode,
} from "./path-validation";
import { RunContextStore } from "./run-context";
import {
  toBrokerGrant,
  type Grant,
  type GrantMode,
  type GrantProvider,
} from "./types";
import {
  LocalWorkspaceAuthority,
  WorkspaceAuthorityError,
  type WorkspaceChangeEntry,
  type WorkspaceChangeSet,
  type WorkspaceCommitResult,
  type WorkspaceRunFacts,
} from "./workspace-authority";
import type { WorkspaceApprovalPermitHandoff } from "./workspace-approval";

// Authenticated loopback capability broker (AC5 slice 1 — skeleton).
//
// A 127.0.0.1-only HTTP listener on an OS-assigned ephemeral port. Every
// request must carry:
//   - `Authorization: Bearer <token>` where <token> is a per-boot 256-bit
//     CSPRNG secret (constant-time compared),
//   - `X-Capability-Protocol: <PROTOCOL_VERSION>`,
//   - a POST + JSON body under the size cap,
//   - and NO browser fetch metadata (`Origin` / `Sec-Fetch-*`). There is no
//     CORS: a browser/renderer context must never reach this surface.
//
// The broker URL is NON-secret config; the token is delivered OUT OF BAND to
// the intended child process (never over renderer IPC, never logged). A
// restart (`stop()` then `start()`) mints a fresh token, invalidating any
// previously issued one.
//
// SLICE 1 exposed ONLY grant-management reads (handshake / list / snapshot).
// SLICE 2 added the filesystem READ ops (stat/list/read/glob/grep).
// SLICE 3 (this change) adds the filesystem WRITE ops (write/edit/mkdir/delete/
// move) and the per-run grant snapshot (runs/begin + runs/end). Each FS op
// resolves its `grant_id` — against the CURRENT active snapshot, OR, when the
// request carries a `run_capability_context`, against the immutable snapshot
// PINNED when that run started — then gates on the required grant MODE for the
// route (reads: read_only; write/edit/mkdir: read_write_no_delete; delete/move:
// read_write; fail-closed for an unknown mode) and runs `HostFs`, whose
// path-validation layer performs the traversal / symlink / junction / ADS /
// TOCTOU checks. Host absolute paths NEVER appear in any response body —
// results carry only root-relative virtual paths.
//
// G1: the grant-management routes are ALSO path-free. `grants/list` and
// `grants/snapshot` return a `BrokerGrant` projection (grantId + mode + label
// + status + an OPAQUE per-boot `mount` id) — the canonical host `root` is kept
// main-side for internal FS resolution and never crosses to the worker. The
// `mount` id is an HMAC of the root under a per-boot random salt, so it is
// stable within a boot (two grants on one tree share a mount) yet reveals
// nothing about the path and is not a brute-force oracle across boots.

export const CAPABILITY_BROKER_PROTOCOL = "1";

const TOKEN_BYTES = 32; // 256-bit
const MAX_BODY_BYTES = 64 * 1024;
// Write routes carry file content in the request body, so they get a larger
// cap sized to hold `FS_LIMITS.maxWriteBytes` (8 MiB) as base64 plus JSON
// overhead. Every OTHER route keeps the tight 64 KiB cap.
const MAX_WRITE_BODY_BYTES = 12 * 1024 * 1024;
const MAX_WORKSPACE_UPLOAD_BYTES = 128 * 1024 * 1024;

const ROUTES = {
  handshake: "/v1/handshake",
  grantsList: "/v1/grants/list",
  grantsSnapshot: "/v1/grants/snapshot",
  runsBegin: "/v1/runs/begin",
  runsEnd: "/v1/runs/end",
  fsStat: "/v1/fs/stat",
  fsList: "/v1/fs/list",
  fsRead: "/v1/fs/read",
  fsGlob: "/v1/fs/glob",
  fsGrep: "/v1/fs/grep",
  fsWrite: "/v1/fs/write",
  fsEdit: "/v1/fs/edit",
  fsMkdir: "/v1/fs/mkdir",
  fsDelete: "/v1/fs/delete",
  fsMove: "/v1/fs/move",
  workspacePrepare: "/internal/workspace/v2/prepare",
  workspaceHostSessions: "/internal/workspace/v2/host-sessions",
  workspaceCommit: "/internal/workspace/v2/prepared",
  workspaceClaims: "/internal/workspace/v2/claims",
} as const;

// Routes whose request body may carry file content (larger body cap).
const WRITE_BODY_ROUTES: ReadonlySet<string> = new Set([
  ROUTES.fsWrite,
  ROUTES.fsEdit,
]);

// The capability methods this broker advertises to a handshaking child.
const ADVERTISED_METHODS = [
  "listGrants",
  "snapshotGrants",
  "beginRun",
  "endRun",
  "statPath",
  "listDir",
  "readFile",
  "glob",
  "grep",
  "writeFile",
  "editFile",
  "makeDir",
  "deletePath",
  "movePath",
  "prepareWorkspaceEffect",
  "uploadWorkspaceContent",
  "commitWorkspaceEffect",
  "reconcileWorkspaceClaim",
  "abortWorkspaceEffect",
] as const;

const LEGACY_FILESYSTEM_METHODS = new Set<string>([
  "statPath",
  "listDir",
  "readFile",
  "glob",
  "grep",
  "writeFile",
  "editFile",
  "makeDir",
  "deletePath",
  "movePath",
]);

const WORKSPACE_V2_METHODS = new Set<string>([
  "prepareWorkspaceEffect",
  "uploadWorkspaceContent",
  "commitWorkspaceEffect",
  "reconcileWorkspaceClaim",
  "abortWorkspaceEffect",
]);

// The minimum grant MODE each FS route requires. Fail-closed: an unknown route
// defaults to the highest bar (`read_write`), and `modeSatisfies` fails closed
// for an unknown grant mode. Reads need only `read_only`; a mutation that only
// creates/modifies needs `read_write_no_delete`; a mutation that can REMOVE a
// path (delete, or move which renames the source away) needs `read_write`.
const FS_ROUTE_REQUIRED_MODE: Record<string, GrantMode> = {
  [ROUTES.fsStat]: "read_only",
  [ROUTES.fsList]: "read_only",
  [ROUTES.fsRead]: "read_only",
  [ROUTES.fsGlob]: "read_only",
  [ROUTES.fsGrep]: "read_only",
  [ROUTES.fsWrite]: "read_write_no_delete",
  [ROUTES.fsEdit]: "read_write_no_delete",
  [ROUTES.fsMkdir]: "read_write_no_delete",
  [ROUTES.fsDelete]: "read_write",
  [ROUTES.fsMove]: "read_write",
};

export interface CapabilityBrokerConfig {
  readonly grants: GrantProvider;
  /**
   * Host filesystem read executor. When omitted the FS routes fail closed
   * (`unsupported`) — the broker never touches the disk without it.
   */
  readonly hostFs?: HostFs;
  /** Injectable CSPRNG for tests. Defaults to node:crypto randomBytes. */
  readonly randomBytes?: (size: number) => Buffer;
  /**
   * In-memory store of per-run grant snapshots. Injectable for tests; defaults
   * to a fresh RAM-only store keyed by the same CSPRNG as the token.
   */
  readonly runContexts?: RunContextStore;
  /**
   * The v2 authority. Its presence withdraws every legacy filesystem endpoint;
   * this is intentionally distinct from the boot bearer, which carries no
   * filesystem right.
   */
  readonly workspaceAuthority?: LocalWorkspaceAuthority;
}

export interface CapabilityBrokerHandle {
  /** `http://127.0.0.1:<port>` — non-secret; safe to pass as plain config. */
  readonly baseUrl: string;
  readonly port: number;
}

/**
 * Main-owned session state.  Its read capability and device identity never
 * leave Electron main; the child process sees only the opaque map key.
 */
interface WorkspaceHostSessionState {
  readonly facts: WorkspaceRunFacts;
  readonly readCapability: string;
  readonly grantIds: readonly string[];
  readonly expiresAt: number;
}

interface CompletedWorkspaceCommit {
  readonly hostSessionRef: string;
  readonly result: WorkspaceCommitResult;
}

const HOST_SESSION_REF = /^whs_[A-Za-z0-9_-]{32,160}$/u;

export class CapabilityBroker {
  readonly #grants: GrantProvider;
  readonly #hostFs: HostFs | null;
  readonly #randomBytes: (size: number) => Buffer;
  readonly #runContexts: RunContextStore;
  readonly #workspaceAuthority: LocalWorkspaceAuthority | null;
  #workspaceApprovalPermitHandoff: WorkspaceApprovalPermitHandoff | null = null;
  readonly #workspaceHostSessions = new Map<
    string,
    WorkspaceHostSessionState
  >();
  readonly #preparedHostSessions = new Map<string, string>();
  readonly #completedWorkspaceCommits = new Map<
    string,
    CompletedWorkspaceCommit
  >();
  #workspaceHostSessionSequence = 0;

  #server: Server | null = null;
  #tokenBuf: Buffer | null = null;
  // Per-boot salt keying the opaque grant `mount` ids (G1). Minted with the
  // token, dropped on stop — so mount ids rotate every boot alongside it.
  #saltBuf: Buffer | null = null;
  #port = 0;

  constructor(config: CapabilityBrokerConfig) {
    this.#grants = config.grants;
    this.#hostFs = config.hostFs ?? null;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
    this.#runContexts =
      config.runContexts ??
      new RunContextStore({ randomBytes: this.#randomBytes });
    this.#workspaceAuthority = config.workspaceAuthority ?? null;
  }

  isRunning(): boolean {
    return this.#server !== null;
  }

  /**
   * Main-only wiring seam for C3's verified-approval reservation source.
   * There is intentionally no IPC, environment, or HTTP route for setting it.
   */
  installWorkspaceApprovalPermitHandoff(
    handoff: WorkspaceApprovalPermitHandoff,
  ): void {
    if (
      this.#workspaceApprovalPermitHandoff !== null &&
      this.#workspaceApprovalPermitHandoff !== handoff
    ) {
      throw new Error("workspace approval permit handoff is already installed");
    }
    this.#workspaceApprovalPermitHandoff = handoff;
  }

  /**
   * Bind the loopback listener and mint a fresh per-boot token. Throws if
   * already running — callers rotate by `stop()` then `start()`.
   */
  async start(): Promise<CapabilityBrokerHandle> {
    if (this.#server !== null) {
      throw new Error("capability broker already running");
    }
    // 256-bit token, base64url (43 chars). Length is not secret.
    this.#tokenBuf = Buffer.from(
      this.#randomBytes(TOKEN_BYTES).toString("base64url"),
      "utf-8",
    );
    // 256-bit per-boot salt for the opaque grant `mount` ids (never sent).
    this.#saltBuf = this.#randomBytes(TOKEN_BYTES);

    const server = createServer((req, res) => {
      this.#handle(req, res).catch(() => {
        // Never surface internals; a handler crash is a generic 500.
        if (!res.headersSent) {
          respondJson(res, 500, { error: "internal" });
        } else {
          res.end();
        }
      });
    });

    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      // Ephemeral OS-assigned port, loopback-only bind.
      server.listen(0, "127.0.0.1", () => {
        server.off("error", reject);
        resolve();
      });
    });

    const address = server.address() as AddressInfo | null;
    if (address === null || typeof address === "string") {
      server.close();
      this.#tokenBuf = null;
      this.#saltBuf = null;
      throw new Error("capability broker failed to bind");
    }
    this.#server = server;
    this.#port = address.port;
    return { baseUrl: this.baseUrl(), port: this.#port };
  }

  /** Close the listener and drop the token. Prior tokens no longer work. */
  async stop(): Promise<void> {
    const server = this.#server;
    this.#server = null;
    this.#tokenBuf = null;
    this.#saltBuf = null;
    this.#port = 0;
    this.#workspaceHostSessions.clear();
    this.#preparedHostSessions.clear();
    this.#completedWorkspaceCommits.clear();
    this.#workspaceHostSessionSequence = 0;
    // Per-run snapshots are RAM-only and MUST NOT survive a restart — drop them
    // so a fresh boot never inherits a prior boot's pinned authority.
    this.#runContexts.clear();
    if (server === null) return;
    await new Promise<void>((resolve) => {
      server.close(() => {
        resolve();
      });
    });
  }

  /** Non-secret base URL. Throws if not running. */
  baseUrl(): string {
    if (this.#server === null) {
      throw new Error("capability broker is not running");
    }
    return `http://127.0.0.1:${this.#port}`;
  }

  /**
   * The per-boot bearer token. MAIN-ONLY: hand this to an intended child
   * process out of band (env / stdin / a file descriptor). NEVER return it
   * over renderer IPC and NEVER log it. Throws if not running.
   */
  authToken(): string {
    if (this.#tokenBuf === null) {
      throw new Error("capability broker is not running");
    }
    return this.#tokenBuf.toString("utf-8");
  }

  /**
   * Pin the CURRENT active grants under a fresh, opaque `run_capability_context`
   * and return the immutable context (MAIN-SIDE view — includes host roots).
   * Called at run start; the id is what a later FS op passes to bind itself to
   * this run's authority snapshot rather than live grant state.
   */
  async mintRunContext() {
    const snapshot = await this.#grants.snapshotActive();
    return this.#runContexts.mint(snapshot);
  }

  /** Release a run's pinned snapshot. True if it existed. */
  releaseRunContext(runContext: string): boolean {
    return this.#runContexts.release(runContext);
  }

  async #handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    // 1) No CORS / no browser callers. Reject any request carrying browser
    //    fetch metadata before doing anything else.
    if (hasBrowserMetadata(req)) {
      respondJson(res, 403, { error: "forbidden" });
      return;
    }
    const pathname = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
    const upload = parseWorkspaceUploadPath(pathname);
    // 2) Private v2 content slots use PUT with raw bytes. Every other broker
    // method remains POST+JSON. OPTIONS is always refused: no CORS.
    if (req.method !== "POST" && !(req.method === "PUT" && upload !== null)) {
      respondJson(res, 405, { error: "method_not_allowed" });
      return;
    }
    // 3) Protocol version handshake on every request.
    if (
      headerValue(req, "x-capability-protocol") !== CAPABILITY_BROKER_PROTOCOL
    ) {
      respondJson(res, 400, { error: "unsupported_protocol_version" });
      return;
    }
    // 4) Auth: constant-time bearer compare.
    if (!this.#authorized(req)) {
      respondJson(res, 401, { error: "unauthorized" });
      return;
    }
    // 5) A v2 upload streams bytes directly into private prepared storage. It
    // never becomes JSON/base64 and never takes a host filesystem path.
    if (upload !== null) {
      await this.#handleWorkspaceUpload(upload, req, res);
      return;
    }
    // 6) Body (size-capped, JSON). Write routes carry file content, so they get
    //    the larger cap; every other route keeps the tight 64 KiB cap.
    const bodyCap = WRITE_BODY_ROUTES.has(pathname)
      ? MAX_WRITE_BODY_BYTES
      : MAX_BODY_BYTES;
    let body: unknown;
    try {
      body = await readJsonBody(req, bodyCap);
    } catch (err) {
      if (err instanceof BodyTooLargeError) {
        respondJson(res, 413, { error: "payload_too_large" });
        return;
      }
      respondJson(res, 400, { error: "invalid_json" });
      return;
    }
    switch (pathname) {
      case ROUTES.handshake:
        respondJson(res, 200, {
          protocol: CAPABILITY_BROKER_PROTOCOL,
          methods: this.#advertisedMethods(),
          workspace_write_available:
            this.#workspaceAuthority?.writableAvailable() ?? false,
          serverTime: Date.now(),
        });
        return;
      case ROUTES.grantsList: {
        // G1: path-free projection — the host `root` never leaves main.
        const grants = (await this.#grants.listAll()).map((g) =>
          toBrokerGrant(g, this.#mountId(g.root)),
        );
        respondJson(res, 200, { grants });
        return;
      }
      case ROUTES.grantsSnapshot: {
        // G1: path-free projection of the active snapshot.
        const snapshot = await this.#grants.snapshotActive();
        respondJson(res, 200, {
          snapshotId: snapshot.snapshotId,
          capturedAt: snapshot.capturedAt,
          grants: snapshot.grants.map((g) =>
            toBrokerGrant(g, this.#mountId(g.root)),
          ),
        });
        return;
      }
      case ROUTES.runsBegin: {
        // Pin the active grants for a starting run; return the opaque context
        // id + a PATH-FREE projection of the pinned set (host roots stay main).
        const ctx = await this.mintRunContext();
        respondJson(res, 200, {
          runCapabilityContext: ctx.runContext,
          capturedAt: ctx.capturedAt,
          snapshotId: ctx.snapshotId,
          grants: ctx.grants.map((g) =>
            toBrokerGrant(g, this.#mountId(g.root)),
          ),
        });
        return;
      }
      case ROUTES.runsEnd: {
        const params = body === null || typeof body !== "object" ? {} : body;
        let released: boolean;
        try {
          released = this.releaseRunContext(
            requireString(params, "run_capability_context"),
          );
        } catch (err) {
          const { status, code } = fsErrorResponse(err);
          respondJson(res, status, { error: code });
          return;
        }
        respondJson(res, 200, { released });
        return;
      }
      case ROUTES.workspaceHostSessions:
        await this.#handleWorkspaceHostSession(body, res);
        return;
      case ROUTES.workspacePrepare:
        await this.#handleWorkspacePrepare(body, res);
        return;
      case ROUTES.fsStat:
      case ROUTES.fsList:
      case ROUTES.fsRead:
      case ROUTES.fsGlob:
      case ROUTES.fsGrep:
      case ROUTES.fsWrite:
      case ROUTES.fsEdit:
      case ROUTES.fsMkdir:
      case ROUTES.fsDelete:
      case ROUTES.fsMove:
        await this.#handleFs(pathname, body, res);
        return;
      default:
        if (pathname.startsWith(`${ROUTES.workspaceCommit}/`)) {
          await this.#handleWorkspacePreparedRoute(pathname, body, res);
          return;
        }
        if (pathname.startsWith(`${ROUTES.workspaceClaims}/`)) {
          await this.#handleWorkspaceClaimRoute(pathname, body, res);
          return;
        }
        respondJson(res, 404, { error: "not_found" });
        return;
    }
  }

  /**
   * Dispatch an AC5 legacy filesystem operation. Once the v2 authority is
   * installed, ALL of these paths are withdrawn — including reads — because a
   * process-wide boot bearer is not a workspace read capability. C2's native
   * read-capability port is the only permitted successor; until that platform
   * primitive is available we fail closed rather than retain a weaker route.
   */
  async #handleFs(
    route: string,
    body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    if (this.#workspaceAuthority !== null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    // Fail closed if no executor was wired.
    if (this.#hostFs === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    const hostFs = this.#hostFs;
    try {
      const params = body === null || typeof body !== "object" ? {} : body;
      const grantId = requireString(params, "grant_id");
      const runContext = optionalString(params, "run_capability_context");
      const grant = await this.#resolveGrant(grantId, runContext);
      // Per-route MODE gate (fail closed to the highest bar for an unknown
      // route; `modeSatisfies` fails closed for an unknown grant mode).
      const required = FS_ROUTE_REQUIRED_MODE[route] ?? "read_write";
      if (!modeSatisfies(required, grant.mode)) {
        throw new FsError("permission_denied", "grant mode too low");
      }
      const result = await runFsOp(hostFs, route, grant.root, params);
      respondJson(res, 200, result);
    } catch (err) {
      const { status, code } = fsErrorResponse(err);
      respondJson(res, status, { error: code });
    }
  }

  async #handleWorkspacePrepare(
    body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    const authority = this.#workspaceAuthority;
    if (authority === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    try {
      const params = requireRecord(body);
      const hostSessionRef = requireHostSessionRef(params);
      const session = this.#requireWorkspaceHostSession(hostSessionRef);
      const prepared = await authority.prepareChangeSet(
        session.readCapability,
        parseWorkspaceChangeSet(params),
      );
      this.#preparedHostSessions.set(prepared.preparedRef, hostSessionRef);
      respondJson(res, 201, {
        prepared_ref: prepared.preparedRef,
        expires_at: prepared.expiresAt,
        observed_target_digest: prepared.observedTargetDigest,
        upload_slots: prepared.uploadSlots.map((slot) => ({
          slot: slot.slot,
          digest: slot.digest,
          size: slot.size,
        })),
      });
    } catch (error) {
      respondWorkspaceError(res, error);
    }
  }

  /**
   * Bootstrap a run-scoped, main-owned C2 read capability for the intended
   * supervised worker.  The request's boot bearer authenticates transport only;
   * the authority derives device identity and live grants in Electron main.
   * The response deliberately contains just one opaque session reference and
   * path-free grant projections — never a capability, root, permit, or path.
   */
  async #handleWorkspaceHostSession(
    body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    const authority = this.#workspaceAuthority;
    if (authority === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    try {
      const params = requireRecord(body);
      const host = await authority.createPrivateHostSession(
        requireOpaqueId(params, "run_id"),
        requireOpaqueId(params, "user_id"),
      );
      const hostSessionRef = this.#newWorkspaceHostSessionRef();
      this.#workspaceHostSessions.set(
        hostSessionRef,
        Object.freeze({
          facts: Object.freeze({ ...host.facts }),
          readCapability: host.readCapability,
          grantIds: Object.freeze([...host.grantIds]),
          expiresAt: host.expiresAt,
        }),
      );
      const grantIds = new Set(host.grantIds);
      const grants = (await this.#grants.listAll())
        .filter((grant) => grantIds.has(grant.grantId))
        .map((grant) => toBrokerGrant(grant, this.#mountId(grant.root)));
      respondJson(res, 201, {
        host_session_ref: hostSessionRef,
        expires_at: host.expiresAt,
        grants,
      });
    } catch (error) {
      respondWorkspaceError(res, error);
    }
  }

  async #handleWorkspaceUpload(
    upload: WorkspaceUploadPath,
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> {
    const authority = this.#workspaceAuthority;
    if (authority === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    try {
      await streamBinaryBody(req, MAX_WORKSPACE_UPLOAD_BYTES, async (chunk) => {
        await authority.uploadPreparedContent(
          upload.preparedRef,
          upload.slot,
          chunk,
        );
      });
      if (headerValue(req, "x-workspace-upload-final") === "true") {
        await authority.sealPreparedContent(upload.preparedRef, upload.slot);
      }
      respondJson(res, 200, {
        accepted: true,
        sealed: headerValue(req, "x-workspace-upload-final") === "true",
      });
    } catch (error) {
      // A partial or malformed upload must never remain an eligible staging
      // object. Abort is idempotent at this boundary; suppress its outcome so
      // the caller receives the original validation/stream error only.
      await authority
        .abortPreparedChangeSet(upload.preparedRef)
        .catch(() => {});
      // Preserve the exact host-session association until the caller aborts
      // or the session expires. A later commit can then ask the C2 authority
      // for its terminal staging state and return `workspace_conflict` (409)
      // before touching a one-use approval reservation. Dropping it here
      // would incorrectly turn a failed upload into permit-denied (403).
      this.#completedWorkspaceCommits.delete(upload.preparedRef);
      respondWorkspaceError(res, error);
    }
  }

  async #handleWorkspacePreparedRoute(
    pathname: string,
    body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    const authority = this.#workspaceAuthority;
    if (authority === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    const prepared = parseWorkspacePreparedPath(pathname);
    if (prepared === null) {
      respondJson(res, 404, { error: "not_found" });
      return;
    }
    try {
      if (prepared.action === "abort") {
        await authority.abortPreparedChangeSet(prepared.preparedRef);
        this.#preparedHostSessions.delete(prepared.preparedRef);
        this.#completedWorkspaceCommits.delete(prepared.preparedRef);
        respondJson(res, 200, { aborted: true });
        return;
      }
      const params = requireRecord(body);
      const hostSessionRef = requireHostSessionRef(params);
      const session = this.#requireWorkspaceHostSession(hostSessionRef);
      const completed = this.#completedWorkspaceCommits.get(
        prepared.preparedRef,
      );
      if (completed !== undefined) {
        if (completed.hostSessionRef !== hostSessionRef) {
          throw new WorkspaceAuthorityError("workspace_permit_denied");
        }
        respondJson(res, 200, workspaceCommitWire(completed.result));
        return;
      }
      if (
        this.#preparedHostSessions.get(prepared.preparedRef) !== hostSessionRef
      ) {
        throw new WorkspaceAuthorityError("workspace_permit_denied");
      }
      const binding = await authority.commitEligibleBinding(
        prepared.preparedRef,
      );
      if (!sameWorkspaceFacts(binding.facts, session.facts)) {
        throw new WorkspaceAuthorityError("workspace_permit_denied");
      }
      const handoff = this.#workspaceApprovalPermitHandoff;
      if (handoff === null) {
        throw new WorkspaceAuthorityError("workspace_permit_denied");
      }
      // The exact approval binding comes exclusively from C2's prepared state.
      // The worker supplied only the opaque host-session reference above.
      const permit = await handoff.take({
        facts: binding.facts,
        preparedRef: binding.preparedRef,
        stageId: binding.stageId,
        revision: binding.revision,
        decisionLedgerId: binding.decisionLedgerId,
        changeSetDigest: binding.changeSetDigest,
        targetDigest: binding.targetDigest,
        proposalDigest: binding.proposalDigest,
      });
      if (permit === null) {
        throw new WorkspaceAuthorityError("workspace_permit_denied");
      }
      // The raw C2 permit exists only in this Electron-main stack frame. It is
      // neither returned to the child nor accepted as a child-controlled body.
      const result = await authority.commitPreparedChangeSet(
        prepared.preparedRef,
        permit,
      );
      this.#completedWorkspaceCommits.set(
        prepared.preparedRef,
        Object.freeze({ hostSessionRef, result }),
      );
      respondJson(res, 200, workspaceCommitWire(result));
    } catch (error) {
      respondWorkspaceError(res, error);
    }
  }

  async #handleWorkspaceClaimRoute(
    pathname: string,
    _body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    const authority = this.#workspaceAuthority;
    if (authority === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    const claimId = parseWorkspaceClaimPath(pathname);
    if (claimId === null) {
      respondJson(res, 404, { error: "not_found" });
      return;
    }
    try {
      respondJson(
        res,
        200,
        workspaceCommitWire(await authority.reconcileCommit(claimId)),
      );
    } catch (error) {
      respondWorkspaceError(res, error);
    }
  }

  #newWorkspaceHostSessionRef(): string {
    this.#workspaceHostSessionSequence += 1;
    return `whs_${this.#randomBytes(32).toString("base64url")}_${this.#workspaceHostSessionSequence}`;
  }

  #requireWorkspaceHostSession(ref: string): WorkspaceHostSessionState {
    const session = this.#workspaceHostSessions.get(ref);
    if (session === undefined || session.expiresAt <= Date.now()) {
      this.#workspaceHostSessions.delete(ref);
      throw new WorkspaceAuthorityError("workspace_capability_denied");
    }
    return session;
  }

  /**
   * Resolve a grant id to authorize an op. When the request carries a
   * `run_capability_context`, resolve against that run's PINNED snapshot (the
   * grants active when the run started) — so an op is authorized against a
   * consistent, run-bound view rather than live state. Otherwise resolve
   * against the CURRENT active snapshot (a revoke then takes effect on the very
   * next op). Both fail closed with `grant_required`.
   */
  async #resolveGrant(
    grantId: string,
    runContext: string | undefined,
  ): Promise<Grant> {
    if (runContext !== undefined) {
      const ctx = this.#runContexts.get(runContext);
      if (ctx === null) {
        throw new FsError("grant_required", "unknown run capability context");
      }
      const pinned = ctx.grants.find((g) => g.grantId === grantId);
      if (pinned === undefined) {
        throw new FsError("grant_required", "grant not in run snapshot");
      }
      return pinned;
    }
    const snapshot = await this.#grants.snapshotActive();
    const grant = snapshot.grants.find((g) => g.grantId === grantId);
    if (grant === undefined) {
      throw new FsError("grant_required", "no active grant for id");
    }
    return grant;
  }

  /**
   * Derive the OPAQUE, per-boot `mount` id for a grant's canonical root (G1).
   * HMAC-SHA256 under the per-boot salt, base64url, truncated. Stable within a
   * boot (same root → same mount) and non-reversible; because the salt is
   * random per boot and never leaves main, it is not a cross-boot brute-force
   * oracle for a caller that guesses a candidate host path.
   */
  #mountId(root: string): string {
    const salt = this.#saltBuf;
    if (salt === null) {
      // Only reachable if called while stopped; fail closed rather than leak.
      throw new Error("capability broker is not running");
    }
    const digest = createHmac("sha256", salt)
      .update(root, "utf-8")
      .digest("base64url");
    return `mnt_${digest.slice(0, 24)}`;
  }

  #advertisedMethods(): readonly string[] {
    const authority = this.#workspaceAuthority;
    if (authority === null) return ADVERTISED_METHODS;
    return ADVERTISED_METHODS.filter((method) => {
      if (LEGACY_FILESYSTEM_METHODS.has(method)) return false;
      if (WORKSPACE_V2_METHODS.has(method))
        return authority.writableAvailable();
      return true;
    });
  }

  #authorized(req: IncomingMessage): boolean {
    const expected = this.#tokenBuf;
    if (expected === null) return false;
    const header = headerValue(req, "authorization");
    if (header === null) return false;
    const match = /^Bearer (.+)$/u.exec(header);
    if (match === null) return false;
    const provided = Buffer.from(match[1], "utf-8");
    // timingSafeEqual requires equal lengths; token length is fixed and not
    // secret, so a length mismatch is a safe early reject.
    if (provided.length !== expected.length) return false;
    return timingSafeEqual(provided, expected);
  }
}

// --- FS op dispatch + param coercion (broker-side, dependency-light) ---

const FS_ROUTE_HANDLERS: Record<
  string,
  (hostFs: HostFs, root: string, p: Record<string, unknown>) => Promise<unknown>
> = {
  [ROUTES.fsStat]: (hostFs, root, p) => hostFs.stat(root, requirePath(p)),
  [ROUTES.fsList]: (hostFs, root, p) => hostFs.list(root, requirePath(p)),
  [ROUTES.fsRead]: (hostFs, root, p) =>
    hostFs.read(root, requirePath(p), {
      offset: optionalInt(p, "offset"),
      maxBytes: optionalInt(p, "max_bytes"),
    }),
  [ROUTES.fsGlob]: (hostFs, root, p) =>
    hostFs.glob(root, requireString(p, "pattern"), {
      maxResults: optionalInt(p, "max_results"),
    }),
  [ROUTES.fsGrep]: (hostFs, root, p) =>
    hostFs.grep(root, requireString(p, "pattern"), {
      pathGlob: optionalString(p, "path_glob"),
      isRegex: optionalBool(p, "is_regex"),
      flags: optionalString(p, "flags"),
      maxMatches: optionalInt(p, "max_matches"),
    }),
  [ROUTES.fsWrite]: (hostFs, root, p) =>
    hostFs.write(root, requirePath(p), requireContent(p)),
  [ROUTES.fsEdit]: (hostFs, root, p) =>
    hostFs.edit(root, requirePath(p), requireContent(p)),
  [ROUTES.fsMkdir]: (hostFs, root, p) => hostFs.mkdir(root, requirePath(p)),
  [ROUTES.fsDelete]: (hostFs, root, p) => hostFs.delete(root, requirePath(p)),
  [ROUTES.fsMove]: (hostFs, root, p) =>
    hostFs.move(root, requireString(p, "from"), requireString(p, "to")),
};

function runFsOp(
  hostFs: HostFs,
  route: string,
  root: string,
  params: object,
): Promise<unknown> {
  const handler = FS_ROUTE_HANDLERS[route];
  if (handler === undefined) {
    return Promise.reject(new FsError("unsupported", "unknown fs route"));
  }
  return handler(hostFs, root, params as Record<string, unknown>);
}

function requireString(params: object, key: string): string {
  const value = (params as Record<string, unknown>)[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new FsError("invalid_request", `missing ${key}`);
  }
  return value;
}

function requireOpaqueId(params: object, key: string): string {
  const value = requireString(params, key);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value)) {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

function requireHostSessionRef(params: object): string {
  const value = requireString(params, "host_session_ref");
  if (!HOST_SESSION_REF.test(value)) {
    throw new FsError("invalid_request", "invalid host_session_ref");
  }
  return value;
}

function sameWorkspaceFacts(
  left: WorkspaceRunFacts,
  right: WorkspaceRunFacts,
): boolean {
  return (
    left.runId === right.runId &&
    left.userId === right.userId &&
    left.deviceId === right.deviceId
  );
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new FsError("invalid_request", "request body must be an object");
  }
  return value as Record<string, unknown>;
}

function requireBoolean(params: object, key: string): boolean {
  const value = (params as Record<string, unknown>)[key];
  if (typeof value !== "boolean") {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

function requirePositiveInteger(params: object, key: string): number {
  const value = (params as Record<string, unknown>)[key];
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value as number;
}

function requireDigest(params: object, key: string): string {
  const value = requireString(params, key);
  if (!/^[a-f0-9]{64}$/u.test(value)) {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

function parseWorkspaceChangeSet(
  params: Record<string, unknown>,
): WorkspaceChangeSet {
  const rawEntries = params.entries;
  if (
    !Array.isArray(rawEntries) ||
    rawEntries.length === 0 ||
    rawEntries.length > 1_000
  ) {
    throw new FsError("invalid_request", "invalid entries");
  }
  return {
    stageId: requireString(params, "stage_id"),
    revision: requirePositiveInteger(params, "revision"),
    decisionLedgerId: requireString(params, "decision_ledger_id"),
    grantId: requireString(params, "grant_id"),
    mount: requireString(params, "mount"),
    changeSetDigest: requireDigest(params, "change_set_digest"),
    targetDigest: requireDigest(params, "target_digest"),
    proposalDigest: requireDigest(params, "proposal_digest"),
    entries: rawEntries.map((entry) =>
      parseWorkspaceEntry(requireRecord(entry)),
    ),
  };
}

function parseWorkspaceEntry(
  params: Record<string, unknown>,
): WorkspaceChangeEntry {
  const operation = requireString(params, "operation");
  if (
    operation !== "create" &&
    operation !== "replace" &&
    operation !== "delete" &&
    operation !== "move" &&
    operation !== "mkdir"
  ) {
    throw new FsError("invalid_request", "invalid workspace operation");
  }
  const precondition = requireRecord(params.precondition);
  const destination = optionalString(params, "destination_relative_path");
  const contentDigest = optionalString(params, "content_digest");
  const contentSize = optionalInt(params, "content_size");
  const contentSlot = optionalString(params, "content_slot");
  if (contentDigest !== undefined && !/^[a-f0-9]{64}$/u.test(contentDigest)) {
    throw new FsError("invalid_request", "invalid content_digest");
  }
  if (contentSize !== undefined && contentSize < 0) {
    throw new FsError("invalid_request", "invalid content_size");
  }
  const needsContent = operation === "create" || operation === "replace";
  if (
    needsContent !==
    (contentDigest !== undefined &&
      contentSize !== undefined &&
      contentSlot !== undefined)
  ) {
    throw new FsError(
      "invalid_request",
      "workspace content declaration mismatch",
    );
  }
  if (
    !needsContent &&
    (contentDigest !== undefined ||
      contentSize !== undefined ||
      contentSlot !== undefined)
  ) {
    throw new FsError("invalid_request", "unexpected workspace content");
  }
  if ((operation === "move") !== (destination !== undefined)) {
    throw new FsError("invalid_request", "workspace destination mismatch");
  }
  return {
    operation,
    relativePath: requireString(params, "relative_path"),
    destinationRelativePath: destination,
    contentDigest,
    contentSize,
    contentSlot,
    precondition: {
      exists: requireBoolean(precondition, "exists"),
      kind: optionalWorkspaceKind(precondition, "kind"),
      stableId: optionalString(precondition, "stable_id"),
      sha256: optionalDigest(precondition, "sha256"),
    },
  };
}

function optionalWorkspaceKind(
  params: Record<string, unknown>,
  key: string,
): "file" | "directory" | undefined {
  const value = optionalString(params, key);
  if (value === undefined || value === "file" || value === "directory")
    return value;
  throw new FsError("invalid_request", `invalid ${key}`);
}

function optionalDigest(
  params: Record<string, unknown>,
  key: string,
): string | undefined {
  const value = optionalString(params, key);
  if (value === undefined || /^[a-f0-9]{64}$/u.test(value)) return value;
  throw new FsError("invalid_request", `invalid ${key}`);
}

interface WorkspaceUploadPath {
  readonly preparedRef: string;
  readonly slot: string;
}

function parseWorkspaceUploadPath(
  pathname: string,
): WorkspaceUploadPath | null {
  const match =
    /^\/internal\/workspace\/v2\/prepared\/([A-Za-z0-9_-]{1,120})\/content\/([A-Za-z0-9_-]{1,120})$/u.exec(
      pathname,
    );
  if (match === null) return null;
  return { preparedRef: preparedRefFromId(match[1]), slot: match[2] };
}

function parseWorkspacePreparedPath(pathname: string): {
  readonly preparedRef: string;
  readonly action: "commit" | "abort";
} | null {
  const match =
    /^\/internal\/workspace\/v2\/prepared\/([A-Za-z0-9_-]{1,120})\/(commit|abort)$/u.exec(
      pathname,
    );
  if (match === null) return null;
  return {
    preparedRef: preparedRefFromId(match[1]),
    action: match[2] as "commit" | "abort",
  };
}

function parseWorkspaceClaimPath(pathname: string): string | null {
  const match =
    /^\/internal\/workspace\/v2\/claims\/([A-Za-z0-9_-]{1,160})\/reconcile$/u.exec(
      pathname,
    );
  return match?.[1] ?? null;
}

function preparedRefFromId(id: string): string {
  return `workspace-prepared://${id}`;
}

function workspaceCommitWire(
  result: WorkspaceCommitResult,
): Record<string, unknown> {
  return {
    outcome: result.outcome,
    receipt_ref: result.receiptRef,
    result_digest: result.resultDigest,
    safe_message: result.safeMessage,
  };
}

// `path` must be PRESENT and a string, but an empty string is valid — it
// denotes the grant root itself (e.g. list/stat of the root). Absence is still
// a bad request.
function requirePath(params: object): string {
  const value = (params as Record<string, unknown>).path;
  if (typeof value !== "string") {
    throw new FsError("invalid_request", "missing path");
  }
  return value;
}

// Decode the base64 `content_base64` write payload into a Buffer, enforcing the
// decoded-size ceiling BEFORE it reaches HostFs (HostFs re-checks — defence in
// depth). Absence or a non-string is `invalid_request`.
function requireContent(params: object): Buffer {
  const value = (params as Record<string, unknown>).content_base64;
  if (typeof value !== "string") {
    throw new FsError("invalid_request", "missing content_base64");
  }
  const buf = Buffer.from(value, "base64");
  if (buf.length > FS_LIMITS.maxWriteBytes) {
    throw new FsError("too_large", "content exceeds the write byte ceiling");
  }
  return buf;
}

function optionalString(params: object, key: string): string | undefined {
  const value = (params as Record<string, unknown>)[key];
  if (value === undefined) return undefined;
  if (typeof value !== "string") {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

function optionalInt(params: object, key: string): number | undefined {
  const value = (params as Record<string, unknown>)[key];
  if (value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

function optionalBool(params: object, key: string): boolean | undefined {
  const value = (params as Record<string, unknown>)[key];
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") {
    throw new FsError("invalid_request", `invalid ${key}`);
  }
  return value;
}

const FS_ERROR_STATUS: Record<FsErrorCode, number> = {
  invalid_path: 400,
  invalid_request: 400,
  not_a_directory: 400,
  not_a_file: 400,
  grant_required: 403,
  permission_denied: 403,
  not_found: 404,
  unsupported: 404,
  too_large: 413,
};

function fsErrorResponse(err: unknown): { status: number; code: string } {
  if (err instanceof FsError) {
    return { status: FS_ERROR_STATUS[err.code], code: err.code };
  }
  // Never leak an unexpected error's message (could carry a host path).
  return { status: 500, code: "internal" };
}

function respondWorkspaceError(res: ServerResponse, error: unknown): void {
  if (error instanceof BodyTooLargeError) {
    respondJson(res, 413, { error: "payload_too_large" });
    return;
  }
  if (error instanceof WorkspaceAuthorityError) {
    const status =
      error.code === "workspace_write_unsupported"
        ? 404
        : error.code === "workspace_prepared_not_found"
          ? 404
          : error.code === "workspace_capability_denied" ||
              error.code === "workspace_permit_denied"
            ? 403
            : 409;
    respondJson(res, status, { error: error.code });
    return;
  }
  const { status, code } = fsErrorResponse(error);
  respondJson(res, status, { error: code });
}

class BodyTooLargeError extends Error {}

function readJsonBody(
  req: IncomingMessage,
  maxBytes: number,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let done = false;
    req.on("data", (chunk: Buffer) => {
      if (done) return;
      size += chunk.length;
      if (size > maxBytes) {
        done = true;
        // Drain (discard) the rest so the socket can still flush our 413,
        // rather than resetting the connection.
        req.resume();
        reject(new BodyTooLargeError());
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (done) return;
      done = true;
      const raw = Buffer.concat(chunks).toString("utf-8").trim();
      if (raw.length === 0) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", (err) => {
      if (done) return;
      done = true;
      reject(err);
    });
  });
}

async function streamBinaryBody(
  req: IncomingMessage,
  maxBytes: number,
  onChunk: (chunk: Uint8Array) => Promise<void>,
): Promise<void> {
  let size = 0;
  try {
    for await (const rawChunk of req) {
      const chunk = Buffer.isBuffer(rawChunk)
        ? rawChunk
        : Buffer.from(rawChunk);
      size += chunk.byteLength;
      if (size > maxBytes) {
        req.resume();
        throw new BodyTooLargeError();
      }
      await onChunk(chunk);
    }
  } catch (error) {
    req.resume();
    throw error;
  }
}

// The renderer is a real Chromium context; a fetch from it to this loopback is
// cross-origin and therefore attaches `Origin`, plus `Sec-Fetch-Site` and
// `Sec-Fetch-Dest`. (Its custom Authorization header would also trip a CORS
// preflight that we never satisfy.) Any of those three means a browser caller
// — refuse it. NOTE: `Sec-Fetch-Mode` is deliberately NOT in this set: Node's
// own `fetch` (undici) sets `sec-fetch-mode: cors` on ordinary non-browser
// requests, so it does not discriminate a browser from an intended child.
// The bearer token remains the primary gate; this is defense in depth.
function hasBrowserMetadata(req: IncomingMessage): boolean {
  if (headerValue(req, "origin") !== null) return true;
  if (headerValue(req, "sec-fetch-site") !== null) return true;
  if (headerValue(req, "sec-fetch-dest") !== null) return true;
  return false;
}

function headerValue(req: IncomingMessage, name: string): string | null {
  const value = req.headers[name];
  if (value === undefined) return null;
  return Array.isArray(value) ? (value[0] ?? null) : value;
}

function respondJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("x-content-type-options", "nosniff");
  // Intentionally NO Access-Control-Allow-* headers — this surface is not for
  // browsers.
  res.end(payload);
}
