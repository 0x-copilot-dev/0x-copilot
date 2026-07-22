// useDesktopComposerTools — the Run cockpit composers' inline Tools popover.
//
// The desktop Run composers (in-chat `RunComposer` + empty-state
// `RunEmptyComposer`) mount the SAME connector-aware Tools popover the FTUE
// built (`ComposerToolsButton` + `ToolsPopover`, from @0x-copilot/chat-surface),
// replacing the flat "open the Tools surface" `ComposerConnectorsButton`. This
// hook is the host-side owner of the per-run Tools state — a direct crib of
// `FirstRunSurface`'s internal wiring + its `FirstRunToolsTrigger` (which is not
// exported): it owns `webOn` (default true) + `activeConnectorIds`, derives the
// run's `connector_scopes`, and builds the trigger node the composer drops into
// `AssistantComposer`'s `connectorsTrigger` slot.
//
// It returns exactly what a composer needs to (a) render the trigger and (b)
// build the run body on send:
//   • `toolsTrigger`       → the `ComposerToolsButton` + floated `ToolsPopover`
//   • `webSearchEnabled`   → per-run web-search toggle (thread `false` only)
//   • `connectorScopes`    → active connector ids → `request_context` scopes
//
// Desktop-specific connect: like `FirstRunGate`/`ConnectorsBinder`, the renderer
// cannot open an external OAuth URL (main denies `window.open`), so a 1-click
// catalog "Connect" is brokered by Electron MAIN over `CONNECTOR_CHANNELS.connect`
// (loopback bind + system browser). No token crosses the bridge.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import {
  ComposerToolsButton,
  ToolsPopover,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";
import type { ConversationConnectorScopes } from "@0x-copilot/api-types";

import { CONNECTOR_CHANNELS } from "../../main/connectors/channels";

export interface UseDesktopComposerToolsOptions {
  /**
   * Host-injected MCP connector surface (the shared `/v1/mcp/*` adapter). When
   * `undefined` the hook returns no trigger — the composer keeps its plain
   * `ComposerConnectorsButton` fallback — but still exposes the web-search
   * default so the run body is unchanged.
   */
  readonly connectorsPort?: ComposerConnectorsPort;
  /** Disables the trigger (e.g. cockpit scrubbed off-live, or run starting). */
  readonly disabled?: boolean;
  /**
   * Routing target for the popover's "Custom MCP" entry and any catalog row
   * that requires a pre-registered client (a keyless install would 422). The
   * run composers point this at the Tools destination (`onShowConnectors`).
   */
  readonly onAddCustom?: () => void;
}

export interface DesktopComposerTools {
  /**
   * The Tools trigger to drop into `AssistantComposer.connectorsTrigger`.
   * `undefined` when no `connectorsPort` was injected.
   */
  readonly toolsTrigger: ReactNode | undefined;
  /** Per-run web-search toggle (default true). Thread an explicit `false`. */
  readonly webSearchEnabled: boolean;
  /**
   * Active connector scopes for the run (active ids → `[]`, i.e. enabled with no
   * extra scopes), or `undefined` when no connectors are active.
   */
  readonly connectorScopes: ConversationConnectorScopes | undefined;
}

export function useDesktopComposerTools(
  options: UseDesktopComposerToolsOptions,
): DesktopComposerTools {
  const { connectorsPort, disabled, onAddCustom } = options;

  // Per-run Tools state (SPEC `webOn`, default true; connectors held as active
  // ids since a run composer has no persisted conversation-scope to PATCH at
  // toggle time — the ids fold into the run-create body on send).
  const [webOn, setWebOn] = useState(true);
  const [activeConnectorIds, setActiveConnectorIds] = useState<
    readonly string[]
  >([]);
  const [toolsOpen, setToolsOpen] = useState(false);

  const handleToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setActiveConnectorIds((prev) =>
        active
          ? prev.includes(serverId)
            ? prev
            : [...prev, serverId]
          : prev.filter((id) => id !== serverId),
      );
    },
    [],
  );

  // Featured 1-click connect — brokered by Electron MAIN (system browser). The
  // renderer only hands over the catalog slug; a pre-registered vendor routes to
  // the custom-config form instead (keyless install 422s). Best-effort: a failed
  // authorize still surfaces later as the run-time `mcp_auth_required` card, so a
  // swallow keeps the composer unblocked.
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
          /* workspace-authorize is best-effort; consent lands at run time */
        });
    },
    [onAddCustom],
  );

  const handleAddCustom = useCallback((): void => {
    onAddCustom?.();
  }, [onAddCustom]);

  // Active connector ids → the run's `request_context.connector_scopes` (active
  // → `[]`, enabled with no extra scopes). Omitted entirely when nothing is
  // active so a default run body carries no connector-scope payload.
  const connectorScopes = useMemo<
    ConversationConnectorScopes | undefined
  >(() => {
    if (activeConnectorIds.length === 0) {
      return undefined;
    }
    const scopes: Record<string, readonly string[] | null> = {};
    for (const id of activeConnectorIds) {
      scopes[id] = [];
    }
    return scopes;
  }, [activeConnectorIds]);

  const toolsTrigger = useMemo<ReactNode | undefined>(() => {
    if (!connectorsPort) {
      return undefined;
    }
    return (
      <DesktopComposerToolsTrigger
        port={connectorsPort}
        open={toolsOpen}
        onOpenChange={setToolsOpen}
        webSearchEnabled={webOn}
        onToggleWebSearch={setWebOn}
        activeConnectorIds={activeConnectorIds}
        onToggleConnector={handleToggleConnector}
        onConnectCatalog={handleConnectCatalog}
        onAddCustom={handleAddCustom}
        disabled={disabled}
      />
    );
  }, [
    connectorsPort,
    toolsOpen,
    webOn,
    activeConnectorIds,
    handleToggleConnector,
    handleConnectCatalog,
    handleAddCustom,
    disabled,
  ]);

  return { toolsTrigger, webSearchEnabled: webOn, connectorScopes };
}

// ---------------------------------------------------------------------------
// The trigger — `ComposerToolsButton` + its `ToolsPopover`, floated above the
// button (no host portal target: the popover renders inline, right-aligned, so
// it never widens the composer bottom bar). A direct crib of the FTUE's
// (non-exported) `FirstRunToolsTrigger`.
// ---------------------------------------------------------------------------

interface DesktopComposerToolsTriggerProps {
  readonly port: ComposerConnectorsPort;
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly webSearchEnabled: boolean;
  readonly onToggleWebSearch: (next: boolean) => void;
  readonly activeConnectorIds: readonly string[];
  readonly onToggleConnector: (serverId: string, active: boolean) => void;
  readonly onConnectCatalog: (entry: FirstRunInstallableConnector) => void;
  readonly onAddCustom: () => void;
  readonly disabled?: boolean;
}

function DesktopComposerToolsTrigger(
  props: DesktopComposerToolsTriggerProps,
): ReactElement {
  const {
    port,
    open,
    onOpenChange,
    webSearchEnabled,
    onToggleWebSearch,
    activeConnectorIds,
    onToggleConnector,
    onConnectCatalog,
    onAddCustom,
    disabled,
  } = props;

  // Badge count from surface state alone (web search + toggled connectors —
  // each active id is by construction a connected row); the popover header
  // recomputes the exact count against the loaded projection.
  const activeCount = (webSearchEnabled ? 1 : 0) + activeConnectorIds.length;

  return (
    <span style={triggerWrapStyle}>
      <ComposerToolsButton
        open={open}
        onClick={() => onOpenChange(!open)}
        activeCount={activeCount}
        disabled={disabled}
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
// never widens the composer bottom bar.
const floatWrapStyle: CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 8px)",
  right: 0,
  zIndex: 50,
};
