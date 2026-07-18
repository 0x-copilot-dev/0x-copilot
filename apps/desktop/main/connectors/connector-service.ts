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
  DesktopConnectorConnectionResult,
  DesktopRequestedProductScope,
} from "@0x-copilot/api-types";

import {
  ConnectorOAuthCoordinator,
  type ConnectorOAuthDeps,
} from "./oauth-coordinator";

export interface ConnectorServiceDeps extends ConnectorOAuthDeps {}

export class ConnectorService {
  private readonly facadeBaseUrl: string;
  private readonly getBearer: () => Promise<string | null>;
  private readonly doFetch: typeof fetch;
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

  /** Begin the system-browser connect flow for a stable slug. Returns only
   *  safe connection metadata — never a token. */
  connect(
    slug: string,
    options: { readonly productScope?: DesktopRequestedProductScope } = {},
  ): Promise<DesktopConnectorConnectionResult> {
    return this.coordinator.connect(slug, options);
  }
}

function trimTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}
