import { randomBytes as nodeRandomBytes, timingSafeEqual } from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

import type { GrantProvider } from "./types";

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
// SLICE 1 exposes ONLY grant-management reads (handshake / list / snapshot).
// Filesystem read/write/stat/list/glob/grep methods land in slice 2, each
// gated by careful path validation performed HERE.

export const CAPABILITY_BROKER_PROTOCOL = "1";

const TOKEN_BYTES = 32; // 256-bit
const MAX_BODY_BYTES = 64 * 1024;

const ROUTES = {
  handshake: "/v1/handshake",
  grantsList: "/v1/grants/list",
  grantsSnapshot: "/v1/grants/snapshot",
} as const;

// The capability methods this skeleton advertises. Slice 2 extends this list.
const ADVERTISED_METHODS = ["listGrants", "snapshotGrants"] as const;

export interface CapabilityBrokerConfig {
  readonly grants: GrantProvider;
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
  readonly #randomBytes: (size: number) => Buffer;

  #server: Server | null = null;
  #tokenBuf: Buffer | null = null;
  #port = 0;

  constructor(config: CapabilityBrokerConfig) {
    this.#grants = config.grants;
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
    void body; // no request params are consumed by the slice-1 read methods.

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
      default:
        respondJson(res, 404, { error: "not_found" });
        return;
    }
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
