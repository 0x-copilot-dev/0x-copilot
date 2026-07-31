// projectFirstRunConnectors — pure `(servers, catalog) → { connected, installable }`.
//
// chat-surface CANNOT import `apps/*`, so this is a deliberate copy of the web
// app's `projectChatConnectors` classification
// (`apps/frontend/src/features/connectors/projectConnectors.ts`): a connector
// is "Connected" only when it is workspace-installed (`enabled === true`) AND
// the user is authenticated against it. Everything else (uninstalled,
// half-installed, workspace-off) is NOT shown as connected.
//
// For the first-run popover the second bucket is "installable": the curated
// 1-click catalog entries the user has NOT yet connected. The catalog cross-
// references the server list by the seed id convention
// (`server_id === "seed:" + slug`, per `McpCatalogEntry` docs) so an entry the
// user already connected drops out of the installable list.
//
// This projection is per-run-state-agnostic: the FTUE has no conversation and
// therefore no `ConversationConnectorScopes` at toggle time. What IS derived
// here is availability — `connected` means "the runtime can call this right
// now". The popover's toggle is an OPT-OUT on top of that (`pausedConnectorIds`,
// empty by default), never an opt-in: a connector the Tools destination reports
// as connected must not render as disabled in the composer. That inversion is
// the whole reason a freshly authorized connector shows up already on.
//
// `access_mode === "off"` drops out entirely, mirroring the backend's own card
// gate (`backend_app/service.py::list_internal_cards` skips `off` servers, so
// the model never sees them). Listing an `off` row with a per-run toggle would
// offer a control that cannot grant anything — the durable switch lives in
// Settings → Tools. An `off` server still suppresses its catalog entry, so it
// does not reappear as a 1-click "Connect" for something already installed.

import type {
  ConnectorAccessMode,
  McpAuthState,
  McpCatalogEntry,
  McpServer,
} from "@0x-copilot/api-types";

/** A workspace-installed, user-authenticated connector the runtime can call —
 *  rendered in the "Connected" section, ON unless paused for this run. */
export interface FirstRunConnectedConnector {
  readonly serverId: string;
  readonly displayName: string;
  /** One-line row subtitle (e.g. "read & write workbooks"). */
  readonly scopesSummary: string | null;
  readonly logoUrl: string | null;
  readonly brandColor: string | null;
  /**
   * Durable authority mode. Never `off` on a projected row (those are dropped);
   * carried so a row can say `read` vs `read_act` without a second fetch.
   * Servers from a backend that predates the field default to `read`.
   */
  readonly accessMode: ConnectorAccessMode;
}

/** A curated catalog entry the user has not connected yet — rendered as a
 *  1-click "Connect" (or "Set up" for pre-registered vendors) row. */
export interface FirstRunInstallableConnector {
  readonly slug: string;
  readonly displayName: string;
  readonly description: string;
  readonly scopesSummary: string | null;
  readonly logoUrl: string | null;
  readonly brandColor: string | null;
  /**
   * Vendor exposes no RFC 8414 metadata / RFC 7591 DCR, so a 1-click keyless
   * install 422s. The popover routes these to the custom-config form instead.
   */
  readonly requiresPreRegisteredClient: boolean;
}

export interface FirstRunConnectorProjection {
  readonly connected: readonly FirstRunConnectedConnector[];
  readonly installable: readonly FirstRunInstallableConnector[];
}

const SEED_PREFIX = "seed:";

/** Mirror of `apps/*` `isAuthenticated(auth_state)` — authenticated, skipped,
 *  and unsupported all mean "the agent may call this connector". */
function isAuthenticated(state: McpAuthState): boolean {
  return (
    state === "authenticated" ||
    state === "auth_skipped" ||
    state === "auth_unsupported"
  );
}

export function projectFirstRunConnectors(
  servers: readonly McpServer[],
  catalog: readonly McpCatalogEntry[],
): FirstRunConnectorProjection {
  const connected: FirstRunConnectedConnector[] = [];
  const connectedSlugs = new Set<string>();

  for (const server of servers) {
    if (server.enabled !== true || !isAuthenticated(server.auth_state)) {
      continue;
    }
    // Installed either way, so the catalog entry is suppressed before the
    // access-mode gate — an `off` connector must not resurface as "Connect".
    if (server.server_id.startsWith(SEED_PREFIX)) {
      connectedSlugs.add(server.server_id.slice(SEED_PREFIX.length));
    }
    const accessMode = server.access_mode ?? "read";
    if (accessMode === "off") {
      continue;
    }
    connected.push({
      serverId: server.server_id,
      // `server_id` last: a stdio server has no URL to fall back to.
      displayName:
        server.display_name || server.name || server.url || server.server_id,
      scopesSummary: server.scopes_summary ?? null,
      logoUrl: server.logo_url ?? null,
      brandColor: server.brand_color ?? null,
      accessMode,
    });
  }

  const installable: FirstRunInstallableConnector[] = catalog
    .filter((entry) => !connectedSlugs.has(entry.slug))
    .map((entry) => ({
      slug: entry.slug,
      displayName: entry.display_name,
      description: entry.description,
      scopesSummary: entry.scopes_summary ?? null,
      logoUrl: entry.logo_url ?? null,
      brandColor: entry.brand_color ?? null,
      requiresPreRegisteredClient:
        entry.requires_pre_registered_client === true,
    }));

  return { connected, installable };
}

/** Is this connected row live for the run? Connected means callable, so the
 *  answer is yes unless the user paused it for this run. */
export function isFirstRunConnectorActive(
  row: FirstRunConnectedConnector,
  pausedConnectorIds: readonly string[],
): boolean {
  return !pausedConnectorIds.includes(row.serverId);
}

/** Count the tools currently ON: web search (when enabled) + every connected
 *  connector the user has not paused. Drives the popover header meta `{n} on`
 *  and the composer pill badge — one function so the two can never disagree.
 *  Paused ids that resolve to no connected row (uninstalled since, or switched
 *  off in Settings) are ignored rather than subtracted. */
export function firstRunActiveToolCount(
  webSearchEnabled: boolean,
  connected: readonly FirstRunConnectedConnector[],
  pausedConnectorIds: readonly string[],
): number {
  const activeConnectors = connected.reduce(
    (n, row) =>
      isFirstRunConnectorActive(row, pausedConnectorIds) ? n + 1 : n,
    0,
  );
  return (webSearchEnabled ? 1 : 0) + activeConnectors;
}
