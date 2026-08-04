// useDesktopComposerTools — the DESKTOP half of the composer's Tools pill.
//
// Everything general moved to `useConnectorTools` in chat-surface: web-search,
// the paused-connector set, the reload token, and the connect lifecycle. This
// file is now only what is genuinely desktop — how a connect is performed —
// because Electron main brokers OAuth in the system browser and the renderer is
// denied `window.open` entirely. No token crosses IPC.
//
// It used to be a near-copy of the FTUE's machine and of the web composer's.
// Three copies is how the FTUE shipped without the refetch this file already
// had, so the machine is shared now and only the verb is bound per host.
//
// The pill stays independent of the `+` attachment menu; its shared trigger
// portals through the body so the controls panel is clickable above the
// overflow-hidden composer frame.

import { useMemo } from "react";

import {
  ConnectSupersededError,
  useConnectorTools,
  type ComposerConnectorsPort,
  type ConnectorToolsHostPort,
} from "@0x-copilot/chat-surface";

import {
  CONNECTOR_CHANNELS,
  CONNECT_CANCELLED,
  type ConnectorAuthorizationOutcome,
} from "../../main/connectors/channels";

export interface UseDesktopComposerToolsOptions {
  readonly connectorsPort?: ComposerConnectorsPort;
  readonly disabled?: boolean;
  /** Route Custom MCP and pre-registered catalog entries to Tools settings. */
  readonly onAddCustom?: () => void;
  /**
   * Report a failed 1-click connect. Without this the failure is invisible:
   * the row simply stops responding, which is how the seed-vs-profile 404 went
   * unnoticed. Hosts wire it to their notification surface.
   */
  readonly onConnectError?: (displayName: string, message: string) => void;
  /**
   * Connector whose OAuth flow just completed elsewhere (the in-chat consent
   * card). Connected connectors are live by default, so this only clears a
   * stale pause on that id and refreshes the list.
   */
  readonly autoActivateConnectorId?: string | null;
}

export interface DesktopComposerTools {
  /** Tools pill + portal-safe popover, omitted when the adapter is unavailable. */
  readonly toolsTrigger: ReturnType<typeof useConnectorTools>["toolsTrigger"];
  readonly webSearchEnabled: boolean;
  /** Ids the user paused for this run → `request_context.paused_connectors`. */
  readonly pausedConnectorIds: readonly string[];
}

export function useDesktopComposerTools(
  options: UseDesktopComposerToolsOptions,
): DesktopComposerTools {
  const {
    connectorsPort,
    disabled,
    onAddCustom,
    onConnectError,
    autoActivateConnectorId = null,
  } = options;

  // The popover lists `mcp_catalog` seeds, so a catalog row is installed as an
  // MCP SERVER first — that mint is what gives the connector a `server_id` to
  // authorize by. Both identities then go to `connector.authorize`, which picks
  // the topology: a seed has no `desktop_profiles.yaml` entry and authorizes
  // over MCP OAuth, which is what this button needed to do all along for Linear
  // and Notion.
  //
  // `connector.authorize` resolves only once the OAuth round-trip finishes (see
  // `ConnectorService.authorize`), which is exactly the completion contract
  // `ConnectorToolsHostPort.connect` requires — so the shared hook's refetch
  // lands at the right moment without this file tracking anything itself.
  const host = useMemo<ConnectorToolsHostPort>(
    () => ({
      async connect(entry) {
        const win = window as unknown as { bridge?: Window["bridge"] };
        if (win.bridge === undefined || connectorsPort === undefined) return;
        const server = await connectorsPort.installFromCatalog(entry.slug);
        const outcome = (await win.bridge.ipc.invoke(
          CONNECTOR_CHANNELS.authorize,
          { slug: entry.slug, serverId: server.server_id },
        )) as ConnectorAuthorizationOutcome;
        // Main resolves the ordinary endings; only a real failure rejects and
        // propagates from the invoke above. The shared hook still ends an
        // attempt by rejecting, so translate here — the same mapping the
        // connectors binder does, for the same reason.
        if (outcome.outcome === "superseded") {
          throw new ConnectSupersededError(entry.slug);
        }
        if (outcome.outcome === "cancelled") {
          throw new Error(CONNECT_CANCELLED);
        }
        return { serverId: server.server_id };
      },
      // Reaches MAIN, which closes the armed loopback so the `authorize` above
      // rejects. A renderer-only reset would leave the provider's tab live and
      // the flow running for its full timeout.
      async cancel() {
        const win = window as unknown as { bridge?: Window["bridge"] };
        if (win.bridge === undefined) return;
        await win.bridge.ipc.invoke(CONNECTOR_CHANNELS.cancelAuthorize, {});
      },
    }),
    [connectorsPort],
  );

  const { toolsTrigger, webSearchEnabled, pausedConnectorIds } =
    useConnectorTools({
      port: connectorsPort,
      host,
      autoActivateConnectorId,
      onAddCustom,
      onConnectError,
      disabled,
    });

  return { toolsTrigger, webSearchEnabled, pausedConnectorIds };
}
