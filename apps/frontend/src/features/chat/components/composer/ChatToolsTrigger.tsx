// ChatToolsTrigger — the web host binding for the composer's Tools pill.

import {
  ComposerToolsTrigger,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import { type ReactElement } from "react";

export interface ChatToolsTriggerProps {
  readonly port: ComposerConnectorsPort;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
}

export function ChatToolsTrigger({
  port,
  webSearchEnabled,
  onToggleWebSearch,
  activeConnectorIds,
  onToggleConnector,
  onConnectCatalog,
  onAddCustom,
}: ChatToolsTriggerProps): ReactElement {
  return (
    <ComposerToolsTrigger
      port={port}
      webSearchEnabled={webSearchEnabled}
      onToggleWebSearch={onToggleWebSearch}
      activeConnectorIds={activeConnectorIds}
      onToggleConnector={onToggleConnector}
      onConnectCatalog={onConnectCatalog}
      onAddCustom={onAddCustom}
    />
  );
}
