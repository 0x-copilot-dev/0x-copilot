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

import {
  CONNECTOR_CHANNELS,
  type ConnectorAuthorizationResult,
} from "../main/connectors/channels";

/**
 * Host plumbing the desktop {@link McpAuthPort} drives. Injected rather than
 * reached for, so the port is testable without an Electron bridge.
 */
export interface DesktopMcpAuthPortDeps {
  /**
   * Main-brokered authorize: loopback + system browser + code exchange. Main
   * picks the OAuth topology, so this passes BOTH identities it holds — the
   * gate's `serverId` and the catalog `slug` — and names no mechanism.
   */
  readonly authorize: (target: {
    readonly slug?: string;
    readonly serverId?: string;
  }) => Promise<ConnectorAuthorizationResult>;
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
  /**
   * Authorization failed. Carries the `serverId` so the host can put THAT
   * card back to `pending`: without it a failure left the card asserting
   * "a browser tab opened" forever, which is what turned a 404 into a
   * silent mystery.
   */
  readonly onError?: (serverId: string, error: unknown) => void;
}

// `NO_CATALOG_IDENTITY` used to live here — the refusal shown when a gate named
// no connector, on the reasoning that the desktop connect flow "is driven by the
// profile catalog". It is gone because that premise is gone: a server id is a
// perfectly good identity on the MCP OAuth route, so a gate without a slug is
// authorized rather than refused.

export function createDesktopMcpAuthPort(
  deps: DesktopMcpAuthPortDeps,
): McpAuthPort {
  function authorize(target: {
    readonly slug?: string;
    readonly serverId?: string;
  }): void {
    // The card is already showing `connecting`, so the id used to undo that on
    // failure must be the one the card is keyed by — the gate's `serverId`.
    const cardId = target.serverId ?? target.slug ?? "";
    void (async () => {
      try {
        const result = await deps.authorize(target);
        // Prefer the id main just confirmed. For an uninstalled suggestion the
        // server row is minted DURING this call, so the id the card knew may
        // not be the one that now exists.
        deps.onConnected(result.server_id ?? cardId);
      } catch (error: unknown) {
        deps.onError?.(cardId, error);
      }
    })();
  }

  function beginAuth(serverId: string, options?: McpAuthBeginOptions): void {
    // Both identities go to main, which resolves the topology. A gate always
    // carries a `serverId`; the `connectorSlug` is what lets a profile-backed
    // connector take the pre-registered-client route. Neither is a mechanism
    // choice, so a missing slug is no longer fatal here — a seed authorizes
    // over MCP OAuth by server id alone.
    authorize({
      serverId,
      ...(options?.connectorSlug != null
        ? { slug: options.connectorSlug }
        : {}),
    });
  }

  function skipAuth(serverId: string): void {
    void deps.recordSkip(serverId).catch((error: unknown) => {
      deps.onError?.(serverId, error);
    });
  }

  function installFromCatalog(slug: string): void {
    // Same call. On desktop, install and authenticate are ONE brokered flow —
    // the backend ensures the server row idempotently before starting OAuth —
    // so there is no separate install step to run first.
    authorize({ slug });
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
  onError?: (serverId: string, error: unknown) => void,
): DesktopMcpAuthPortDeps | undefined {
  const win = window as unknown as { bridge?: Window["bridge"] };
  if (win.bridge === undefined) return undefined;
  const bridge = win.bridge;
  return {
    authorize: (target) =>
      bridge.ipc.invoke<ConnectorAuthorizationResult>(
        CONNECTOR_CHANNELS.authorize,
        target,
      ),
    recordSkip,
    onConnected,
    onError,
  };
}
