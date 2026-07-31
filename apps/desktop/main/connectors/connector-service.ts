// AC9 — desktop connector service (Electron main).
//
// The IPC-facing seam the renderer reaches through `connector.*` channels. It
// owns nothing secret: it forwards the reconciled catalog from the facade and
// drives the OAuth connect flow through `ConnectorOAuthCoordinator`. Every
// value it returns to the renderer is safe by construction — the catalog and
// the post-connect metadata carry no provider token or client secret (those
// stay in the backend TokenVault).

import type {
  DesktopConnectorCatalogResponse,
  DesktopRequestedProductScope,
} from "@0x-copilot/api-types";

import type { ConnectorAuthorizationResult } from "./channels";
import {
  ConnectorOAuthCoordinator,
  ConnectorOAuthError,
  type ConnectorOAuthDeps,
} from "./oauth-coordinator";

export interface ConnectorServiceDeps extends ConnectorOAuthDeps {}

// `ConnectorAuthorizationResult` is declared in `./channels` — the one connector
// module the renderer is allowed to import — so both sides of the IPC read the
// same shape without pulling this file into the renderer bundle.
export type { ConnectorAuthorizationResult };

export class ConnectorService {
  private readonly facadeBaseUrl: string;
  private readonly getBearer: () => Promise<string | null>;
  private readonly doFetch: typeof fetch;
  /** Profile-backed slugs, resolved once per boot. `null` = not yet known. */
  private profileSlugs: Set<string> | null = null;
  readonly coordinator: ConnectorOAuthCoordinator;

  constructor(deps: ConnectorServiceDeps) {
    this.facadeBaseUrl = trimTrailingSlash(deps.facadeBaseUrl);
    this.getBearer = deps.getBearer;
    this.doFetch = deps.fetch ?? globalThis.fetch.bind(globalThis);
    this.coordinator = new ConnectorOAuthCoordinator(deps);
  }

  /** Renderer → main deep-link demux hook. Returns true iff a connector owned
   *  the state; the caller lets non-owners fall through to app-login. */
  handleDeepLinkCallback(code: string, state: string): boolean {
    return this.coordinator.handleDeepLinkCallback(code, state);
  }

  /** Fetch the reconciled desktop catalog (safe, read-only). */
  async listCatalog(): Promise<DesktopConnectorCatalogResponse> {
    const bearer = await this.getBearer();
    if (bearer === null) return { entries: [] };
    const response = await this.doFetch(
      `${this.facadeBaseUrl}/v1/connectors/desktop/catalog`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
      },
    );
    if (!response.ok) {
      return { entries: [] };
    }
    return (await response.json()) as DesktopConnectorCatalogResponse;
  }

  /**
   * Authorize a connector. THE authorization entry point — the renderer names
   * a connector, never a mechanism.
   *
   * Two OAuth topologies exist and they are not interchangeable:
   *
   *   profile  `desktop_profiles.yaml` connectors (gmail, gdrive, outlook,
   *            atlassian) carry a PRE-REGISTERED client, so they authorize
   *            through `/v1/connectors/{slug}/desktop/start-oauth`.
   *   mcp      every other catalog seed and every custom server has no profile
   *            and authorizes through the MCP OAuth routes, which discover the
   *            provider and dynamically register a client.
   *
   * Which one applies is decided by a backend-owned file, so asking the
   * renderer to choose was never sound. It chose wrong at three of five call
   * sites, and the failure mode was silent: the profile route answers 404
   * `connector_profile_unavailable` BEFORE opening a browser, so Connect did
   * nothing at all for Linear, Notion, and every other seed.
   *
   * Resolution order, and why:
   *   1. a profile-backed slug wins even when a `serverId` is also known — the
   *      MCP route cannot complete a connector that needs a pre-registered
   *      client, so the profile is the only route that works.
   *   2. otherwise a `serverId` authorizes over MCP OAuth.
   *   3. a slug with no profile and no server row cannot be resolved here; the
   *      caller has to install it first (that mints the row). Saying so beats
   *      opening a browser at something that cannot finish.
   */
  async authorize(target: {
    readonly slug?: string;
    readonly serverId?: string;
    readonly productScope?: DesktopRequestedProductScope;
  }): Promise<ConnectorAuthorizationResult> {
    const { slug, serverId, productScope } = target;
    if (slug !== undefined && (await this.hasDesktopProfile(slug))) {
      const result = await this.coordinator.connect(slug, { productScope });
      return {
        server_id: result.server_id,
        connector_slug: result.connector_slug,
        auth_state: result.auth_state,
      };
    }
    // A catalog seed must EXIST before it can be authorized. `seed:<slug>` is
    // the catalog identity, not proof of a row: the backend mints the row on
    // install and only then does `/v1/mcp/servers/{id}/auth/start` resolve —
    // otherwise it answers "MCP server was not found for this scope", which is
    // what a run's discovery suggestion hits every time, since suggesting a
    // connector never installed it.
    //
    // Install is idempotent on slug, but "idempotent" is not "free" and it is
    // not "always applicable", which is why running it on every authorize was
    // wrong twice over:
    //
    //   • the composer's Connect installs first and hands us the `server_id`
    //     it just minted — re-installing it was a second POST for a row we
    //     were already holding the id of (2×200 per connect);
    //   • a CUSTOM server (added via `POST /v1/mcp/servers`) has a real row
    //     but no catalog entry, so installing "its slug" asked for a catalog
    //     seed that does not exist and answered 404 — twice per connect,
    //     logged at warning, in the one path where a real failure has to be
    //     visible.
    //
    // A known id is therefore trusted only once its row is confirmed to
    // exist. That confirmation is the whole point: `seed:<slug>` is the
    // catalog IDENTITY and not proof of a row, so a run's discovery
    // suggestion still falls through to install and still mints one.
    const resolvedId = await this.resolveServerId(slug, serverId);
    if (resolvedId !== undefined) {
      await this.coordinator.connectMcpServer(resolvedId);
      // No `auth_state` to report: the MCP route resolves once the round-trip
      // completes and the server's OWN row is the record of what it granted.
      return {
        server_id: resolvedId,
        connector_slug: slug ?? null,
        auth_state: null,
      };
    }
    throw new ConnectorOAuthError(
      "start",
      `no desktop profile for "${slug ?? ""}" and no MCP server to authorize`,
    );
  }

  /**
   * Resolve the server row to authorize against, installing only if needed.
   *
   * Order, and why: an existing row always wins. It is the only answer that
   * is certainly authorizable, and reaching it costs one idempotent GET
   * instead of a POST that mutates. Install is the fallback for the case that
   * genuinely has no row yet — a catalog seed the user has never connected.
   */
  private async resolveServerId(
    slug: string | undefined,
    serverId: string | undefined,
  ): Promise<string | undefined> {
    if (serverId !== undefined && (await this.serverExists(serverId))) {
      return serverId;
    }
    if (slug !== undefined) return this.ensureCatalogServer(slug);
    return serverId;
  }

  /** Does `serverId` name a real `mcp_servers` row for this user? */
  private async serverExists(serverId: string): Promise<boolean> {
    const bearer = await this.getBearer();
    if (bearer === null) return false;
    const response = await this.doFetch(
      `${this.facadeBaseUrl}/v1/mcp/servers`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
      },
    );
    // A listing we did not get is not evidence either way, so it degrades to
    // exactly the previous behaviour (install-if-slug) rather than to a guess.
    // Claiming "exists" on a failed read would skip the install a genuine
    // catalog seed still needs and authorize against a row that was never
    // minted — trading a harmless duplicate for a broken connect.
    if (!response.ok) return false;
    const body = (await response.json()) as {
      servers?: readonly { server_id?: string }[];
    };
    const servers = body.servers ?? [];
    return servers.some((server) => server.server_id === serverId);
  }

  /**
   * Ensure the catalog seed for `slug` has a server row, and return its id.
   *
   * Idempotent server-side, so this is an "ensure" rather than an "install".
   * Falls back to the conventional `seed:<slug>` id if the response omits one —
   * that is the id the backend mints for a catalog entry, so a malformed
   * response degrades to the previous behaviour instead of throwing.
   */
  private async ensureCatalogServer(slug: string): Promise<string> {
    const bearer = await this.getBearer();
    if (bearer === null) {
      throw new ConnectorOAuthError("start", "not signed in");
    }
    const response = await this.doFetch(
      `${this.facadeBaseUrl}/v1/mcp/servers/install`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          authorization: `Bearer ${bearer}`,
        },
        body: JSON.stringify({ slug }),
      },
    );
    if (!response.ok) {
      // 422 here is the honest "this entry needs a pre-registered OAuth client"
      // case; surfacing the status beats a browser that cannot complete.
      throw new ConnectorOAuthError(
        "start",
        `install failed for "${slug}": ${response.status}`,
      );
    }
    const installed = (await response.json()) as { server_id?: string };
    return installed.server_id ?? `seed:${slug}`;
  }

  /**
   * Is this slug one of the profile-backed connectors?
   *
   * The reconciled desktop catalog IS the profile overlay, so membership is the
   * authoritative answer rather than a guess. Cached because the overlay is
   * static for a boot — but ONLY when non-empty: `listCatalog` degrades to
   * `{entries: []}` on a signed-out or failed fetch, and caching that would
   * misroute a real profile connector down the MCP path for the whole session.
   */
  private async hasDesktopProfile(slug: string): Promise<boolean> {
    if (this.profileSlugs === null) {
      const catalog = await this.listCatalog();
      if (catalog.entries.length === 0) return false;
      this.profileSlugs = new Set(catalog.entries.map((e) => e.slug));
    }
    return this.profileSlugs.has(slug);
  }
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
