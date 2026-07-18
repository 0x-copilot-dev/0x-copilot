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
  /** Renderer → main: begin the system-browser OAuth connect flow for a slug. */
  connect: "connector.connect",
} as const;

export type ConnectorChannelName =
  (typeof CONNECTOR_CHANNELS)[keyof typeof CONNECTOR_CHANNELS];

export const CONNECTOR_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CONNECTOR_CHANNELS),
);

export function isConnectorChannel(name: string): name is ConnectorChannelName {
  return CONNECTOR_CHANNEL_VALUES.has(name);
}
