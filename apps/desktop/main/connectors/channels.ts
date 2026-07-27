// Allowlisted connector IPC channel names (AC9 — desktop MCP connectors).
//
// DEPENDENCY-FREE (string literals only) so it is safe to import from the
// sandboxed preload AND from the renderer bundle as well as from main — every
// side must agree on the exact channel set from a single source. Mirrors the
// role `capabilities/channels.ts` plays for the host-folder grant surface and
// `@0x-copilot/chat-transport`'s CHANNELS for transport/auth.
//
// Channel string values follow the codebase convention: camelCase keys,
// kebab-case wire values. Never hardcode the string values elsewhere — import
// `CONNECTOR_CHANNELS`.

export const CONNECTOR_CHANNELS = {
  /** Renderer → main: fetch the reconciled desktop connector catalog. */
  listCatalog: "connector.list-catalog",
  /**
   * Renderer → main: authorize a connector. THE one authorization verb.
   *
   * There used to be two — `connector.connect` (by slug, via the four
   * `desktop_profiles.yaml` entries) and `connector.authorize-server` (by MCP
   * server id, via MCP OAuth discovery + DCR). Which one a connector needs is
   * decided by a BACKEND-owned file, so every renderer caller was guessing:
   * of five call sites, three guessed "profile", which is why Connect was a
   * dead button for every catalog seed — Linear, Notion, and anything else
   * outside the four profiles. See `ConnectorService.authorize`.
   *
   * Main now resolves the topology, so a renderer never names a mechanism and
   * a new connector needs no renderer change at all.
   */
  authorize: "connector.authorize",
} as const;

export type ConnectorChannelName =
  (typeof CONNECTOR_CHANNELS)[keyof typeof CONNECTOR_CHANNELS];

export const CONNECTOR_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CONNECTOR_CHANNELS),
);

export function isConnectorChannel(name: string): name is ConnectorChannelName {
  return CONNECTOR_CHANNEL_VALUES.has(name);
}
