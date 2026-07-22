// ChatToolsTrigger — the composer's inline Tools pill + popover for the web chat.
//
// The connector-aware `ComposerToolsButton` + `ToolsPopover` (web-search toggle +
// Connected rows + 1-click Installable + Custom-MCP) that replaces the old flat
// `ComposerConnectorsButton` + `ConnectorPopover`. It mirrors the FTUE reference
// host `FirstRunToolsTrigger` (FirstRunSurface.tsx): the caller owns `webOn` +
// `activeConnectorIds`, and the popover floats above the pill (right-aligned) so
// it never widens the composer bottom bar. Presentational — the port + all state
// come down as props from `ChatScreen`.

import {
  ComposerToolsButton,
  ToolsPopover,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import { type CSSProperties, type ReactElement } from "react";

export interface ChatToolsTriggerProps {
  readonly port: ComposerConnectorsPort;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
}

export function ChatToolsTrigger({
  port,
  open,
  onOpenChange,
  webSearchEnabled,
  onToggleWebSearch,
  activeConnectorIds,
  onToggleConnector,
  onConnectCatalog,
  onAddCustom,
}: ChatToolsTriggerProps): ReactElement {
  // Badge count from surface state alone (web search + each active connector id);
  // the popover header recomputes the exact count against the loaded projection.
  const activeCount = (webSearchEnabled ? 1 : 0) + activeConnectorIds.length;

  return (
    <span style={triggerWrapStyle}>
      <ComposerToolsButton
        open={open}
        onClick={() => onOpenChange(!open)}
        activeCount={activeCount}
      />
      <span style={floatWrapStyle}>
        <ToolsPopover
          open={open}
          onClose={() => onOpenChange(false)}
          port={port}
          webSearchEnabled={webSearchEnabled}
          onToggleWebSearch={onToggleWebSearch}
          activeConnectorIds={activeConnectorIds}
          onToggleConnector={onToggleConnector}
          onConnectCatalog={onConnectCatalog}
          onAddCustom={onAddCustom}
        />
      </span>
    </span>
  );
}

const triggerWrapStyle: CSSProperties = {
  position: "relative",
  display: "inline-flex",
};

// Inline (non-portaled) popover floats above the trigger, right-aligned, so it
// never widens the composer bottom bar (identical to FirstRunToolsTrigger).
const floatWrapStyle: CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 8px)",
  right: 0,
  zIndex: 50,
};
