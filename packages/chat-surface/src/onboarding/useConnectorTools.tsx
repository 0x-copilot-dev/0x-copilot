// useConnectorTools — THE per-run Tools state machine for every host.
//
// This existed in three near-identical copies: `FirstRunSurface` (the FTUE),
// `useDesktopComposerTools` (the desktop Run composer) and
// `useWebRunComposerTools` (the web one). Each owned its own `webSearchEnabled`,
// `pausedConnectorIds`, `reloadToken`, toggle handler and connect handler, and
// they drifted exactly the way three copies do: the FTUE shipped without the
// refetch-on-connect the desktop composer already had, so finishing OAuth there
// left the row on "Connect" until the app restarted.
//
// What is shared is the STATE MACHINE, not the layout — so this is a hook, and
// the popover row / page row stay separate components. What is host-specific is
// one verb (`ConnectorToolsHostPort.connect`), because performing a connect is
// the only part that genuinely differs between an Electron main-brokered
// loopback and a browser full-page redirect.
//
// Two knobs, both opt-OUTS. Web search defaults ON, and every connected
// connector is live unless the user paused it for this run. The older opt-IN
// model started empty with nothing seeding it from the server list, so a
// connector that Settings reported as connected rendered disabled in the
// composer.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ComposerToolsTrigger } from "./ComposerToolsTrigger";
import type { ConnectorToolsHostPort } from "./ports/ConnectorToolsHostPort";
import type { FirstRunConnectorsPort } from "./ports/FirstRunConnectorsPort";
import type { FirstRunInstallableConnector } from "./projectFirstRunConnectors";

/** Catalog id convention (`McpCatalogEntry`): a seed installs as `seed:<slug>`. */
function seedServerId(slug: string): string {
  return `seed:${slug}`;
}

export interface UseConnectorToolsOptions {
  /**
   * Data surface for the popover — list / catalog / install. Absent ⇒ the host
   * has no connector surface, so `toolsTrigger` is `undefined` and no pill
   * mounts. Owning that rule here keeps every host from re-deciding it (and
   * from inventing a stub port just to satisfy the hook).
   */
  readonly port?: FirstRunConnectorsPort;
  /** How THIS host walks the user through the provider's consent screen. */
  readonly host: ConnectorToolsHostPort;
  /**
   * A connector whose OAuth completed elsewhere (the in-chat `mcp_auth` card).
   * Connected connectors are live by default, so this only clears a stale pause
   * on that id and re-reads the list — there is no activation to seed.
   */
  readonly autoActivateConnectorId?: string | null;
  /**
   * Open the custom-MCP form. Also the routing target for catalog entries
   * flagged `requiresPreRegisteredClient`, because a keyless install of one
   * answers 422 — so the popover must never send those down the connect path.
   */
  readonly onAddCustom?: () => void;
  /**
   * Report a failed connect. Without it the failure is invisible: the row just
   * stops responding, which is how a seed-vs-profile 404 went unnoticed.
   */
  readonly onConnectError?: (displayName: string, message: string) => void;
  readonly disabled?: boolean;
}

export interface ConnectorTools {
  /** Tools pill + portal-safe popover; `undefined` when no `port` was given. */
  readonly toolsTrigger: ReactNode | undefined;
  readonly webSearchEnabled: boolean;
  /** Ids paused for this run → `request_context.paused_connectors`. */
  readonly pausedConnectorIds: readonly string[];
}

export function useConnectorTools(
  options: UseConnectorToolsOptions,
): ConnectorTools {
  const {
    port,
    host,
    autoActivateConnectorId = null,
    onAddCustom,
    onConnectError,
    disabled,
  } = options;

  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [pausedConnectorIds, setPausedConnectorIds] = useState<
    readonly string[]
  >([]);
  // Bumped whenever durable connector state moves under us, so the pill's badge
  // and the open panel both refetch instead of rendering a pre-connect world.
  const [reloadToken, setReloadToken] = useState(0);

  // A connector that just authorized must not stay paused from earlier in the
  // session. Derived during render rather than in an effect: an effect would
  // paint one frame of the stale answer, which is the exact flicker at issue.
  const effectivePausedIds = useMemo<readonly string[]>(
    () =>
      autoActivateConnectorId === null
        ? pausedConnectorIds
        : pausedConnectorIds.filter((id) => id !== autoActivateConnectorId),
    [autoActivateConnectorId, pausedConnectorIds],
  );

  // A host-observed completion (the consent card's OAuth return) is the other
  // way the list moves; re-read on it too.
  useEffect(() => {
    if (autoActivateConnectorId === null) return;
    setReloadToken((n) => n + 1);
  }, [autoActivateConnectorId]);

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

  const handleConnectCatalog = useCallback(
    (entry: FirstRunInstallableConnector): void => {
      // A pre-registered vendor cannot be installed keylessly, so it is routed
      // to the custom-config form instead of being attempted and failing.
      if (entry.requiresPreRegisteredClient) {
        onAddCustom?.();
        return;
      }
      void (async () => {
        try {
          const outcome = await host.connect(entry);
          // Resolved means the round-trip finished (the port's contract), so
          // durable state has moved and the list has to be re-read — this is
          // the step whose absence left the FTUE showing "Connect" forever.
          const serverId = outcome?.serverId ?? seedServerId(entry.slug);
          // A pause left over from an earlier turn must not silently apply to a
          // connector the user just chose to connect.
          setPausedConnectorIds((current) =>
            current.includes(serverId)
              ? current.filter((id) => id !== serverId)
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
    [host, onAddCustom, onConnectError],
  );

  const toolsTrigger = useMemo<ReactNode | undefined>(
    () =>
      port === undefined ? undefined : (
        <ComposerToolsTrigger
          port={port}
          reloadToken={reloadToken}
          webSearchEnabled={webSearchEnabled}
          onToggleWebSearch={setWebSearchEnabled}
          pausedConnectorIds={effectivePausedIds}
          onToggleConnector={handleToggleConnector}
          onConnectCatalog={handleConnectCatalog}
          onAddCustom={() => onAddCustom?.()}
          disabled={disabled}
        />
      ),
    [
      port,
      reloadToken,
      webSearchEnabled,
      effectivePausedIds,
      handleToggleConnector,
      handleConnectCatalog,
      onAddCustom,
      disabled,
    ],
  );

  return {
    toolsTrigger,
    webSearchEnabled,
    pausedConnectorIds: effectivePausedIds,
  };
}
