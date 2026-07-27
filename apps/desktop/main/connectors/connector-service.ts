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
    if (serverId !== undefined) {
      await this.coordinator.connectMcpServer(serverId);
      // No `auth_state` to report: the MCP route resolves once the round-trip
      // completes and the server's OWN row is the record of what it granted.
      return {
        server_id: serverId,
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
