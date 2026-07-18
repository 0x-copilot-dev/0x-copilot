// AC8 agentic browser — loopback egress policy proxy.
//
// Chromium launches with THIS as its only proxy and no bypass list, so every
// HTTPS connection arrives here as `CONNECT host:443`. The proxy:
//
//   1. rejects any method other than CONNECT (http/ftp/etc. never tunnel),
//   2. rejects any port other than 443 (non-default ports are denied),
//   3. rejects a host that is not in the approved exact-origin host set,
//   4. rejects metadata / `.local` / single-label / IP-literal-to-denied-range
//      hosts via the shape policy,
//   5. RESOLVES DNS ITSELF, checks EVERY resolved address against the denied
//      range table, and — this is the anti-rebinding control — DIALS THE PINNED
//      NUMERIC ADDRESS, never the hostname. A later reconnect re-runs the whole
//      check, so a rebind after TTL/keepalive expiry is re-validated and denied.
//
// The socket plumbing is thin; the tested surface is `authorizeConnect`, a pure
// async decision over injected DNS. Real DNS + dialer are injected so unit tests
// never touch the network. Fail-closed: any resolve error, any denied address,
// or a host with zero allowed addresses is DENIED.

import { createServer, type Server } from "node:http";
import type { AddressInfo, Socket } from "node:net";
import { connect as netConnect } from "node:net";
import type { Duplex } from "node:stream";
import { lookup as dnsLookup } from "node:dns/promises";

import { evaluateHostName, evaluateResolvedAddress } from "./egress-policy";

export interface ResolvedAddress {
  readonly address: string;
  readonly family: number;
}

/** Resolve a host to ALL of its addresses (dns.lookup with `all: true`). */
export type DnsResolver = (host: string) => Promise<readonly ResolvedAddress[]>;

export interface ConnectDecision {
  readonly allowed: boolean;
  readonly reason: string;
  /** The single numeric address the tunnel must dial (anti-rebinding pin). */
  readonly pinnedAddress?: string;
  readonly pinnedFamily?: number;
}

export interface EgressProxyConfig {
  /** Approved exact-origin HOST names (derived from the origin policy). */
  readonly approvedHosts: ReadonlySet<string>;
  /** DNS resolver; defaults to node:dns lookup(all). Injected in tests. */
  readonly resolve?: DnsResolver;
  /** TCP dialer; defaults to node:net connect. Injected in tests. */
  readonly dial?: (opts: { host: string; port: number }) => Socket;
  /** Loopback/service ports that must never be dialled even if 443-shaped. */
  readonly deniedPorts?: ReadonlySet<number>;
}

const defaultResolver: DnsResolver = async (host) => {
  const records = await dnsLookup(host, { all: true });
  return records.map((r) => ({ address: r.address, family: r.family }));
};

export class EgressProxy {
  readonly #approvedHosts: ReadonlySet<string>;
  readonly #resolve: DnsResolver;
  readonly #dial: (opts: { host: string; port: number }) => Socket;
  readonly #deniedPorts: ReadonlySet<number>;
  #server: Server | null = null;
  #port = 0;

  constructor(config: EgressProxyConfig) {
    this.#approvedHosts = new Set(
      [...config.approvedHosts].map((h) => h.toLowerCase()),
    );
    this.#resolve = config.resolve ?? defaultResolver;
    this.#dial = config.dial ?? ((opts) => netConnect(opts));
    this.#deniedPorts = config.deniedPorts ?? new Set();
  }

  /**
   * The pure, tested decision core. Given a `CONNECT` target (`host:port`),
   * decide whether the tunnel may open and, if so, the single numeric address
   * to dial. Never dials; never mutates state.
   */
  async authorizeConnect(target: string): Promise<ConnectDecision> {
    const parsed = parseHostPort(target);
    if (parsed === null) return { allowed: false, reason: "bad_target" };
    const { host, port } = parsed;

    // Only the default HTTPS port. Non-default ports (incl. local app / broker
    // ports) are denied outright.
    if (port !== 443) return { allowed: false, reason: "port_denied" };
    if (this.#deniedPorts.has(port)) {
      return { allowed: false, reason: "port_denied" };
    }

    const lowerHost = host.toLowerCase();
    // Host must be an approved exact-origin host (defense in depth over the
    // navigate-time origin check).
    if (!this.#approvedHosts.has(lowerHost)) {
      return { allowed: false, reason: "host_not_approved" };
    }
    // Structural host check (metadata / local / single-label / IP literals).
    const shape = evaluateHostName(lowerHost);
    if (!shape.allowed) {
      return { allowed: false, reason: shape.reason };
    }

    // Resolve DNS ourselves and check EVERY address. One denied address denies
    // the whole connection (fail closed against split-horizon rebinding).
    let records: readonly ResolvedAddress[];
    try {
      records = await this.#resolve(lowerHost);
    } catch {
      return { allowed: false, reason: "dns_failed" };
    }
    if (records.length === 0) {
      return { allowed: false, reason: "dns_empty" };
    }
    let pinned: ResolvedAddress | null = null;
    for (const record of records) {
      const decision = evaluateResolvedAddress(record.address);
      if (!decision.allowed) {
        return { allowed: false, reason: decision.reason };
      }
      if (pinned === null) pinned = record;
    }
    // Unreachable given the length check, but keeps the type total.
    if (pinned === null) return { allowed: false, reason: "dns_empty" };
    return {
      allowed: true,
      reason: "ok",
      pinnedAddress: pinned.address,
      pinnedFamily: pinned.family,
    };
  }

  /** Bind the loopback proxy listener. Returns `127.0.0.1:<port>`. */
  async start(): Promise<{ host: string; port: number }> {
    if (this.#server !== null) throw new Error("egress proxy already running");
    const server = createServer((_req, res) => {
      // Plain HTTP (non-CONNECT) is never tunnelled — http top-level/subresource
      // is denied. Refuse with 405 rather than proxy anything.
      res.statusCode = 405;
      res.end();
    });
    server.on("connect", (req, clientSocket) => {
      void this.#handleConnect(req.url ?? "", clientSocket);
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
      throw new Error("egress proxy failed to bind");
    }
    this.#server = server;
    this.#port = address.port;
    return { host: "127.0.0.1", port: this.#port };
  }

  async stop(): Promise<void> {
    const server = this.#server;
    this.#server = null;
    this.#port = 0;
    if (server === null) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  get port(): number {
    return this.#port;
  }

  async #handleConnect(target: string, clientSocket: Duplex): Promise<void> {
    const decision = await this.authorizeConnect(target);
    if (!decision.allowed || decision.pinnedAddress === undefined) {
      // Origin-level reason only; never echo the full target back.
      clientSocket.write(
        "HTTP/1.1 403 Forbidden\r\n" +
          `X-Egress-Denied: ${decision.reason}\r\n\r\n`,
      );
      clientSocket.destroy();
      return;
    }
    // Dial the PINNED numeric address — not the hostname — so a DNS rebind
    // cannot redirect the tunnel to a denied address after this check.
    const upstream = this.#dial({ host: decision.pinnedAddress, port: 443 });
    upstream.on("connect", () => {
      clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      upstream.pipe(clientSocket);
      clientSocket.pipe(upstream);
    });
    upstream.on("error", () => {
      if (!clientSocket.destroyed) {
        clientSocket.write("HTTP/1.1 502 Bad Gateway\r\n\r\n");
        clientSocket.destroy();
      }
    });
    clientSocket.on("error", () => upstream.destroy());
  }
}

function parseHostPort(target: string): { host: string; port: number } | null {
  const trimmed = target.trim();
  if (trimmed === "") return null;
  // Bracketed IPv6: [::1]:443
  if (trimmed.startsWith("[")) {
    const close = trimmed.indexOf("]");
    if (close === -1) return null;
    const host = trimmed.slice(1, close);
    const rest = trimmed.slice(close + 1);
    if (!rest.startsWith(":")) return null;
    const port = Number(rest.slice(1));
    if (!Number.isInteger(port)) return null;
    return { host, port };
  }
  const idx = trimmed.lastIndexOf(":");
  if (idx === -1) return null;
  const host = trimmed.slice(0, idx);
  const port = Number(trimmed.slice(idx + 1));
  if (host === "" || !Number.isInteger(port)) return null;
  return { host, port };
}

/** Derive the approved host set from a set of canonical `https://host` origins. */
export function hostsFromOrigins(origins: Iterable<string>): Set<string> {
  const hosts = new Set<string>();
  for (const origin of origins) {
    try {
      hosts.add(new URL(origin).hostname.toLowerCase());
    } catch {
      // Skip malformed entries; they were already rejected at policy build.
    }
  }
  return hosts;
}
