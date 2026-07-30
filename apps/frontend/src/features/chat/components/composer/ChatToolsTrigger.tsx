// ChatToolsTrigger — the web host binding for the composer's Tools pill.

import {
  ComposerToolsTrigger,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import { type ReactElement } from "react";

export interface ChatToolsTriggerProps {
  readonly port: ComposerConnectorsPort;
  /** Bump to refetch the connector list after a connect completes. */
  readonly reloadToken?: number;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  /** Ids paused for this run. Empty ⇒ every connected connector is live. */
  readonly pausedConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
}

export function ChatToolsTrigger({
  port,
  reloadToken,
  webSearchEnabled,
  onToggleWebSearch,
  pausedConnectorIds,
  onToggleConnector,
  onConnectCatalog,
  onAddCustom,
}: ChatToolsTriggerProps): ReactElement {
  return (
    <ComposerToolsTrigger
      port={port}
      reloadToken={reloadToken}
      webSearchEnabled={webSearchEnabled}
      onToggleWebSearch={onToggleWebSearch}
      pausedConnectorIds={pausedConnectorIds}
      onToggleConnector={onToggleConnector}
      onConnectCatalog={onConnectCatalog}
      onAddCustom={onAddCustom}
    />
  );
}
