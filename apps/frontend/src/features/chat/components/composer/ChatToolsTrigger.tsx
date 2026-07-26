// ChatToolsMenu — the web host's body for composer `+ → Tools`.
//
// The caller owns web-search + connector state; this component deliberately
// renders only the body because AssistantComposer's `+` menu is the one entry
// point for attachments, skills, connectors, and run-scoped tools.

import {
  ToolsPopoverContent,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import { type ReactElement } from "react";

export interface ChatToolsMenuProps {
  readonly port: ComposerConnectorsPort;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
  readonly onBack: () => void;
}

export function ChatToolsMenu({
  port,
  webSearchEnabled,
  onToggleWebSearch,
  activeConnectorIds,
  onToggleConnector,
  onConnectCatalog,
  onAddCustom,
  onBack,
}: ChatToolsMenuProps): ReactElement {
  return (
    <ToolsPopoverContent
      port={port}
      webSearchEnabled={webSearchEnabled}
      onToggleWebSearch={onToggleWebSearch}
      activeConnectorIds={activeConnectorIds}
      onToggleConnector={onToggleConnector}
      onConnectCatalog={onConnectCatalog}
      onAddCustom={onAddCustom}
      onBack={onBack}
    />
  );
}
