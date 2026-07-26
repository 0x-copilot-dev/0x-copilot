// useDesktopComposerTools — per-run Tools pill state for desktop composers.
//
// The pill is intentionally independent of the `+` attachment menu. Its shared
// trigger uses a body portal, so the 300px controls panel remains clickable
// above the overflow-hidden desktop composer frame.

import { useCallback, useMemo, useState, type ReactNode } from "react";

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
  const { connectorsPort, disabled, onAddCustom } = options;
  const [webOn, setWebOn] = useState(true);
  const [activeConnectorIds, setActiveConnectorIds] = useState<
    readonly string[]
  >([]);

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
  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (entry.requiresPreRegisteredClient) {
        onAddCustom?.();
        return;
      }
      const win = window as unknown as { bridge?: Window["bridge"] };
      if (win.bridge === undefined) return;
      void win.bridge.ipc
        .invoke(CONNECTOR_CHANNELS.connect, { slug: entry.slug })
        .catch(() => {
          /* first-use authorization is best effort */
        });
    },
    [onAddCustom],
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
