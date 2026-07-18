// AC8 agentic browser — authenticated loopback broker (Electron-main owned).
//
// The narrow, authenticated surface the AI worker's DesktopBrowserMcpProvider
// calls. It mirrors the AC5 capability broker's transport hardening
// (`capabilities/broker.ts`) — loopback-only bind, per-boot CSPRNG bearer,
// constant-time compare, protocol header, POST+JSON only, browser-metadata
// rejection (no CORS) — and adds the AC8 request-binding controls:
//
//   - every request envelope carries `aud` (must equal the browser broker
//     audience), `requestId`, `nonce`, and `expiresAt`;
//   - a wrong audience, an expired envelope, or a REPLAYED nonce/requestId is
//     rejected (fail closed);
//   - it never returns the worker credential; the AI credential never reaches
//     the renderer or worker.
//
// It exposes exactly two routes — `tools/list` (read-only schemas) and `action`
// (dispatch a typed read-only action) — forwarding the latter to an injected
// `BrowserWorkerPort`. The worker credential + the main<->worker channel are
// owned elsewhere; this broker is the AI-facing edge and is unit-tested against
// a fake port.

import { randomBytes as nodeRandomBytes, timingSafeEqual } from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

import {
  BROWSER_BROKER_AUDIENCE,
  BrowserActionRequestSchema,
  type BrowserActionRequest,
  type BrowserActionResult,
} from "./protocol";
import { BROWSER_TOOL_SCHEMAS, type BrowserToolSchema } from "./tool-schemas";

export const BROWSER_BROKER_PROTOCOL = "1";

const TOKEN_BYTES = 32;
const MAX_BODY_BYTES = 256 * 1024;

const ROUTES = {
  handshake: "/v1/browser/handshake",
  toolsList: "/v1/browser/tools/list",
  action: "/v1/browser/action",
} as const;

export interface BrowserWorkerPort {
  listTools(): Promise<readonly BrowserToolSchema[]>;
  dispatch(request: BrowserActionRequest): Promise<BrowserActionResult>;
}

export interface BrowserBrokerConfig {
  readonly worker: BrowserWorkerPort;
  readonly randomBytes?: (size: number) => Buffer;
  readonly now?: () => number;
  /** Replay-cache TTL for nonces/request ids (default 5 min). */
  readonly nonceTtlMs?: number;
}

export interface BrowserBrokerHandle {
  readonly baseUrl: string;
  readonly port: number;
}

interface RequestEnvelope {
  aud?: unknown;
  nonce?: unknown;
  requestId?: unknown;
  expiresAt?: unknown;
  action?: unknown;
}

export class BrowserBroker {
  readonly #worker: BrowserWorkerPort;
  readonly #randomBytes: (size: number) => Buffer;
  readonly #now: () => number;
  readonly #nonceTtlMs: number;
  #server: Server | null = null;
  #tokenBuf: Buffer | null = null;
  #port = 0;
  // Replay caches: value -> expiry epoch ms.
  readonly #seenNonces = new Map<string, number>();
  readonly #seenRequestIds = new Map<string, number>();

  constructor(config: BrowserBrokerConfig) {
    this.#worker = config.worker;
    this.#randomBytes = config.randomBytes ?? nodeRandomBytes;
    this.#now = config.now ?? Date.now;
    this.#nonceTtlMs = config.nonceTtlMs ?? 5 * 60 * 1000;
  }

  isRunning(): boolean {
    return this.#server !== null;
  }

  async start(): Promise<BrowserBrokerHandle> {
    if (this.#server !== null)
      throw new Error("browser broker already running");
    this.#tokenBuf = Buffer.from(
      this.#randomBytes(TOKEN_BYTES).toString("base64url"),
      "utf-8",
    );
    const server = createServer((req, res) => {
      this.#handle(req, res).catch(() => {
        if (!res.headersSent) respondJson(res, 500, { error: "internal" });
        else res.end();
      });
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        server.off("error", reject);
        resolve();
      });
    });
    const address = server.address() as AddressInfo | null;
    if (address === null || typeof address === "string") {
      server.close();
      this.#tokenBuf = null;
      throw new Error("browser broker failed to bind");
    }
    this.#server = server;
    this.#port = address.port;
    return { baseUrl: this.baseUrl(), port: this.#port };
  }

  async stop(): Promise<void> {
    const server = this.#server;
    this.#server = null;
    this.#tokenBuf = null;
    this.#port = 0;
    this.#seenNonces.clear();
    this.#seenRequestIds.clear();
    if (server === null) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  baseUrl(): string {
    if (this.#server === null) throw new Error("browser broker is not running");
    return `http://127.0.0.1:${this.#port}`;
  }

  /** Per-boot bearer for the AI worker. MAIN-ONLY; never over renderer IPC. */
  authToken(): string {
    if (this.#tokenBuf === null)
      throw new Error("browser broker is not running");
    return this.#tokenBuf.toString("utf-8");
  }

  async #handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    if (hasBrowserMetadata(req))
      return respondJson(res, 403, { error: "forbidden" });
    if (req.method !== "POST")
      return respondJson(res, 405, { error: "method_not_allowed" });
    if (headerValue(req, "x-browser-protocol") !== BROWSER_BROKER_PROTOCOL) {
      return respondJson(res, 400, { error: "unsupported_protocol_version" });
    }
    if (!this.#authorized(req))
      return respondJson(res, 401, { error: "unauthorized" });

    const pathname = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
    if (pathname === ROUTES.handshake) {
      return respondJson(res, 200, {
        protocol: BROWSER_BROKER_PROTOCOL,
        audience: BROWSER_BROKER_AUDIENCE,
      });
    }

    let body: unknown;
    try {
      body = await readJsonBody(req, MAX_BODY_BYTES);
    } catch (err) {
      if (err instanceof BodyTooLargeError)
        return respondJson(res, 413, { error: "payload_too_large" });
      return respondJson(res, 400, { error: "invalid_json" });
    }

    const envelope = (body ?? {}) as RequestEnvelope;
    const bindingError = this.#validateEnvelope(envelope);
    if (bindingError !== null)
      return respondJson(res, 401, { error: bindingError });

    switch (pathname) {
      case ROUTES.toolsList:
        return respondJson(res, 200, { tools: await this.#worker.listTools() });
      case ROUTES.action: {
        const parsed = BrowserActionRequestSchema.safeParse(envelope.action);
        if (!parsed.success)
          return respondJson(res, 400, { error: "invalid_action" });
        const result = await this.#worker.dispatch(parsed.data);
        return respondJson(res, 200, { result });
      }
      default:
        return respondJson(res, 404, { error: "not_found" });
    }
  }

  /**
   * Validate the request-binding envelope: audience, freshness, and single-use
   * nonce + request id. Returns an error code string, or null when valid.
   */
  #validateEnvelope(envelope: RequestEnvelope): string | null {
    if (envelope.aud !== BROWSER_BROKER_AUDIENCE) return "wrong_audience";
    const nonce = envelope.nonce;
    const requestId = envelope.requestId;
    const expiresAt = envelope.expiresAt;
    if (typeof nonce !== "string" || nonce === "") return "missing_nonce";
    if (typeof requestId !== "string" || requestId === "")
      return "missing_request_id";
    if (typeof expiresAt !== "number") return "missing_expiry";

    const now = this.#now();
    this.#sweep(now);
    if (expiresAt < now) return "expired";
    if (this.#seenNonces.has(nonce)) return "replayed_nonce";
    if (this.#seenRequestIds.has(requestId)) return "replayed_request_id";

    const ttlExpiry = now + this.#nonceTtlMs;
    this.#seenNonces.set(nonce, ttlExpiry);
    this.#seenRequestIds.set(requestId, ttlExpiry);
    return null;
  }

  #sweep(now: number): void {
    for (const [key, expiry] of this.#seenNonces) {
      if (expiry <= now) this.#seenNonces.delete(key);
    }
    for (const [key, expiry] of this.#seenRequestIds) {
      if (expiry <= now) this.#seenRequestIds.delete(key);
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
    if (provided.length !== expected.length) return false;
    return timingSafeEqual(provided, expected);
  }
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
      if (raw.length === 0) return resolve({});
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
  res.end(payload);
}
