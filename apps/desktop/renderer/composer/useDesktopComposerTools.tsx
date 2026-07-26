// useDesktopComposerTools — the Run cockpit composers' `+` menu Tools view.
//
// The desktop Run composers own per-run web-search and connector state here,
// then provide a body for the shared composer's one `+` menu. There is no
// standalone Tools pill: it competed with the attachment button and its
// independently positioned popover was clipped by the desktop frame.

import { useCallback, useMemo, useState, type ReactNode } from "react";

import {
  ToolsPopoverContent,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import type { ConversationConnectorScopes } from "@0x-copilot/api-types";

import { CONNECTOR_CHANNELS } from "../../main/connectors/channels";

export interface UseDesktopComposerToolsOptions {
  /** Shared `/v1/mcp/*` connector adapter; absent means no per-run tools UI. */
  readonly connectorsPort?: ComposerConnectorsPort;
  /** Route Custom MCP and pre-registered catalog entries to Tools settings. */
  readonly onAddCustom?: () => void;
}

export interface DesktopComposerTools {
  /** Body rendered in AssistantComposer's `+` menu Tools view. */
  readonly renderToolsMenu:
    | ((args: { readonly onBack: () => void }) => ReactNode)
    | undefined;
  /** Per-run web-search toggle (default true). Thread an explicit `false`. */
  readonly webSearchEnabled: boolean;
  /** Active connector ids mapped to request-context scopes. */
  readonly connectorScopes: ConversationConnectorScopes | undefined;
}

export function useDesktopComposerTools(
  options: UseDesktopComposerToolsOptions,
): DesktopComposerTools {
  const { connectorsPort, onAddCustom } = options;
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

  const renderToolsMenu = useCallback(
    ({ onBack }: { readonly onBack: () => void }): ReactNode => {
      if (connectorsPort === undefined) return null;
      return (
        <ToolsPopoverContent
          port={connectorsPort}
          webSearchEnabled={webOn}
          onToggleWebSearch={setWebOn}
          activeConnectorIds={activeConnectorIds}
          onToggleConnector={handleToggleConnector}
          onConnectCatalog={handleConnectCatalog}
          onAddCustom={() => onAddCustom?.()}
          onBack={onBack}
        />
      );
    },
    [
      connectorsPort,
      webOn,
      activeConnectorIds,
      handleToggleConnector,
      handleConnectCatalog,
      onAddCustom,
    ],
  );

  return {
    renderToolsMenu: connectorsPort === undefined ? undefined : renderToolsMenu,
    webSearchEnabled: webOn,
    connectorScopes,
  };
}
