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
function makeService(
  profileSlugs: string[],
  options: { readonly installStatus?: number } = {},
): {
  service: ConnectorService;
  connect: ReturnType<typeof vi.fn>;
  connectMcpServer: ReturnType<typeof vi.fn>;
  catalogFetches: () => number;
  installed: () => string[];
} {
  let catalogFetches = 0;
  const installed: string[] = [];
  const service = new ConnectorService({
    facadeBaseUrl: "http://127.0.0.1:8200",
    openExternal: vi.fn(async () => undefined),
    getBearer: vi.fn(async () => "bearer-token"),
    fetch: (async (url: string, init?: { body?: string }) => {
      if (url.includes("/v1/mcp/servers/install")) {
        const slug = JSON.parse(init?.body ?? "{}").slug as string;
        installed.push(slug);
        if (options.installStatus !== undefined) {
          return { ok: false, status: options.installStatus };
        }
        // Mirrors the backend: a catalog install mints `seed:<slug>` and is
        // idempotent, returning the existing row on repeat.
        return { ok: true, json: async () => ({ server_id: `seed:${slug}` }) };
      }
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
    installed: () => installed,
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

  it("installs the catalog seed before authorizing it", async () => {
    // The row does not exist until install mints it. Authorizing `seed:linear`
    // straight off a discovery suggestion answers "MCP server was not found for
    // this scope" — suggesting a connector never installed it. Install is
    // idempotent on slug, so this runs unconditionally rather than behind a
    // lookup.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    await service.authorize({ slug: "linear", serverId: "seed:linear" });

    expect(installed()).toEqual(["linear"]);
    expect(connectMcpServer).toHaveBeenCalledWith("seed:linear");
  });

  it("authorizes the id the INSTALL returned, not the one passed in", async () => {
    // The caller's id can be stale for an uninstalled suggestion; the backend
    // is the authority on what row now exists.
    const { service, connectMcpServer } = makeService(["gmail"]);

    await service.authorize({ slug: "notion", serverId: "stale:notion" });

    expect(connectMcpServer).toHaveBeenCalledWith("seed:notion");
  });

  it("does NOT install for a custom server that has no slug", async () => {
    // A custom server added by URL already has its row and no catalog entry to
    // install; trying would 404 on an unknown slug.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    await service.authorize({ serverId: "custom:abc123" });

    expect(installed()).toEqual([]);
    expect(connectMcpServer).toHaveBeenCalledWith("custom:abc123");
  });

  it("never installs on the profile route", async () => {
    // Profile connectors are provisioned by the overlay, not the MCP catalog.
    const { service, installed } = makeService(["gmail"]);

    await service.authorize({ slug: "gmail" });

    expect(installed()).toEqual([]);
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

  it("resolves a slug with no server id at all, by installing it", async () => {
    // This used to throw, because a seed had to arrive with an id already
    // attached. Install mints the row, so the slug alone is now enough — which
    // is what makes the composer's install-then-connect path one call.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    const result = await service.authorize({ slug: "linear" });

    expect(installed()).toEqual(["linear"]);
    expect(connectMcpServer).toHaveBeenCalledWith("seed:linear");
    expect(result.server_id).toBe("seed:linear");
  });

  it("refuses when the install itself fails, instead of opening a doomed browser", async () => {
    // An unknown slug, or an entry needing a pre-registered client (422). No
    // row can exist, so there is nothing to authorize — saying so beats handing
    // the user a vendor page that cannot complete.
    const { service, connectMcpServer } = makeService(["gmail"], {
      installStatus: 422,
    });

    await expect(service.authorize({ slug: "linear" })).rejects.toThrow(
      /install failed/,
    );
    expect(connectMcpServer).not.toHaveBeenCalled();
  });

  it("refuses when there is neither a slug nor a server id", async () => {
    const { service } = makeService(["gmail"]);

    await expect(service.authorize({})).rejects.toThrow(/no desktop profile/);
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

  it("authorizes a custom server by its own id when the catalog has no such slug", async () => {
    // The api.githubcopilot.com regression, and the gap that let it ship: a
    // register-by-URL server has a REAL registry row plus a host-derived slug
    // (`api_githubcopilot_com`) the curated catalog has never heard of, so
    // install answers 404 `Unknown catalog entry`. That 404 used to abort
    // Connect even though the caller had already resolved the row's id from
    // `listServers()` — the OAuth flow never started and no browser opened.
    const { service, connectMcpServer } = makeService(["gmail"], {
      installStatus: 404,
    });

    const result = await service.authorize({
      slug: "api_githubcopilot_com",
      serverId: "mcp_7f3c",
    });

    expect(connectMcpServer).toHaveBeenCalledWith("mcp_7f3c");
    expect(result.server_id).toBe("mcp_7f3c");
  });

  it("still refuses a 404 install when there is no row to fall back to", async () => {
    // Without an id, an unknown slug leaves nothing to authorize. Saying so
    // beats opening a browser at something that cannot finish.
    const { service, connectMcpServer } = makeService(["gmail"], {
      installStatus: 404,
    });

    await expect(
      service.authorize({ slug: "api_githubcopilot_com" }),
    ).rejects.toThrow(/install failed/);
    expect(connectMcpServer).not.toHaveBeenCalled();
  });

  it("still refuses a 422 install even when a row id IS known", async () => {
    // 422 is the pre-registered-client gate — a catalog entry that genuinely
    // cannot complete over this route. Unlike 404 it says nothing about
    // whether the catalog knows the slug, so no fallback id rescues it.
    const { service, connectMcpServer } = makeService(["gmail"], {
      installStatus: 422,
    });

    await expect(
      service.authorize({ slug: "linear", serverId: "seed:linear" }),
    ).rejects.toThrow(/install failed/);
    expect(connectMcpServer).not.toHaveBeenCalled();
  });
});
