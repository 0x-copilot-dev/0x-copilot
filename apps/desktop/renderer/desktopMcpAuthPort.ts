// The DESKTOP host implementation of `McpAuthPort` — the in-chat connector
// consent card's Connect / Deny.
//
// chat-surface defines the port type and the card calls it; the web half has
// existed since WC-P5b. Desktop shipped without one, so the card rendered and
// its buttons did nothing: `actionable` is `mcpAuthPort !== undefined`, and it
// was undefined. Everything this needs already existed in the main process —
// `ConnectorOAuthCoordinator` binds an ephemeral loopback, opens the system
// browser, races a deep-link delivery of the same state, and posts the code
// back — exposed to the renderer as the `connector.connect` IPC. The only
// missing piece was the identity hop; see `connectorSlug` below.
//
// WHY THIS LOOKS NOTHING LIKE THE WEB PORT. Web redirects the whole page and
// resumes from a `sessionStorage` breadcrumb after the app reloads. Desktop
// never leaves: the renderer is DENIED `window.open` and `openExternal` on
// purpose, so main owns the browser and the loopback, and the IPC call simply
// RESOLVES when OAuth completes. That is why this port can report success
// directly through `onConnected` while the web host has to recover it from a
// callback route.
//
// WHAT STAYS IN MAIN: the loopback bind, the system-browser open, the state
// demux, and the code→token exchange. A provider token never crosses into the
// renderer — the connect resolves to safe connection metadata only.

import type {
  McpAuthBeginOptions,
  McpAuthPort,
} from "@0x-copilot/chat-surface";
import type { DesktopConnectorConnectionResult } from "@0x-copilot/api-types";

import { CONNECTOR_CHANNELS } from "../main/connectors/channels";

/**
 * Host plumbing the desktop {@link McpAuthPort} drives. Injected rather than
 * reached for, so the port is testable without an Electron bridge.
 */
export interface DesktopMcpAuthPortDeps {
  /** Main-brokered connect: loopback + system browser + code exchange. */
  readonly connect: (slug: string) => Promise<DesktopConnectorConnectionResult>;
  /**
   * Record that the user declined this connector for the run, so the agent
   * does not re-prompt. Best-effort — a discovery suggestion has no persisted
   * approval row to resolve, so a rejection is swallowed.
   */
  readonly recordSkip: (serverId: string) => Promise<void>;
  /**
   * OAuth returned successfully for this server. Feeds the cockpit's consent
   * machine so the card reaches `connected` instead of sitting at `connecting`
   * — the one transition nothing else can observe.
   */
  readonly onConnected: (serverId: string) => void;
  /** Surface a failure to the host. The card's actions are fire-and-forget. */
  readonly onError?: (error: unknown) => void;
}

/**
 * Raised when a gate names no connector. Not a bug and not a crash: a custom
 * MCP server (one the user added by URL) has no catalog identity, and the
 * desktop connect flow is driven by the profile catalog. Saying so is better
 * than opening a browser at something that cannot complete.
 */
export const NO_CATALOG_IDENTITY =
  "This connector isn’t in the desktop catalog, so it can’t be connected here yet.";

export function createDesktopMcpAuthPort(
  deps: DesktopMcpAuthPortDeps,
): McpAuthPort {
  function connectBySlug(slug: string, serverId: string | null): void {
    void (async () => {
      try {
        const result = await deps.connect(slug);
        // Prefer the id the backend just confirmed. For an uninstalled
        // suggestion the server row is minted DURING this call, so the id the
        // card knew may not be the one that now exists.
        deps.onConnected(result.server_id ?? serverId ?? slug);
      } catch (error: unknown) {
        deps.onError?.(error);
      }
    })();
  }

  function beginAuth(serverId: string, options?: McpAuthBeginOptions): void {
    const slug = options?.connectorSlug ?? null;
    if (slug === null) {
      // The identity hop failed upstream. Report it rather than falling back to
      // the `server_id`: the slug-keyed endpoint would 404 on a profile lookup,
      // which reads to the user as a broken button rather than an absent one.
      deps.onError?.(new Error(NO_CATALOG_IDENTITY));
      return;
    }
    connectBySlug(slug, serverId);
  }

  function skipAuth(serverId: string): void {
    void deps.recordSkip(serverId).catch((error: unknown) => {
      deps.onError?.(error);
    });
  }

  function installFromCatalog(slug: string): void {
    // Same call. On desktop, install and authenticate are ONE brokered flow —
    // the backend ensures the server row idempotently before starting OAuth —
    // so there is no separate install step to run first.
    connectBySlug(slug, null);
  }

  return { beginAuth, skipAuth, installFromCatalog };
}

/**
 * Bind the port to the Electron bridge. Returns `undefined` outside Electron
 * (MockTransport dev, tests), which the cockpit already handles by rendering
 * the gate visible-but-inert.
 */
export function bridgeMcpAuthDeps(
  onConnected: (serverId: string) => void,
  recordSkip: (serverId: string) => Promise<void>,
  onError?: (error: unknown) => void,
): DesktopMcpAuthPortDeps | undefined {
  const win = window as unknown as { bridge?: Window["bridge"] };
  if (win.bridge === undefined) return undefined;
  const bridge = win.bridge;
  return {
    connect: (slug: string) =>
      bridge.ipc.invoke<DesktopConnectorConnectionResult>(
        CONNECTOR_CHANNELS.connect,
        { slug },
      ),
    recordSkip,
    onConnected,
    onError,
  };
}
