// AC8 agentic browser — deny-by-default egress policy (pure decisions).
//
// This module is the SECURITY HEART of the browser capability. It answers two
// independent questions with no I/O of its own:
//
//   1. Is a URL's SHAPE allowed? (scheme https-only, host not a metadata name /
//      `.local` / single-label, origin in the approved exact-origin set).
//   2. Is a RESOLVED ADDRESS allowed? (the denied-range table: loopback,
//      private, link-local, CGNAT, multicast, unspecified, benchmarking,
//      documentation, reserved, IPv4-mapped IPv6, cloud metadata).
//
// DNS rebinding is defeated by the CALLER (network-policy-proxy.ts): it resolves
// DNS ITSELF, calls `evaluateResolvedAddress` on EVERY resolved address, pins a
// single permitted address for that connection, and re-runs the whole check on
// reconnect/redirect. This module only decides; it never resolves or dials.
//
// Fail-closed everywhere: an unparseable host, an unknown scheme, or any
// resolved address that falls in (or that we cannot prove falls OUTSIDE) a
// denied range is DENIED.

import { canonicalizeOrigin, isIpLiteral } from "./protocol";

export interface EgressDecision {
  readonly allowed: boolean;
  /** Safe, origin-level reason. Never leaks a full URL / query / fragment. */
  readonly reason: string;
}

const ALLOW: EgressDecision = { allowed: true, reason: "ok" } as const;

function deny(reason: string): EgressDecision {
  return { allowed: false, reason };
}

// --- Denied host-name suffixes / exact names ------------------------------

/** Cloud metadata + platform identity host names (and known aliases). */
const DENIED_METADATA_HOSTS: ReadonlySet<string> = new Set([
  "metadata.google.internal",
  "metadata",
  "metadata.goog",
  "instance-data",
  "instance-data.ec2.internal",
]);

/** Denied host-name suffixes: mDNS / local search domains. */
const DENIED_HOST_SUFFIXES: readonly string[] = [
  ".local",
  ".internal",
  ".localhost",
];

const DENIED_EXACT_HOSTS: ReadonlySet<string> = new Set([
  "localhost",
  "localhost.localdomain",
  "broadcasthost",
]);

// --- Public: URL shape check ----------------------------------------------

/**
 * Decide whether a URL's SHAPE is permitted for a TOP-LEVEL navigation, given
 * the approved exact-origin set. This does NOT resolve DNS — the proxy does
 * that. Returns the canonical origin on success via `origin`.
 */
export function evaluateUrlShape(
  rawUrl: string,
  approvedOrigins: ReadonlySet<string>,
): EgressDecision & { origin?: string } {
  const url = tryParseUrl(rawUrl);
  if (url === null) return deny("unparseable_url");

  // Only https top-level targets. Everything else (file/data/blob/javascript/
  // about/chrome/chrome-extension/devtools/view-source/ftp/custom) is denied.
  if (url.protocol !== "https:") return deny("scheme_denied");
  if (url.username !== "" || url.password !== "")
    return deny("userinfo_denied");
  // Explicit non-default port is denied (443 normalizes to empty).
  if (url.port !== "" && url.port !== "443") return deny("port_denied");

  const host = normalizeHost(url.hostname);
  const hostDecision = evaluateHostName(host);
  if (!hostDecision.allowed) return hostDecision;

  // The canonical exact origin is scheme+host only — path/query/fragment are
  // ignored for the approval decision.
  const origin = `https://${host}`;
  if (canonicalizeOrigin(origin) !== origin) return deny("noncanonical_origin");
  if (!approvedOrigins.has(origin)) {
    return deny("origin_not_approved");
  }
  return { ...ALLOW, origin };
}

/**
 * Decide whether a HOST NAME (already lowercased) is structurally permitted —
 * i.e. not a metadata name, mDNS/`.local` name, single-label name, or IP
 * literal that names a denied range. A NAME that is an IP literal is routed
 * through the resolved-address table so `https://127.0.0.1` is denied even
 * though no DNS lookup happens.
 */
export function evaluateHostName(host: string): EgressDecision {
  if (host === "") return deny("empty_host");
  if (DENIED_EXACT_HOSTS.has(host)) return deny("local_host_name");
  if (DENIED_METADATA_HOSTS.has(host)) return deny("metadata_host");
  const bareHost = host.endsWith(".") ? host.slice(0, -1) : host;
  for (const suffix of DENIED_HOST_SUFFIXES) {
    if (bareHost.endsWith(suffix)) return deny("local_search_domain");
  }
  // Single-label names (no dot) are denied for top-level use.
  if (!bareHost.includes(".") && !isIpLiteral(bareHost)) {
    return deny("single_label_host");
  }
  // If the host is itself an IP literal, classify it directly.
  if (isIpLiteral(bareHost)) {
    return evaluateResolvedAddress(bareHost);
  }
  return ALLOW;
}

// --- Public: resolved-address check ---------------------------------------

/**
 * Classify a RESOLVED address (an IP string handed back by DNS, or an IP
 * literal host). Denies loopback, private, link-local, CGNAT, multicast,
 * unspecified, benchmarking, documentation, reserved, and IPv4-mapped IPv6
 * forms. Fails closed for anything it cannot parse.
 */
export function evaluateResolvedAddress(address: string): EgressDecision {
  const addr =
    address.startsWith("[") && address.endsWith("]")
      ? address.slice(1, -1)
      : address;
  // Strip an IPv6 zone id (fe80::1%en0).
  const zoneless = addr.split("%")[0];

  if (zoneless.includes(":")) {
    return evaluateIpv6(zoneless);
  }
  const v4 = parseIpv4(zoneless);
  if (v4 === null) return deny("unparseable_address");
  return evaluateIpv4(v4);
}

// --- IPv4 parsing (dotted / integer / octal / hex) ------------------------

/**
 * Parse any of the IPv4 spellings a URL host can smuggle — dotted-decimal,
 * a bare 32-bit integer, octal (`0177...`), or hex (`0x7f...`) — into a 32-bit
 * unsigned number. Returns null when it is not an IPv4 form.
 */
export function parseIpv4(host: string): number | null {
  const h = host.trim();
  if (h === "") return null;

  // Bare hex integer: 0x7f000001
  if (/^0x[0-9a-f]+$/iu.test(h)) {
    const n = Number.parseInt(h, 16);
    return n >= 0 && n <= 0xffffffff ? n >>> 0 : null;
  }
  // Bare octal integer: 017700000001
  if (/^0[0-7]+$/u.test(h)) {
    const n = Number.parseInt(h, 8);
    return n >= 0 && n <= 0xffffffff ? n >>> 0 : null;
  }
  // Bare decimal integer: 2130706433
  if (/^\d+$/u.test(h)) {
    const n = Number(h);
    return Number.isInteger(n) && n >= 0 && n <= 0xffffffff ? n >>> 0 : null;
  }
  // Dotted form: each label may itself be decimal/octal/hex.
  const labels = h.split(".");
  if (labels.length !== 4) return null;
  let result = 0;
  for (const label of labels) {
    const octet = parseIpLabel(label);
    if (octet === null || octet > 255) return null;
    result = (result << 8) | octet;
  }
  return result >>> 0;
}

function parseIpLabel(label: string): number | null {
  if (label === "") return null;
  if (/^0x[0-9a-f]+$/iu.test(label)) return Number.parseInt(label, 16);
  if (/^0[0-7]+$/u.test(label)) return Number.parseInt(label, 8);
  if (/^\d+$/u.test(label)) return Number(label);
  return null;
}

interface Cidr4 {
  readonly base: number;
  readonly bits: number;
  readonly reason: string;
}

const DENIED_IPV4: readonly Cidr4[] = [
  { base: ip4(0, 0, 0, 0), bits: 8, reason: "unspecified" },
  { base: ip4(10, 0, 0, 0), bits: 8, reason: "private" },
  { base: ip4(100, 64, 0, 0), bits: 10, reason: "cgnat" },
  { base: ip4(127, 0, 0, 0), bits: 8, reason: "loopback" },
  { base: ip4(169, 254, 0, 0), bits: 16, reason: "link_local" },
  { base: ip4(172, 16, 0, 0), bits: 12, reason: "private" },
  { base: ip4(192, 0, 0, 0), bits: 24, reason: "ietf_protocol" },
  { base: ip4(192, 0, 2, 0), bits: 24, reason: "documentation" },
  { base: ip4(192, 88, 99, 0), bits: 24, reason: "6to4_relay" },
  { base: ip4(192, 168, 0, 0), bits: 16, reason: "private" },
  { base: ip4(198, 18, 0, 0), bits: 15, reason: "benchmarking" },
  { base: ip4(198, 51, 100, 0), bits: 24, reason: "documentation" },
  { base: ip4(203, 0, 113, 0), bits: 24, reason: "documentation" },
  { base: ip4(224, 0, 0, 0), bits: 4, reason: "multicast" },
  { base: ip4(240, 0, 0, 0), bits: 4, reason: "reserved" },
];

export function evaluateIpv4(value: number): EgressDecision {
  const n = value >>> 0;
  if (n === 0xffffffff) return deny("broadcast");
  for (const cidr of DENIED_IPV4) {
    const mask = cidr.bits === 0 ? 0 : (0xffffffff << (32 - cidr.bits)) >>> 0;
    if ((n & mask) === (cidr.base & mask)) return deny(cidr.reason);
  }
  return ALLOW;
}

function ip4(a: number, b: number, c: number, d: number): number {
  return (((a << 24) | (b << 16) | (c << 8) | d) >>> 0) as number;
}

// --- IPv6 parsing ----------------------------------------------------------

/**
 * Parse an IPv6 address (including `::` compression and an embedded IPv4 tail)
 * into eight 16-bit groups, or null. Fails closed on anything malformed.
 */
export function parseIpv6(input: string): number[] | null {
  let text = input.trim().toLowerCase();
  if (text === "") return null;

  // Embedded IPv4 tail (e.g. ::ffff:127.0.0.1) — expand to two hextets.
  const lastColon = text.lastIndexOf(":");
  const tail = text.slice(lastColon + 1);
  if (tail.includes(".")) {
    const v4 = parseIpv4(tail);
    if (v4 === null) return null;
    const hi = (v4 >>> 16) & 0xffff;
    const lo = v4 & 0xffff;
    text =
      text.slice(0, lastColon + 1) + hi.toString(16) + ":" + lo.toString(16);
  }

  const halves = text.split("::");
  if (halves.length > 2) return null;

  const parseGroups = (s: string): number[] | null => {
    if (s === "") return [];
    const parts = s.split(":");
    const out: number[] = [];
    for (const p of parts) {
      if (!/^[0-9a-f]{1,4}$/u.test(p)) return null;
      out.push(Number.parseInt(p, 16));
    }
    return out;
  };

  if (halves.length === 2) {
    const head = parseGroups(halves[0]);
    const rear = parseGroups(halves[1]);
    if (head === null || rear === null) return null;
    const missing = 8 - head.length - rear.length;
    if (missing < 0) return null;
    return [...head, ...Array(missing).fill(0), ...rear];
  }
  const groups = parseGroups(text);
  if (groups === null || groups.length !== 8) return null;
  return groups;
}

export function evaluateIpv6(input: string): EgressDecision {
  const g = parseIpv6(input);
  if (g === null) return deny("unparseable_address");

  // Unspecified ::
  if (g.every((x) => x === 0)) return deny("unspecified");
  // Loopback ::1
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) {
    return deny("loopback");
  }
  // IPv4-mapped ::ffff:a.b.c.d  -> classify the embedded v4.
  if (g.slice(0, 5).every((x) => x === 0) && g[5] === 0xffff) {
    const v4 = ((g[6] << 16) | g[7]) >>> 0;
    const inner = evaluateIpv4(v4);
    return inner.allowed ? inner : deny(`mapped_${inner.reason}`);
  }
  // IPv4-compatible ::a.b.c.d (deprecated) -> classify embedded v4.
  if (g.slice(0, 6).every((x) => x === 0) && (g[6] !== 0 || g[7] !== 0)) {
    const v4 = ((g[6] << 16) | g[7]) >>> 0;
    const inner = evaluateIpv4(v4);
    return inner.allowed ? inner : deny(`compat_${inner.reason}`);
  }
  const first = g[0];
  // Link-local fe80::/10
  if ((first & 0xffc0) === 0xfe80) return deny("link_local");
  // Unique-local fc00::/7
  if ((first & 0xfe00) === 0xfc00) return deny("unique_local");
  // Multicast ff00::/8
  if ((first & 0xff00) === 0xff00) return deny("multicast");
  // Discard-only / documentation 2001:db8::/32
  if (g[0] === 0x2001 && g[1] === 0x0db8) return deny("documentation");
  return ALLOW;
}

/** Lowercase a host and strip a single trailing dot (root-label normalization). */
function normalizeHost(host: string): string {
  const lower = host.toLowerCase();
  return lower.endsWith(".") ? lower.slice(0, -1) : lower;
}

function tryParseUrl(raw: string): URL | null {
  try {
    return new URL(raw.trim());
  } catch {
    return null;
  }
}
