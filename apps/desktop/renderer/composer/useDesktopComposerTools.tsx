// useDesktopComposerTools — per-run Tools pill state for desktop composers.
//
// The pill is intentionally independent of the `+` attachment menu. Its shared
// trigger uses a body portal, so the 300px controls panel remains clickable
// above the overflow-hidden desktop composer frame.
//
// State is the set of connectors the user PAUSED for this run — not the set they
// activated. A connected connector is live by default, which is what Settings →
// Tools already claims and what the runtime already does, and it means a
// connector that just finished OAuth shows up on rather than silently off. The
// paused ids ride the run body as `request_context.paused_connectors`, the field
// the MCP gate actually reads; `connector_scopes` is left alone so the
// conversation's persisted per-chat scope keeps applying.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ComposerToolsTrigger,
  type ComposerConnectorsPort,
  type FirstRunInstallableConnector,
} from "@0x-copilot/chat-surface";

import { CONNECTOR_CHANNELS } from "../../main/connectors/channels";

export interface UseDesktopComposerToolsOptions {
  readonly connectorsPort?: ComposerConnectorsPort;
  readonly disabled?: boolean;
  /** Route Custom MCP and pre-registered catalog entries to Tools settings. */
  readonly onAddCustom?: () => void;
  /**
   * Report a failed 1-click connect. Without this the failure is invisible:
   * the row simply stops responding, which is how the seed-vs-profile 404 went
   * unnoticed. Hosts wire it to their notification surface.
   */
  readonly onConnectError?: (displayName: string, message: string) => void;
  /**
   * Connector whose OAuth flow just completed. Connected connectors are live by
   * default, so this only needs to clear a stale pause on that id and refresh
   * the list — there is no activation to seed.
   */
  readonly autoActivateConnectorId?: string | null;
}

export interface DesktopComposerTools {
  /** Tools pill + portal-safe popover, omitted when the adapter is unavailable. */
  readonly toolsTrigger: ReactNode | undefined;
  readonly webSearchEnabled: boolean;
  /** Ids the user paused for this run → `request_context.paused_connectors`. */
  readonly pausedConnectorIds: readonly string[];
}

export function useDesktopComposerTools(
  options: UseDesktopComposerToolsOptions,
): DesktopComposerTools {
  const {
    connectorsPort,
    disabled,
    onAddCustom,
    onConnectError,
    autoActivateConnectorId = null,
  } = options;
  const [webOn, setWebOn] = useState(true);
  const [pausedConnectorIds, setPausedConnectorIds] = useState<
    readonly string[]
  >([]);
  // Bumped whenever durable connector state moves under us, so the pill's badge
  // and the open panel both refetch instead of showing a pre-connect world.
  const [reloadToken, setReloadToken] = useState(0);

  // A connector that just authorized must not stay paused from earlier in the
  // session, and the list has to be re-read for it to appear at all. Derived
  // during render rather than in an effect: an effect would paint one frame of
  // the stale answer, which is the exact flicker this fix is about.
  const effectivePausedIds = useMemo<readonly string[]>(
    () =>
      autoActivateConnectorId === null
        ? pausedConnectorIds
        : pausedConnectorIds.filter((id) => id !== autoActivateConnectorId),
    [autoActivateConnectorId, pausedConnectorIds],
  );

  const handleToggleConnector = useCallback(
    (serverId: string, active: boolean): void => {
      setPausedConnectorIds((current) =>
        active
          ? current.filter((id) => id !== serverId)
          : current.includes(serverId)
            ? current
            : [...current, serverId],
      );
    },
    [],
  );

  // Electron MAIN brokers OAuth in the system browser. No token crosses IPC.
  //
  // The popover lists `mcp_catalog` seeds, so a catalog row is installed as an
  // MCP SERVER first — that mint is what gives the connector a `server_id` to
  // authorize by. Both identities then go to `connector.authorize`, which picks
  // the route: a seed has no `desktop_profiles.yaml` entry and authorizes over
  // MCP OAuth, which is what this button needed to do all along for Linear and
  // Notion.
  //
  // Failures are reported. The original `.catch(() => {})` swallowed them, so a
  // 404 presented as a button that did not respond, with no error anywhere and
  // no request in the HTTP logs.
  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      if (entry.requiresPreRegisteredClient) {
        onAddCustom?.();
        return;
      }
      const win = window as unknown as { bridge?: Window["bridge"] };
      if (win.bridge === undefined || connectorsPort === undefined) return;
      const bridge = win.bridge;
      void (async () => {
        try {
          const server = await connectorsPort.installFromCatalog(entry.slug);
          await bridge.ipc.invoke(CONNECTOR_CHANNELS.authorize, {
            slug: entry.slug,
            serverId: server.server_id,
          });
          // `connector.authorize` resolves only once the OAuth round-trip
          // finishes (see `ConnectorService.authorize`), so this is the
          // completion signal: refetch, and the row moves from "Add a
          // connector" to "Connected" — already on — in place. A pause left
          // over from an earlier turn would otherwise silently apply to a
          // connector the user just chose to connect.
          setPausedConnectorIds((current) =>
            current.includes(server.server_id)
              ? current.filter((id) => id !== server.server_id)
              : current,
          );
          setReloadToken((n) => n + 1);
        } catch (error: unknown) {
          onConnectError?.(
            entry.displayName,
            error instanceof Error ? error.message : String(error),
          );
        }
      })();
    },
    [connectorsPort, onAddCustom, onConnectError],
  );

  // A host-observed completion (the consent card's OAuth return) is the other
  // way the list moves; refetch on it too.
  useEffect(() => {
    if (autoActivateConnectorId === null) return;
    setReloadToken((n) => n + 1);
  }, [autoActivateConnectorId]);

  const toolsTrigger = useMemo<ReactNode | undefined>(() => {
    if (connectorsPort === undefined) return undefined;
    return (
      <ComposerToolsTrigger
        port={connectorsPort}
        reloadToken={reloadToken}
        webSearchEnabled={webOn}
        onToggleWebSearch={setWebOn}
        pausedConnectorIds={effectivePausedIds}
        onToggleConnector={handleToggleConnector}
        onConnectCatalog={handleConnectCatalog}
        onAddCustom={() => onAddCustom?.()}
        disabled={disabled}
      />
    );
  }, [
    connectorsPort,
    reloadToken,
    webOn,
    effectivePausedIds,
    handleToggleConnector,
    handleConnectCatalog,
    onAddCustom,
    disabled,
  ]);

  return {
    toolsTrigger,
    webSearchEnabled: webOn,
    pausedConnectorIds: effectivePausedIds,
  };
}
