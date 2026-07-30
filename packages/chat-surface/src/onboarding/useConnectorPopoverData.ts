// useConnectorPopoverData — the ONE fetch + projection behind the composer's
// Tools surface.
//
// This used to live inside `ToolsPopoverContent`, which meant only the OPEN
// panel knew what was connected. The pill badge beside it counted
// `activeConnectorIds.length` instead, so the two halves of the same control
// answered from different data: the badge could say "Tools 1" (web search) while
// the panel listed a connected Linear. Hoisting the fetch to the parent gives
// the badge and the rows one projection, one load state, one refresh.
//
// `reloadToken` is the refresh seam. The list is durable state that changes
// out-of-band — finishing OAuth installs and authenticates a server — and a
// mounted popover has no way to hear about it. Hosts bump the token when they
// observe a connect complete and the row moves from "Add a connector" to
// "Connected", already on, without the user reopening the panel.

import { useEffect, useState } from "react";

import type { McpCatalogEntry, McpServer } from "@0x-copilot/api-types";

import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import {
  projectFirstRunConnectors,
  type FirstRunConnectedConnector,
  type FirstRunInstallableConnector,
} from "./projectFirstRunConnectors";

export type ConnectorPopoverLoadState =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | {
      readonly status: "ready";
      readonly servers: readonly McpServer[];
      readonly catalog: readonly McpCatalogEntry[];
    }
  | { readonly status: "error" };

export interface ConnectorPopoverData {
  readonly state: ConnectorPopoverLoadState;
  readonly connected: readonly FirstRunConnectedConnector[];
  readonly installable: readonly FirstRunInstallableConnector[];
}

/** Stable identities so a not-ready render doesn't invalidate caller memos. */
const NO_CONNECTED: readonly FirstRunConnectedConnector[] = Object.freeze([]);
const NO_INSTALLABLE: readonly FirstRunInstallableConnector[] = Object.freeze(
  [],
);

export interface ConnectorPopoverDataOptions {
  /** Bump to refetch — a connect completed, so durable state moved. */
  readonly reloadToken?: number;
  /**
   * `false` keeps the hook idle and issues no request. The composer pill needs
   * the data while CLOSED (its badge counts connected connectors), but a
   * standalone dialog should not fetch until it opens.
   */
  readonly enabled?: boolean;
}

export function useConnectorPopoverData(
  port: FirstRunConnectorsPort,
  options: ConnectorPopoverDataOptions = {},
): ConnectorPopoverData {
  const { reloadToken = 0, enabled = true } = options;
  const [state, setState] = useState<ConnectorPopoverLoadState>({
    status: "idle",
  });

  // No "already loaded" ref guard here. Both hosts mount under <StrictMode>, so
  // this effect is deliberately double-invoked: a ref guard set on the first run
  // makes the second run bail out, while the first run's cleanup has already set
  // `cancelled = true` and suppressed its own result — leaving the popover stuck
  // on "Loading connectors…" forever. The `cancelled` flag below is the whole
  // StrictMode contract: the discarded pass resolves into nothing and the live
  // pass sets the state.
  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setState({ status: "loading" });
    Promise.all([port.listServers(), port.listCatalog()])
      .then(([servers, catalog]) => {
        if (!cancelled) {
          setState({ status: "ready", servers, catalog });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({ status: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, port, reloadToken]);

  if (state.status !== "ready") {
    return { state, connected: NO_CONNECTED, installable: NO_INSTALLABLE };
  }
  const projection = projectFirstRunConnectors(state.servers, state.catalog);
  return {
    state,
    connected: projection.connected,
    installable: projection.installable,
  };
}
