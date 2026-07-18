// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  EgressProxy,
  hostsFromOrigins,
  type ResolvedAddress,
} from "./network-policy-proxy";

const APPROVED = new Set(["example.com", "docs.example.org"]);

function resolverOf(
  map: Record<string, ResolvedAddress[]>,
): (host: string) => Promise<readonly ResolvedAddress[]> {
  return (host) => {
    const records = map[host];
    if (records === undefined) return Promise.reject(new Error("NXDOMAIN"));
    return Promise.resolve(records);
  };
}

describe("EgressProxy.authorizeConnect", () => {
  it("allows an approved host resolving to a public address and pins it", async () => {
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({
        "example.com": [{ address: "93.184.216.34", family: 4 }],
      }),
    });
    const d = await proxy.authorizeConnect("example.com:443");
    expect(d.allowed).toBe(true);
    expect(d.pinnedAddress).toBe("93.184.216.34");
  });

  it("denies DNS rebinding: an approved host that resolves to a private address", async () => {
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({
        "example.com": [{ address: "127.0.0.1", family: 4 }],
      }),
    });
    const d = await proxy.authorizeConnect("example.com:443");
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("loopback");
    expect(d.pinnedAddress).toBeUndefined();
  });

  it("denies a split-horizon answer with one public and one metadata address", async () => {
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({
        "example.com": [
          { address: "93.184.216.34", family: 4 },
          { address: "169.254.169.254", family: 4 },
        ],
      }),
    });
    const d = await proxy.authorizeConnect("example.com:443");
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("link_local");
  });

  it("denies a rebind to a mapped-IPv6 private address", async () => {
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({
        "example.com": [{ address: "::ffff:10.0.0.5", family: 6 }],
      }),
    });
    const d = await proxy.authorizeConnect("example.com:443");
    expect(d.allowed).toBe(false);
  });

  it("denies a non-443 port", async () => {
    const proxy = new EgressProxy({ approvedHosts: APPROVED });
    const d = await proxy.authorizeConnect("example.com:8080");
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("port_denied");
  });

  it("denies a host not in the approved set (never resolves it)", async () => {
    let resolved = false;
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: () => {
        resolved = true;
        return Promise.resolve([{ address: "93.184.216.34", family: 4 }]);
      },
    });
    const d = await proxy.authorizeConnect("evil.example.net:443");
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("host_not_approved");
    expect(resolved).toBe(false);
  });

  it("denies a metadata host name even if approved by mistake", async () => {
    const proxy = new EgressProxy({
      approvedHosts: new Set(["metadata.google.internal"]),
    });
    const d = await proxy.authorizeConnect("metadata.google.internal:443");
    expect(d.allowed).toBe(false);
    expect(d.reason).toBe("metadata_host");
  });

  it("fails closed when DNS errors or returns nothing", async () => {
    const proxy = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({ "example.com": [] }),
    });
    expect((await proxy.authorizeConnect("example.com:443")).reason).toBe(
      "dns_empty",
    );

    const proxy2 = new EgressProxy({
      approvedHosts: APPROVED,
      resolve: resolverOf({}),
    });
    expect((await proxy2.authorizeConnect("example.com:443")).reason).toBe(
      "dns_failed",
    );
  });
});

describe("hostsFromOrigins", () => {
  it("extracts lowercased hostnames from canonical origins", () => {
    const hosts = hostsFromOrigins([
      "https://Example.com",
      "https://docs.example.org",
    ]);
    expect(hosts.has("example.com")).toBe(true);
    expect(hosts.has("docs.example.org")).toBe(true);
  });
});
