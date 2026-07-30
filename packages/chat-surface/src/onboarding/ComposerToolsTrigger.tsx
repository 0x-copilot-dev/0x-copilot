// ComposerToolsTrigger — the one anchored implementation for the composer's
// Tools pill. It portals through the design-system Menu, so it cannot be
// clipped by the composer's overflow-hidden frame and Menu treats the portal
// itself as inside for pointer dismissal.
//
// The trigger — not the panel — owns the connector fetch
// (`useConnectorPopoverData`), because the PILL has to answer the same question
// the panel does. Its badge used to count `activeConnectorIds.length`, a set
// that started empty, so the closed pill read "Tools 1" (web search alone) while
// the panel it opened listed a connected connector. Now both read one projection
// through `firstRunActiveToolCount`, and the panel is a pure renderer of it.

import { Menu } from "@0x-copilot/design-system";
import { useRef, useState, type CSSProperties, type ReactElement } from "react";

import { ComposerToolsButton } from "./ComposerToolsButton";
import { ToolsPopoverContent } from "./ToolsPopover";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import {
  firstRunActiveToolCount,
  type FirstRunInstallableConnector,
} from "./projectFirstRunConnectors";
import { useConnectorPopoverData } from "./useConnectorPopoverData";

export interface ComposerToolsTriggerProps {
  readonly port: FirstRunConnectorsPort;
  /**
   * Bump to refetch the connector list. Hosts increment it when they observe an
   * OAuth/connect completion, so the new connector appears — already on —
   * without the user closing and reopening the pill.
   */
  readonly reloadToken?: number;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  /** Ids paused for this run. Empty ⇒ every connected connector is live. */
  readonly pausedConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
  readonly disabled?: boolean;
}

export function ComposerToolsTrigger(
  props: ComposerToolsTriggerProps,
): ReactElement {
  const {
    port,
    reloadToken,
    webSearchEnabled,
    onToggleWebSearch,
    pausedConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    onAddCustom,
    disabled,
  } = props;
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const data = useConnectorPopoverData(port, { reloadToken });
  const activeCount = firstRunActiveToolCount(
    webSearchEnabled,
    data.connected,
    pausedConnectorIds,
  );

  const close = (): void => setOpen(false);

  return (
    <span ref={anchorRef} style={triggerWrapStyle}>
      <ComposerToolsButton
        open={open}
        onClick={() => setOpen((current) => !current)}
        activeCount={activeCount}
        disabled={disabled}
      />
      <Menu
        open={open}
        onClose={close}
        anchorRef={anchorRef}
        side="up"
        // The Focus composer is flush to the left of the canvas. Right-aligning
        // a 318px panel to its small pill pushed the panel beyond the viewport's
        // left edge, leaving its Web Search row visible but unclickable. A left
        // edge anchor keeps it fully reachable in Focus and has room in Studio.
        align="left"
        role="dialog"
        aria-label="Tools"
        data-testid="composer-tools-popover"
      >
        <div className="ui-pop" style={panelStyle}>
          <ToolsPopoverContent
            data={data}
            webSearchEnabled={webSearchEnabled}
            onToggleWebSearch={onToggleWebSearch}
            pausedConnectorIds={pausedConnectorIds}
            onToggleConnector={onToggleConnector}
            onConnectCatalog={onConnectCatalog}
            onAddCustom={onAddCustom}
            onClose={close}
          />
        </div>
      </Menu>
    </span>
  );
}

const triggerWrapStyle: CSSProperties = {
  display: "inline-flex",
};

const panelStyle: CSSProperties = {
  // The Design composer uses a 318px Tools panel (the model picker is 300px).
  // Keeping those roles distinct preserves the row's readable connector copy
  // without changing the responsive viewport cap.
  width: 318,
  maxWidth: "calc(100vw - 1rem)",
};
