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
  options: {
    readonly installStatus?: number;
    /** Rows that already exist in `mcp_servers` for this user. */
    readonly existingServerIds?: readonly string[];
    /** Fail the server listing, to pin the degradation path. */
    readonly listServersFails?: boolean;
  } = {},
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
      if (url.endsWith("/v1/mcp/servers")) {
        if (options.listServersFails === true) {
          return { ok: false, status: 503 };
        }
        return {
          ok: true,
          json: async () => ({
            servers: (options.existingServerIds ?? []).map((id) => ({
              server_id: id,
            })),
          }),
        };
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

    expect(connect).toHaveBeenCalledWith(
      "gmail",
      expect.objectContaining({ productScope: undefined }),
    );
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
    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:linear",
      expect.any(Object),
    );
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
    // this scope" — suggesting a connector never installed it.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    await service.authorize({ slug: "linear", serverId: "seed:linear" });

    expect(installed()).toEqual(["linear"]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:linear",
      expect.any(Object),
    );
  });

  it("does NOT re-install a server row that already exists", async () => {
    // The composer's Connect installs first and hands `authorize` the id it
    // just minted. Installing again was a second POST for a row we were
    // already holding the id of — 2×200 per connect in the desktop logs.
    const { service, connectMcpServer, installed } = makeService(["gmail"], {
      existingServerIds: ["seed:linear"],
    });

    await service.authorize({ slug: "linear", serverId: "seed:linear" });

    expect(installed()).toEqual([]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:linear",
      expect.any(Object),
    );
  });

  it("does NOT install a CUSTOM server's slug — the phantom 404", async () => {
    // A custom server added by URL has a real row and no catalog entry, so
    // "installing its slug" asked for a seed that does not exist and answered
    // 404. Twice per connect, at warning level, in the one path where a real
    // failure has to stand out.
    const { service, connectMcpServer, installed } = makeService(["gmail"], {
      existingServerIds: ["custom:abc123"],
    });

    await service.authorize({ slug: "my-server", serverId: "custom:abc123" });

    expect(installed()).toEqual([]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "custom:abc123",
      expect.any(Object),
    );
  });

  it("still installs when the id is known but its row does not exist", async () => {
    // `seed:<slug>` is the catalog IDENTITY, not proof of a row. A discovery
    // suggestion carries one for a connector that was never installed, so
    // confirming absence has to send it to install rather than authorize a
    // row that is not there.
    const { service, connectMcpServer, installed } = makeService(["gmail"], {
      existingServerIds: ["seed:notion"],
    });

    await service.authorize({ slug: "linear", serverId: "seed:linear" });

    expect(installed()).toEqual(["linear"]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:linear",
      expect.any(Object),
    );
  });

  it("degrades to installing when the server listing cannot be read", async () => {
    // An unreadable listing is not evidence either way, so it falls back to
    // the previous behaviour rather than to a guess: claiming "exists" would
    // authorize a row that may never have been minted.
    const { service, installed } = makeService(["gmail"], {
      listServersFails: true,
    });

    await service.authorize({ slug: "linear", serverId: "seed:linear" });

    expect(installed()).toEqual(["linear"]);
  });

  it("still connects a custom server when install 404s on its slug", async () => {
    // The degraded path: listing unreadable, so a custom server reaches
    // install after all, and its host-derived slug is not a catalog entry. A
    // 404 only means "the catalog does not know this slug" — with a real row
    // already in hand it is not an error, and treating it as fatal threw
    // before a browser ever opened.
    const { service, connectMcpServer } = makeService(["gmail"], {
      listServersFails: true,
      installStatus: 404,
    });

    const result = await service.authorize({
      slug: "api_githubcopilot_com",
      serverId: "custom:abc123",
    });

    expect(connectMcpServer).toHaveBeenCalledWith(
      "custom:abc123",
      expect.any(Object),
    );
    expect(result.server_id).toBe("custom:abc123");
  });

  it("still refuses a 404 install when there is no row to fall back to", async () => {
    // Without a known server id a 404 is terminal: no row exists, so there is
    // nothing to authorize and opening a browser would be a lie.
    const { service, connectMcpServer } = makeService(["gmail"], {
      installStatus: 404,
    });

    await expect(service.authorize({ slug: "nonexistent" })).rejects.toThrow(
      /install failed/,
    );
    expect(connectMcpServer).not.toHaveBeenCalled();
  });

  it("authorizes the id the INSTALL returned, not the one passed in", async () => {
    // The caller's id can be stale for an uninstalled suggestion; the backend
    // is the authority on what row now exists.
    const { service, connectMcpServer } = makeService(["gmail"]);

    await service.authorize({ slug: "notion", serverId: "stale:notion" });

    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:notion",
      expect.any(Object),
    );
  });

  it("does NOT install for a custom server that has no slug", async () => {
    // A custom server added by URL already has its row and no catalog entry to
    // install; trying would 404 on an unknown slug.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    await service.authorize({ serverId: "custom:abc123" });

    expect(installed()).toEqual([]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "custom:abc123",
      expect.any(Object),
    );
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

    expect(connectMcpServer).toHaveBeenCalledWith(
      "custom:abc123",
      expect.any(Object),
    );
    expect(result.connector_slug).toBeNull();
  });

  it("resolves a slug with no server id at all, by installing it", async () => {
    // This used to throw, because a seed had to arrive with an id already
    // attached. Install mints the row, so the slug alone is now enough — which
    // is what makes the composer's install-then-connect path one call.
    const { service, connectMcpServer, installed } = makeService(["gmail"]);

    const result = await service.authorize({ slug: "linear" });

    expect(installed()).toEqual(["linear"]);
    expect(connectMcpServer).toHaveBeenCalledWith(
      "seed:linear",
      expect.any(Object),
    );
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
});

// ---------------------------------------------------------------------------
// Cancellation — the single pending slot
// ---------------------------------------------------------------------------
//
// One connect is in flight at a time, so the service holds exactly one abort,
// newest-wins. That mirrors `AuthService`'s pending sign-in slot, and the
// identity check on the way out is the part worth pinning: a finishing connect
// must not clear a NEWER connect's abort, or the newer one silently becomes
// uncancellable.

describe("ConnectorService — cancelPendingAuthorize", () => {
  /**
   * A service whose MCP flow parks forever — like a real connect waiting on a
   * redirect — and records each attempt as it hands out its cancel. Tests wait
   * on `started.length` rather than counting microtasks, because `authorize`
   * does real awaits (catalog probe, server listing) before it gets there.
   */
  function makePausableService(): {
    service: ConnectorService;
    started: Array<(reason?: "user" | "superseded") => void>;
  } {
    const { service } = makeService([], { existingServerIds: ["seed:linear"] });
    const started: Array<(reason?: "user" | "superseded") => void> = [];
    Object.assign(service.coordinator, {
      connectMcpServer: (
        _id: string,
        options: {
          onCancelAvailable?: (
            cancel: (reason?: "user" | "superseded") => void,
          ) => void;
        } = {},
      ) =>
        new Promise<void>((_resolve, reject) => {
          // The fake honours the REASON rather than hardcoding one message —
          // otherwise it would pass whatever the service passed and these tests
          // could not tell a supersede from a cancel at all.
          const abort = (reason: "user" | "superseded" = "user"): void =>
            reject(
              new Error(
                reason === "superseded"
                  ? "connect superseded"
                  : "connect cancelled",
              ),
            );
          options.onCancelAvailable?.(abort);
          started.push(abort);
        }),
    });
    return { service, started };
  }

  it("is a no-op when nothing is pending", () => {
    const { service } = makeService(["gmail"]);
    // A Cancel that races the connect's own completion must be harmless, so
    // this may not throw.
    expect(() => service.cancelPendingAuthorize()).not.toThrow();
    expect(() => service.cancelPendingAuthorize()).not.toThrow();
  });

  it("aborts the connect awaiting a redirect", async () => {
    const { service, started } = makePausableService();
    const pending = service.authorize({
      slug: "linear",
      serverId: "seed:linear",
    });
    await vi.waitFor(() => expect(started).toHaveLength(1));

    service.cancelPendingAuthorize();

    await expect(pending).rejects.toThrow(/connect cancelled/);
  });

  it("lets a second connect abort the first — newest wins", async () => {
    const { service, started } = makePausableService();
    // Assertions are attached the moment each promise exists. Starting the
    // second connect rejects the first synchronously, so waiting to attach
    // would surface a real rejection as an unhandled one.
    const first = service.authorize({
      slug: "linear",
      serverId: "seed:linear",
    });
    // SUPERSEDED, not cancelled. The user did not stop this one — the app did,
    // to give its slot to the next connect — and the renderer branches on the
    // difference: a cancel stays quiet because the user already knows, while a
    // supersede reported as a cancel told them a connector they had just
    // started had failed.
    const firstRejects = expect(first).rejects.toThrow(/connect superseded/);
    await vi.waitFor(() => expect(started).toHaveLength(1));

    const second = service.authorize({
      slug: "linear",
      serverId: "seed:linear",
    });
    const secondRejects = expect(second).rejects.toThrow(/connect cancelled/);
    await vi.waitFor(() => expect(started).toHaveLength(2));

    // The first is unreachable once a second browser flow owns the screen, so
    // it is aborted rather than left holding a loopback port for five minutes.
    await firstRejects;
    // The second is still live; cancelling it now must reach IT, not the slot
    // the first one left behind.
    service.cancelPendingAuthorize();
    await secondRejects;
  });
});
