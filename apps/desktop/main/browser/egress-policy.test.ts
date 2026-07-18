// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  evaluateHostName,
  evaluateIpv4,
  evaluateIpv6,
  evaluateResolvedAddress,
  evaluateUrlShape,
  parseIpv4,
  parseIpv6,
} from "./egress-policy";

const APPROVED = new Set(["https://example.com", "https://docs.example.org"]);

describe("evaluateUrlShape — schemes", () => {
  it("allows an approved https origin", () => {
    const d = evaluateUrlShape("https://example.com/path?q=1", APPROVED);
    expect(d.allowed).toBe(true);
    expect(d.origin).toBe("https://example.com");
  });

  it.each([
    ["file:///etc/passwd", "scheme_denied"],
    ["data:text/html,<h1>x", "scheme_denied"],
    ["blob:https://example.com/abc", "scheme_denied"],
    ["javascript:alert(1)", "scheme_denied"],
    ["about:blank", "scheme_denied"],
    ["chrome://settings", "scheme_denied"],
    ["view-source:https://example.com", "scheme_denied"],
    ["ftp://example.com", "scheme_denied"],
    ["http://example.com", "scheme_denied"],
  ])("denies scheme %s", (url, reason) => {
    const d = evaluateUrlShape(url, APPROVED);
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe(reason);
  });

  it("denies an unapproved https origin", () => {
    const d = evaluateUrlShape("https://evil.example.net", APPROVED);
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("origin_not_approved");
  });

  it("denies a non-default port even on an approved host", () => {
    const d = evaluateUrlShape("https://example.com:8443", APPROVED);
    expect(d.allowed).toBe(false);
  });
});

describe("evaluateHostName — metadata / local / IP literals", () => {
  it.each([
    ["metadata.google.internal", "metadata_host"],
    ["metadata", "metadata_host"],
    ["localhost", "local_host_name"],
    ["printer.local", "local_search_domain"],
    ["db.internal", "local_search_domain"],
    ["singlelabel", "single_label_host"],
  ])("denies host %s", (host, reason) => {
    const d = evaluateHostName(host);
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe(reason);
  });

  it("denies an IP-literal host that names a loopback address", () => {
    expect(evaluateHostName("127.0.0.1").allowed).toBe(false);
    expect(evaluateHostName("[::1]").allowed).toBe(false);
  });

  it("allows a normal public host name", () => {
    expect(evaluateHostName("example.com").allowed).toBe(true);
  });
});

describe("IPv4 denied ranges", () => {
  it.each([
    "0.0.0.0",
    "10.0.0.1",
    "100.64.0.1", // CGNAT
    "127.0.0.1",
    "169.254.169.254", // cloud metadata (link-local)
    "172.16.5.4",
    "192.168.1.1",
    "198.18.0.1", // benchmarking
    "192.0.2.5", // documentation
    "203.0.113.9", // documentation
    "224.0.0.1", // multicast
    "240.0.0.1", // reserved
    "255.255.255.255", // broadcast
  ])("denies %s", (addr) => {
    expect(evaluateResolvedAddress(addr).allowed).toBe(false);
  });

  it("allows a public v4 address", () => {
    expect(evaluateResolvedAddress("93.184.216.34").allowed).toBe(true);
  });

  it("denies loopback in integer, octal and hex spellings", () => {
    // 127.0.0.1 == 2130706433 == 0x7f000001 == 017700000001
    expect(parseIpv4("2130706433")).toBe(0x7f000001);
    expect(evaluateResolvedAddress("2130706433").allowed).toBe(false);
    expect(evaluateResolvedAddress("0x7f000001").allowed).toBe(false);
    expect(evaluateResolvedAddress("017700000001").allowed).toBe(false);
  });

  it("denies mixed octal/hex dotted labels for a private address", () => {
    // 0xa.0.0.1 -> 10.0.0.1
    expect(parseIpv4("0xa.0.0.1")).toBe(evaluateIpv4Base(10, 0, 0, 1));
    expect(evaluateResolvedAddress("0xa.0.0.1").allowed).toBe(false);
  });
});

describe("IPv6 denied ranges", () => {
  it.each([
    "::1", // loopback
    "::", // unspecified
    "fe80::1", // link-local
    "fc00::1", // unique-local
    "fd00::1", // unique-local
    "ff02::1", // multicast
    "2001:db8::1", // documentation
    "::ffff:127.0.0.1", // IPv4-mapped loopback
    "::ffff:10.0.0.1", // IPv4-mapped private
    "::ffff:169.254.169.254", // IPv4-mapped metadata
  ])("denies %s", (addr) => {
    expect(evaluateResolvedAddress(addr).allowed).toBe(false);
  });

  it("allows a public v6 address", () => {
    expect(
      evaluateResolvedAddress("2606:2800:220:1:248:1893:25c8:1946").allowed,
    ).toBe(true);
  });

  it("parses and denies a mixed-case, zone-suffixed link-local", () => {
    expect(evaluateResolvedAddress("FE80::1%en0").allowed).toBe(false);
    expect(parseIpv6("::1")).toEqual([0, 0, 0, 0, 0, 0, 0, 1]);
  });

  it("classifies IPv4-mapped public as allowed", () => {
    expect(evaluateIpv6("::ffff:93.184.216.34").allowed).toBe(true);
  });
});

describe("fail-closed", () => {
  it("denies an unparseable address", () => {
    expect(evaluateResolvedAddress("not-an-ip").allowed).toBe(false);
    expect(evaluateResolvedAddress("999.999.999.999").allowed).toBe(false);
  });
});

function evaluateIpv4Base(a: number, b: number, c: number, d: number): number {
  return (((a << 24) | (b << 16) | (c << 8) | d) >>> 0) as number;
}

// Guard: evaluateIpv4 is exercised indirectly; assert a couple of direct cases.
describe("evaluateIpv4 direct", () => {
  it("denies 0.0.0.0/8 and allows a public host", () => {
    expect(evaluateIpv4(0).allowed).toBe(false);
    expect(evaluateIpv4(evaluateIpv4Base(93, 184, 216, 34)).allowed).toBe(true);
  });
});
