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
// therefore no `ConversationConnectorScopes` at toggle time. Active/paused is
// held as component state (`activeConnectorIds`) by the popover, not derived
// here — so this function takes no scopes argument.

import type {
  McpAuthState,
  McpCatalogEntry,
  McpServer,
} from "@0x-copilot/api-types";

/** A workspace-installed, user-authenticated connector — rendered in the
 *  "Connected" section with a per-run active/paused toggle. */
export interface FirstRunConnectedConnector {
  readonly serverId: string;
  readonly displayName: string;
  /** One-line row subtitle (e.g. "read & write workbooks"). */
  readonly scopesSummary: string | null;
  readonly logoUrl: string | null;
  readonly brandColor: string | null;
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
    connected.push({
      serverId: server.server_id,
      displayName: server.display_name || server.name || server.url,
      scopesSummary: server.scopes_summary ?? null,
      logoUrl: server.logo_url ?? null,
      brandColor: server.brand_color ?? null,
    });
    if (server.server_id.startsWith(SEED_PREFIX)) {
      connectedSlugs.add(server.server_id.slice(SEED_PREFIX.length));
    }
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

/** Count the tools currently ON: web search (when enabled) + active
 *  connectors that actually resolve to a connected row. Drives the popover
 *  header meta `{n} on` and the composer pill badge. */
export function firstRunActiveToolCount(
  webSearchEnabled: boolean,
  connected: readonly FirstRunConnectedConnector[],
  activeConnectorIds: readonly string[],
): number {
  const active = new Set(activeConnectorIds);
  const activeConnectors = connected.reduce(
    (n, row) => (active.has(row.serverId) ? n + 1 : n),
    0,
  );
  return (webSearchEnabled ? 1 : 0) + activeConnectors;
}
