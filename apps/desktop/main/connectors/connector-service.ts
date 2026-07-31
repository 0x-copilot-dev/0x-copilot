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
    // Install is idempotent on slug (it returns the existing row unchanged), so
    // this is safe to run on every authorize rather than gated behind a
    // lookup, and it finally makes true the thing the desktop port has claimed
    // all along: install and authenticate are ONE brokered flow.
    const resolvedId =
      slug !== undefined
        ? await this.ensureCatalogServer(slug, { fallbackServerId: serverId })
        : serverId;
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
   * Ensure the catalog seed for `slug` has a server row, and return its id.
   *
   * Idempotent server-side, so this is an "ensure" rather than an "install".
   * Falls back to the conventional `seed:<slug>` id if the response omits one —
   * that is the id the backend mints for a catalog entry, so a malformed
   * response degrades to the previous behaviour instead of throwing.
   *
   * `fallbackServerId` is what a NON-catalog server authorizes by. Install
   * answers 404 `Unknown catalog entry` for any slug the curated catalog does
   * not carry — which is every custom register-by-URL server, since its slug is
   * derived from its host (`api_githubcopilot_com`). Treating that 404 as fatal
   * is what made Connect fail for every hand-added server: the row existed, the
   * renderer had resolved its id, and this threw before a browser ever opened.
   * A 404 means only "the catalog does not know this slug"; when the caller
   * already holds a real row it is not an error at all. Every other status
   * still throws — notably 422, the honest "this entry needs a pre-registered
   * OAuth client", where no row can exist and no id can rescue it.
   */
  private async ensureCatalogServer(
    slug: string,
    options: { readonly fallbackServerId?: string } = {},
  ): Promise<string | undefined> {
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
      if (response.status === 404 && options.fallbackServerId !== undefined) {
        return options.fallbackServerId;
      }
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
