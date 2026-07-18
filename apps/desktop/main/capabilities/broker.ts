import { randomBytes as nodeRandomBytes, timingSafeEqual } from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

import { HostFs } from "./host-fs";
import { FsError, modeSatisfies, type FsErrorCode } from "./path-validation";
import type { Grant, GrantProvider } from "./types";

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
// SLICE 2 (this change) adds the filesystem READ ops (stat/list/read/glob/grep)
// for the runtime-worker audience. Each resolves its `grant_id` against the
// CURRENT active snapshot (so a revoked grant fails closed), gates on the
// grant mode, and runs `HostFs`, whose path-validation layer performs the
// traversal / symlink / junction / ADS / TOCTOU checks. Write/mkdir/delete/
// move remain absent (slice 3). Host absolute paths NEVER appear in any
// response body — results carry only root-relative virtual paths.

export const CAPABILITY_BROKER_PROTOCOL = "1";

const TOKEN_BYTES = 32; // 256-bit
const MAX_BODY_BYTES = 64 * 1024;

const ROUTES = {
  handshake: "/v1/handshake",
  grantsList: "/v1/grants/list",
  grantsSnapshot: "/v1/grants/snapshot",
  fsStat: "/v1/fs/stat",
  fsList: "/v1/fs/list",
  fsRead: "/v1/fs/read",
  fsGlob: "/v1/fs/glob",
  fsGrep: "/v1/fs/grep",
} as const;

// The capability methods this broker advertises to a handshaking child.
const ADVERTISED_METHODS = [
  "listGrants",
  "snapshotGrants",
  "statPath",
  "listDir",
  "readFile",
  "glob",
  "grep",
] as const;

// Every READ op requires at least a read-only grant. The gate is generic so
// slice-3 writes can raise the bar per route; it fails closed for an unknown
// mode. Reads never demand more than the minimum, so no read is mode-denied —
// but the gate is the same one writes will use.
const FS_READ_REQUIRED_MODE = "read_only";

export interface CapabilityBrokerConfig {
  readonly grants: GrantProvider;
  /**
   * Host filesystem read executor. When omitted the FS routes fail closed
   * (`unsupported`) — the broker never touches the disk without it.
   */
  readonly hostFs?: HostFs;
  /** Injectable CSPRNG for tests. Defaults to node:crypto randomBytes. */
  readonly randomBytes?: (size: number) => Buffer;
}

export interface CapabilityBrokerHandle {
  /** `http://127.0.0.1:<port>` — non-secret; safe to pass as plain config. */
  readonly baseUrl: string;
  readonly port: number;
}

export class CapabilityBroker {
  readonly #grants: GrantProvider;
  readonly #hostFs: HostFs | null;
  readonly #randomBytes: (size: number) => Buffer;

  #server: Server | null = null;
  #tokenBuf: Buffer | null = null;
  #port = 0;

  constructor(config: CapabilityBrokerConfig) {
    this.#grants = config.grants;
    this.#hostFs = config.hostFs ?? null;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
  }

  isRunning(): boolean {
    return this.#server !== null;
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
    this.#port = 0;
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

  async #handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    // 1) No CORS / no browser callers. Reject any request carrying browser
    //    fetch metadata before doing anything else.
    if (hasBrowserMetadata(req)) {
      respondJson(res, 403, { error: "forbidden" });
      return;
    }
    // 2) Method: JSON-RPC-ish over POST only. OPTIONS (preflight) is refused
    //    outright — we never negotiate CORS.
    if (req.method !== "POST") {
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
    // 5) Body (size-capped, JSON).
    let body: unknown;
    try {
      body = await readJsonBody(req);
    } catch (err) {
      if (err instanceof BodyTooLargeError) {
        respondJson(res, 413, { error: "payload_too_large" });
        return;
      }
      respondJson(res, 400, { error: "invalid_json" });
      return;
    }
    const pathname = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
    switch (pathname) {
      case ROUTES.handshake:
        respondJson(res, 200, {
          protocol: CAPABILITY_BROKER_PROTOCOL,
          methods: ADVERTISED_METHODS,
          serverTime: Date.now(),
        });
        return;
      case ROUTES.grantsList: {
        const grants = await this.#grants.listAll();
        respondJson(res, 200, { grants });
        return;
      }
      case ROUTES.grantsSnapshot: {
        const snapshot = await this.#grants.snapshotActive();
        respondJson(res, 200, snapshot);
        return;
      }
      case ROUTES.fsStat:
      case ROUTES.fsList:
      case ROUTES.fsRead:
      case ROUTES.fsGlob:
      case ROUTES.fsGrep:
        await this.#handleFs(pathname, body, res);
        return;
      default:
        respondJson(res, 404, { error: "not_found" });
        return;
    }
  }

  /**
   * Dispatch a filesystem READ op. Resolves the grant from the CURRENT active
   * snapshot (revoked → `grant_required`), gates on grant mode, then runs the
   * op on `HostFs`. All failures collapse to a generic `{ error: <code> }`
   * with a mapped status — never a host path, never an internal stack.
   */
  async #handleFs(
    route: string,
    body: unknown,
    res: ServerResponse,
  ): Promise<void> {
    // Fail closed if no executor was wired.
    if (this.#hostFs === null) {
      respondJson(res, 404, { error: "unsupported" });
      return;
    }
    const hostFs = this.#hostFs;
    try {
      const params = body === null || typeof body !== "object" ? {} : body;
      const grantId = requireString(params, "grant_id");
      const grant = await this.#resolveActiveGrant(grantId);
      if (!modeSatisfies(FS_READ_REQUIRED_MODE, grant.mode)) {
        throw new FsError("permission_denied", "grant mode too low");
      }
      const result = await runFsOp(hostFs, route, grant.root, params);
      respondJson(res, 200, result);
    } catch (err) {
      const { status, code } = fsErrorResponse(err);
      respondJson(res, status, { error: code });
    }
  }

  /**
   * Resolve a grant id against the active snapshot. Unknown or revoked ids
   * (revoked grants are excluded from `snapshotActive`) fail closed with
   * `grant_required`, so a revoke takes effect on the very next op.
   */
  async #resolveActiveGrant(grantId: string): Promise<Grant> {
    const snapshot = await this.#grants.snapshotActive();
    const grant = snapshot.grants.find((g) => g.grantId === grantId);
    if (grant === undefined) {
      throw new FsError("grant_required", "no active grant for id");
    }
    return grant;
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

class BodyTooLargeError extends Error {}

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let done = false;
    req.on("data", (chunk: Buffer) => {
      if (done) return;
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
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
