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
  /**
   * Renderer → main: abort the connect currently awaiting a redirect.
   *
   * Mirrors `auth.cancel-sign-in`, deliberately: one pending slot, newest wins,
   * no argument. There is no attempt id because there is at most one connect in
   * flight — every surface disables its other rows while one is running, the
   * same way the sign-in screen does.
   *
   * This has to reach MAIN to mean anything. A renderer-only "cancel" would
   * leave the loopback armed for its full five-minute timeout, so a user who
   * cancelled and then approved anyway in the still-open browser tab would find
   * the connector silently connected. Closing the loopback here rejects the
   * pending `authorize`, which is what actually stops the flow.
   */
  cancelAuthorize: "connector.cancel-authorize",
} as const;

/**
 * What `connector.authorize` resolves with — the payload half of the same
 * contract, and it lives HERE for the same reason the channel names do: main,
 * preload, and the renderer must agree on it from one source, and this is the
 * only connector module the service-boundary check lets the renderer import
 * (`tools/check_service_boundaries.py`, `_DESKTOP_MAIN_IPC_CONTRACTS`). It stays
 * dependency-free — declaring it beside the coordinator would drag
 * main-process code into the renderer bundle, which is exactly what that check
 * exists to stop.
 *
 * Not `packages/api-types`: that package mirrors the public HTTP surface, and
 * this shape is synthesized by main for an IPC reply. It is deliberately
 * narrower than the profile route's own HTTP result, because it must describe
 * BOTH OAuth topologies honestly — the MCP route knows the server it authorized
 * and nothing more, so `auth_state` and `connector_slug` are nullable rather
 * than padded with a plausible value.
 */
export interface ConnectorAuthorizationResult {
  readonly server_id: string;
  readonly connector_slug: string | null;
  readonly auth_state: string | null;
}

export type ConnectorChannelName =
  (typeof CONNECTOR_CHANNELS)[keyof typeof CONNECTOR_CHANNELS];

export const CONNECTOR_CHANNEL_VALUES: ReadonlySet<string> = new Set(
  Object.values(CONNECTOR_CHANNELS),
);

export function isConnectorChannel(name: string): name is ConnectorChannelName {
  return CONNECTOR_CHANNEL_VALUES.has(name);
}
