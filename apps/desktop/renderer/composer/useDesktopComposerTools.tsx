// useDesktopComposerTools — per-run Tools pill state for desktop composers.
//
// The pill is intentionally independent of the `+` attachment menu. Its shared
// trigger uses a body portal, so the 300px controls panel remains clickable
// above the overflow-hidden desktop composer frame.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ComposerToolsTrigger,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import type { ConversationConnectorScopes } from "@0x-copilot/api-types";

import { CONNECTOR_CHANNELS } from "../../main/connectors/channels";

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
   * Connector whose OAuth flow just completed. Seed it into the run-scoped
   * active set once; subsequent manual toggles remain authoritative.
   */
  readonly autoActivateConnectorId?: string | null;
}

export interface DesktopComposerTools {
  /** Tools pill + portal-safe popover, omitted when the adapter is unavailable. */
  readonly toolsTrigger: ReactNode | undefined;
  readonly webSearchEnabled: boolean;
  readonly connectorScopes: ConversationConnectorScopes | undefined;
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
  const [webOn, setWebOn] = useState(true);
  const [activeConnectorIds, setActiveConnectorIds] = useState<
    readonly string[]
  >(() => (autoActivateConnectorId === null ? [] : [autoActivateConnectorId]));

  useEffect(() => {
    if (autoActivateConnectorId === null) return;
    setActiveConnectorIds((current) =>
      current.includes(autoActivateConnectorId)
        ? current
        : [...current, autoActivateConnectorId],
    );
  }, [autoActivateConnectorId]);

  const handleToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setActiveConnectorIds((current) =>
        active
          ? current.includes(serverId)
            ? current
            : [...current, serverId]
          : current.filter((id) => id !== serverId),
      );
    },
    [],
  );

  // Electron MAIN brokers OAuth in the system browser. No token crosses IPC.
  //
  // The popover lists `mcp_catalog` seeds, so a catalog row is installed as an
  // MCP SERVER first — that mint is what gives the connector a `server_id` to
  // authorize by. Both identities then go to `connector.authorize`, which picks
  // the route: a seed has no `desktop_profiles.yaml` entry and authorizes over
  // MCP OAuth, which is what this button needed to do all along for Linear and
  // Notion.
  //
  // Failures are reported. The original `.catch(() => {})` swallowed them, so a
  // 404 presented as a button that did not respond, with no error anywhere and
  // no request in the HTTP logs.
  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (entry.requiresPreRegisteredClient) {
        onAddCustom?.();
        return;
      }
      const win = window as unknown as { bridge?: Window["bridge"] };
      if (win.bridge === undefined || connectorsPort === undefined) return;
      const bridge = win.bridge;
      void (async () => {
        try {
          const server = await connectorsPort.installFromCatalog(entry.slug);
          await bridge.ipc.invoke(CONNECTOR_CHANNELS.authorize, {
            slug: entry.slug,
            serverId: server.server_id,
          });
        } catch (error: unknown) {
          onConnectError?.(
            entry.displayName,
            error instanceof Error ? error.message : String(error),
          );
        }
      })();
    },
    [connectorsPort, onAddCustom, onConnectError],
  );

  const connectorScopes = useMemo<
    ConversationConnectorScopes | undefined
  >(() => {
    if (activeConnectorIds.length === 0) return undefined;
    return Object.fromEntries(activeConnectorIds.map((id) => [id, []]));
  }, [activeConnectorIds]);

  const toolsTrigger = useMemo<ReactNode | undefined>(() => {
    if (connectorsPort === undefined) return undefined;
    return (
      <ComposerToolsTrigger
        port={connectorsPort}
        webSearchEnabled={webOn}
        onToggleWebSearch={setWebOn}
        activeConnectorIds={activeConnectorIds}
        onToggleConnector={handleToggleConnector}
        onConnectCatalog={handleConnectCatalog}
        onAddCustom={() => onAddCustom?.()}
        disabled={disabled}
      />
    );
  }, [
    connectorsPort,
    webOn,
    activeConnectorIds,
    handleToggleConnector,
    handleConnectCatalog,
    onAddCustom,
    disabled,
  ]);

  return { toolsTrigger, webSearchEnabled: webOn, connectorScopes };
}
