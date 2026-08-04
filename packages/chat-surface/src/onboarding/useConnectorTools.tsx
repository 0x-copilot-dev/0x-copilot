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
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ConnectSupersededError } from "../destinations/connectors/useConnectFlow";
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
  /** Catalog slug mid-connect, or null. Hosts use it to gate other chrome. */
  readonly connectingSlug: string | null;
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
  // Which catalog slug is mid-connect, or null. Before this the row simply did
  // not change: the browser opened and the popover went on saying "Connect", so
  // the only feedback that anything had happened was the other application
  // appearing on screen.
  const [connectingSlug, setConnectingSlug] = useState<string | null>(null);
  // Set when the user cancels, so the rejection their own Cancel caused is not
  // then reported to them as a connect failure. Same shape `SignInGate` uses:
  // the side that pressed Cancel already knows, so it stays quiet rather than
  // parsing an error message back out across the IPC hop.
  const cancelledRef = useRef(false);
  // Which attempt owns `connectingSlug`. See the same ref in `useConnectFlow`:
  // a monotonic token, because two attempts at the SAME connector are otherwise
  // indistinguishable and the abandoned one clears the live one's spinner.
  const attemptSeqRef = useRef(0);

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
      const myAttempt = ++attemptSeqRef.current;
      cancelledRef.current = false;
      setConnectingSlug(entry.slug);
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
          // A newer connect in this same popover owns `connectingSlug` now;
          // this attempt reporting anything would describe the wrong connector.
          if (attemptSeqRef.current !== myAttempt) return;
          if (error instanceof ConnectSupersededError) {
            // The HOST abandoned this attempt for a newer one — not a failure,
            // and not something the user did. Re-read for the same reason a
            // cancel does: a supersede cannot un-grant an authorization the
            // provider may already have completed.
            setReloadToken((n) => n + 1);
            return;
          }
          if (cancelledRef.current) {
            // The user's own Cancel caused this rejection, so it is not an
            // error to report. Re-read anyway: cancelling cannot un-grant an
            // authorization the provider already completed, so the server —
            // not this guess — decides whether the connector is connected.
            setReloadToken((n) => n + 1);
            return;
          }
          onConnectError?.(
            entry.displayName,
            error instanceof Error ? error.message : String(error),
          );
        } finally {
          // Only the current attempt may clear the shared spinner; a superseded
          // one would blank the newer connect's row.
          if (attemptSeqRef.current === myAttempt) setConnectingSlug(null);
        }
      })();
    },
    [host, onAddCustom, onConnectError],
  );

  const handleCancelConnect = useCallback((): void => {
    if (host.cancel === undefined) return;
    cancelledRef.current = true;
    // The row leaves its connecting state on the connect's rejection, not here
    // — otherwise it would claim to have stopped something that is still
    // running if the abort itself failed.
    void host.cancel().catch(() => {
      // Nothing useful to say: either the flow already finished (so the
      // connect's own result governs) or main is gone, in which case the
      // rejection below restores the row anyway.
    });
  }, [host]);

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
          connectingSlug={connectingSlug}
          onCancelConnect={
            host.cancel === undefined ? undefined : handleCancelConnect
          }
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
      connectingSlug,
      host.cancel,
      handleCancelConnect,
      onAddCustom,
      disabled,
    ],
  );

  return {
    toolsTrigger,
    webSearchEnabled,
    pausedConnectorIds: effectivePausedIds,
    connectingSlug,
  };
}
