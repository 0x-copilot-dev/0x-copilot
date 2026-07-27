// `ConnectorService.authorize` — the topology resolver.
//
// Two OAuth routes exist and they are not interchangeable: profile connectors
// carry a pre-registered client, everything else discovers + dynamically
// registers one. Which applies is decided by a backend-owned file, so the
// renderer was never in a position to choose — and when it chose (at three of
// five call sites) it chose the profile route for catalog seeds, which answers
// 404 BEFORE opening a browser. Connect looked like it worked and did nothing.
//
// These tests pin the resolution itself, because it is the only place that
// knowledge now lives.

import { describe, expect, it, vi } from "vitest";

import { ConnectorService } from "./connector-service";

/**
 * A service whose network is faked at the `fetch` seam and whose coordinator
 * flows are stubbed — the routes themselves are covered by
 * `oauth-coordinator.test.ts`; what matters here is WHICH one runs.
 */
function makeService(profileSlugs: string[]): {
  service: ConnectorService;
  connect: ReturnType<typeof vi.fn>;
  connectMcpServer: ReturnType<typeof vi.fn>;
  catalogFetches: () => number;
} {
  let catalogFetches = 0;
  const service = new ConnectorService({
    facadeBaseUrl: "http://127.0.0.1:8200",
    openExternal: vi.fn(async () => undefined),
    getBearer: vi.fn(async () => "bearer-token"),
    fetch: (async () => {
      catalogFetches += 1;
      return {
        ok: true,
        json: async () => ({
          entries: profileSlugs.map((slug) => ({ slug })),
        }),
      };
    }) as unknown as typeof fetch,
  });

  const connect = vi.fn(async (slug: string) => ({
    server_id: `desktop:${slug}`,
    connector_slug: slug,
    display_group: "Work",
    auth_state: "authenticated",
  }));
  const connectMcpServer = vi.fn(async () => undefined);
  // The coordinator is the seam being routed TO; replace both flows so the
  // assertion is about selection, not about OAuth mechanics.
  Object.assign(service.coordinator, { connect, connectMcpServer });

  return {
    service,
    connect,
    connectMcpServer,
    catalogFetches: () => catalogFetches,
  };
}

describe("ConnectorService.authorize", () => {
  it("routes a profile-backed slug through the pre-registered-client flow", async () => {
    const { service, connect, connectMcpServer } = makeService(["gmail"]);

    const result = await service.authorize({ slug: "gmail" });

    expect(connect).toHaveBeenCalledWith("gmail", { productScope: undefined });
    expect(connectMcpServer).not.toHaveBeenCalled();
    expect(result.server_id).toBe("desktop:gmail");
  });

  it("routes a catalog seed through MCP OAuth — the Linear regression", async () => {
    // Linear is in the marketing catalog but has no desktop profile. This is
    // the exact case that rendered a Connect button which opened nothing.
    const { service, connect, connectMcpServer } = makeService(["gmail"]);

    const result = await service.authorize({
      slug: "linear",
      serverId: "seed:linear",
    });

    expect(connect).not.toHaveBeenCalled();
    expect(connectMcpServer).toHaveBeenCalledWith("seed:linear");
    expect(result).toEqual({
      server_id: "seed:linear",
      connector_slug: "linear",
      // Nothing truthful to report: the server's own row is the record.
      auth_state: null,
    });
  });

  it("prefers the profile route when BOTH identities are known", async () => {
    // A profile connector needs its pre-registered client; the MCP route cannot
    // complete it. So a known profile slug wins over a server id rather than
    // the other way round.
    const { service, connect, connectMcpServer } = makeService(["gmail"]);

    await service.authorize({ slug: "gmail", serverId: "seed:gmail" });

    expect(connect).toHaveBeenCalledTimes(1);
    expect(connectMcpServer).not.toHaveBeenCalled();
  });

  it("authorizes a custom server that has no slug at all", async () => {
    const { service, connectMcpServer } = makeService(["gmail"]);

    const result = await service.authorize({ serverId: "custom:abc123" });

    expect(connectMcpServer).toHaveBeenCalledWith("custom:abc123");
    expect(result.connector_slug).toBeNull();
  });

  it("refuses an unresolvable slug instead of opening a doomed browser", async () => {
    // No profile and no server row: nothing to authorize. Saying so beats
    // handing the user a vendor page that cannot complete.
    const { service } = makeService(["gmail"]);

    await expect(service.authorize({ slug: "linear" })).rejects.toThrow(
      /no desktop profile/,
    );
  });

  it("resolves the profile set once, not per authorization", async () => {
    const { service, catalogFetches } = makeService(["gmail"]);

    await service.authorize({ slug: "gmail" });
    await service.authorize({ slug: "gmail" });

    expect(catalogFetches()).toBe(1);
  });

  it("does NOT cache an empty catalog", async () => {
    // `listCatalog` degrades to `{entries: []}` when signed out or on a failed
    // fetch. Caching that would misroute every profile connector down the MCP
    // path for the rest of the session — a silent, session-long regression
    // triggered by one unlucky moment.
    const { service, catalogFetches } = makeService([]);

    // A slug must be present or the lookup is skipped entirely (see below).
    await service.authorize({ slug: "gmail", serverId: "seed:gmail" });
    await service.authorize({ slug: "gmail", serverId: "seed:gmail" });

    expect(catalogFetches()).toBe(2);
  });

  it("skips the catalog entirely when there is no slug to look up", async () => {
    // A server-id-only authorize has nothing to resolve against the profile
    // overlay, so it should not pay for a fetch to learn that.
    const { service, catalogFetches } = makeService(["gmail"]);

    await service.authorize({ serverId: "custom:abc123" });

    expect(catalogFetches()).toBe(0);
  });
});
