// ConnectorsRoute — data binder for the Phase 11 Connectors destination
// (the 13th destination per
// `docs/atlas-new-design/destinations/connectors-prd.md`).
//
// Mirrors the P10-C ToolsRoute pattern:
//   1. Fetches `GET /v1/connectors` via `connectorsApi.fetchConnectors`
//      and owns loading / error / ready states (connectors-prd §4.1).
//   2. Opens the `/v1/connectors/stream` SSE channel (connectors-prd §4.9)
//      with exponential-backoff reconnect (1s → 30s), tracking the
//      highest `sequence_no` for `?after_sequence=N` resume (cross-audit
//      §5.2).
//   3. Merges `connector.created` / `connector.status_changed` /
//      `connector.scope_changed` / `connector.error_threshold` /
//      `heartbeat` envelopes into the local list via the pure
//      `applyConnectorEnvelope` adapter shipped in P11-C-part-1.
//   4. Owns the active filter (Connected / Available / Custom) +
//      selected-connector pane state. The detail pane and the webhooks
//      manager are sibling routes mounted via the host App.tsx; this
//      route is just the list binder.
//
// SSE goes through `streamConnectorEvents` (which uses
// `getAppTransport().subscribeServerSentEvents` internally). No raw
// EventSource — substrate substitution rides through the transport
// port so the desktop substrate can swap in its own implementation.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  ConnectModal,
  ConnectorsDestination,
  ConnectorsPanel,
  type ConnectorAccessPort,
  type ConnectorsFilterCounts,
  type ConnectorsFilterSlug,
  type CustomServerInput,
} from "@0x-copilot/chat-surface";
import type {
  Connector,
  ConnectorAccessMode,
  ConnectorCatalogEntry,
  ConnectorId,
  ConnectorListResponse,
  ConnectorSlug,
  ConnectorStreamEnvelope,
  SectionResult,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  type ConnectorEventsStream,
  fetchConnectors,
  setConnectorAccessMode,
  startConnectorOAuth,
  streamConnectorEvents,
} from "../../api/connectorsApi";
import { createMcpServer, startMcpAuth } from "../../api/mcpApi";
import { errorMessage } from "../../utils/errors";
import { applyConnectorEnvelope } from "./adapters";

/** Reconnect backoff bounds (mirrors AgentsRoute / RoutinesRoute / ToolsRoute). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface ConnectorsRouteProps {
  readonly identity: RequestIdentity;
  /** Open the detail sub-route for the given connector id. */
  readonly onOpenConnector?: (id: ConnectorId) => void;
  /** Open the webhooks sub-route. */
  readonly onOpenWebhooks?: () => void;
  /**
   * Open Settings → Model & behavior from the Tools approval-policy note
   * (FR-4.25). The actual destination dispatch is wired by the App shell in
   * PR-4.11; until then hosts may pass a callback (or omit it, in which case
   * the note renders as plain text).
   */
  readonly onOpenApprovalSettings?: () => void;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly response: ConnectorListResponse;
      readonly highestSequenceNo: number;
    };

// ===========================================================================
// ConnectorsRoute
// ===========================================================================

export function ConnectorsRoute({
  identity,
  onOpenConnector,
  onOpenWebhooks,
  onOpenApprovalSettings,
}: ConnectorsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [filter, setFilter] = useState<ConnectorsFilterSlug>("connected");
  const [pendingError, setPendingError] = useState<string | null>(null);

  // ---- Connect flow (FR-4.23) — ConnectModal is host-driven -----------
  const [connectOpen, setConnectOpen] = useState(false);
  const [connectPending, setConnectPending] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  // Route-level banner for an access-mode PATCH failure. The shared
  // ConnectorsDestination already reverts the segment inline; this is the
  // web route's own surface so the failure is visible above the fold.
  const [accessModeError, setAccessModeError] = useState<string | null>(null);
  // Slug the OAuth round-trip is currently authorizing. The SSE channel
  // reports completion (`connector.created` / `status_changed` → connected)
  // for this slug, which clears `connectPending` and advances the modal to
  // the permission step. A ref (not state) so the SSE effect closure — keyed
  // on `[identity, state.kind]` — always sees the latest value without
  // re-subscribing.
  const connectingSlugRef = useRef<ConnectorSlug | null>(null);
  // Custom-server add (D1) in flight. Unlike a catalog pick there is no slug
  // to match against, so completion is the first `connector.created` (or
  // connected `status_changed`) envelope observed while the custom add is
  // pending — the backend's MCP-registration write-through emits it. A ref
  // for the same reason as `connectingSlugRef`.
  const customConnectRef = useRef(false);

  // Latest ready connectors, mirrored into a ref so the optimistic access-
  // mode revert can snapshot the prior mode and the connect flow can resolve
  // a freshly-created connector by slug — both across an `await` boundary
  // without a stale render closure.
  const connectorsRef = useRef<ReadonlyArray<Connector>>([]);
  useEffect(() => {
    if (state.kind === "ready") {
      connectorsRef.current = state.response.connectors;
    }
  }, [state]);

  // ---- Initial fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchConnectors(identity, { limit: 50 })
      .then((response) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          response,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load connectors."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // Resolve the connect-flow OAuth round-trip from a streamed envelope.
  // Stable identity (refs + stable setters only) so the SSE effect can list
  // it as a dependency without re-subscribing.
  const maybeCompleteConnect = useCallback(
    (envelope: ConnectorStreamEnvelope): void => {
      const conn = envelope.connector;
      // Custom-server add: no slug is known up front, so the first created/
      // connected envelope while the add is pending resolves it (the modal
      // then closes — the custom flow has no permission step).
      if (customConnectRef.current) {
        if (conn === undefined) return;
        if (
          envelope.event_type === "connector.created" ||
          conn.status === "connected"
        ) {
          customConnectRef.current = false;
          setConnectPending(false);
          setConnectError(null);
        }
        return;
      }
      const slug = connectingSlugRef.current;
      if (slug === null) return;
      if (conn === undefined || conn.slug !== slug) return;
      if (
        envelope.event_type === "connector.created" ||
        conn.status === "connected"
      ) {
        connectingSlugRef.current = null;
        setConnectPending(false);
        setConnectError(null);
      }
    },
    [],
  );

  // ---- SSE subscription with exponential-backoff reconnect -----------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: ConnectorEventsStream | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamConnectorEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope: ConnectorStreamEnvelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const connectors = applyConnectorEnvelope(
              prev.response.connectors,
              envelope,
            );
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            if (connectors === prev.response.connectors) {
              return { ...prev, highestSequenceNo };
            }
            return {
              ...prev,
              response: { ...prev.response, connectors },
              highestSequenceNo,
            };
          });
          // Connect-flow OAuth completion: the server-side callback inserts
          // the connector row and emits `connector.created` /
          // `status_changed`. When the row for the slug we're authorizing
          // lands connected, clear `connectPending` so the ConnectModal
          // auto-advances catalog → OAuth spinner → permission (FR-4.23).
          maybeCompleteConnect(envelope);
        },
        onError: () => {
          if (cancelled) return;
          activeHandle?.close();
          activeHandle = null;
          const delay = backoffRef.current;
          backoffRef.current = Math.min(
            backoffRef.current * 2,
            RECONNECT_BACKOFF_MAX_MS,
          );
          reconnectTimer = setTimeout(open, delay);
        },
      });
    }

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
  }, [identity, state.kind, maybeCompleteConnect]);

  // ---- Mutations -----------------------------------------------------

  // "Connect a tool" CTA — open the ConnectModal (FR-4.23). The catalog it
  // shows is the server-provided `available` set (generic-SaaS-first, no
  // hardcoded Safe/Dune defaults — FR-4.24).
  const handleOpenConnect = useCallback(() => {
    connectingSlugRef.current = null;
    customConnectRef.current = false;
    setConnectError(null);
    setConnectPending(false);
    setConnectOpen(true);
  }, []);

  const handleCloseConnect = useCallback(() => {
    connectingSlugRef.current = null;
    customConnectRef.current = false;
    setConnectOpen(false);
    setConnectPending(false);
    setConnectError(null);
  }, []);

  // Access-mode PATCH (FR-4.22, PRD-06 D4) — the optimistic-apply / revert /
  // error-banner state machine now lives ONCE inside `ConnectorsDestination`.
  // The host supplies only this single-method port; on success it also merges
  // the reconciled row into the local list so the SSE-fed state stays truthful.
  const accessPort = useMemo<ConnectorAccessPort>(
    () => ({
      setAccessMode: async (id: ConnectorId, mode: ConnectorAccessMode) => {
        setAccessModeError(null);
        let res;
        try {
          res = await setConnectorAccessMode(identity, id, {
            access_mode: mode,
          });
        } catch (error: unknown) {
          // Surface the failure at the route level, then re-throw so the
          // shared segment performs its optimistic revert (DoD 12).
          setAccessModeError(
            errorMessage(error, "Could not change the access mode."),
          );
          throw error;
        }
        setState((prev) => {
          if (prev.kind !== "ready") return prev;
          const connectors = prev.response.connectors.map((c) =>
            c.id === id ? res.connector : c,
          );
          return { ...prev, response: { ...prev.response, connectors } };
        });
        return res.connector;
      },
    }),
    [identity],
  );

  // Connect flow — catalog pick starts the provider OAuth round-trip in a
  // popup and flips the modal into its spinner state (`pending`). Completion
  // is reported by the SSE channel (`maybeCompleteConnect`), which clears
  // `pending` so the modal advances to the permission step.
  const handleConnectSelectEntry = useCallback(
    async (slug: ConnectorSlug): Promise<void> => {
      connectingSlugRef.current = slug;
      setConnectError(null);
      setConnectPending(true);
      try {
        const res = await startConnectorOAuth(identity, slug);
        // Keep the modal alive: authorize in a popup rather than a full-page
        // redirect. The server-side callback inserts the connector and the
        // SSE `connector.created` event resolves the pending state.
        if (typeof window !== "undefined") {
          window.open(res.authorization_url, "_blank", "noopener,noreferrer");
        }
      } catch (error: unknown) {
        connectingSlugRef.current = null;
        setConnectPending(false);
        setConnectError(errorMessage(error, "Could not start the OAuth flow."));
      }
    },
    [identity],
  );

  // Custom-server add (Decision D1) — create the MCP server from the URL (+
  // optional pre-registered OAuth client), then, mirroring
  // `useConnectors.addServer`'s post-create guards, kick off the MCP OAuth
  // round-trip in a popup so the modal stays alive. Completion signals:
  //   • auth needed  → the SSE `connector.created` write-through envelope
  //     clears `pending` (`maybeCompleteConnect`), which closes the modal.
  //   • no auth      → the create alone completes; clear `pending` now.
  const handleAddCustomServer = useCallback(
    async (input: CustomServerInput): Promise<void> => {
      connectingSlugRef.current = null;
      customConnectRef.current = false;
      setConnectError(null);
      setConnectPending(true);
      try {
        const server = await createMcpServer(
          input.url,
          identity,
          input.oauthClient,
        );
        const needsAuth =
          server.auth_mode !== "none" &&
          server.auth_state !== "auth_unsupported" &&
          server.auth_state !== "authenticated";
        if (!needsAuth) {
          // Install alone completes the add — clear `pending` so the modal
          // closes; the SSE write-through lands the row when it arrives.
          setConnectPending(false);
          return;
        }
        customConnectRef.current = true;
        const auth = await startMcpAuth(server.server_id, identity);
        // Keep the modal alive: authorize in a popup (same pattern as the
        // catalog pick). The server-side callback flips the MCP server to
        // authenticated and the connector write-through emits the SSE
        // envelope that resolves the pending state.
        if (typeof window !== "undefined") {
          window.open(auth.auth_url, "_blank", "noopener,noreferrer");
        }
      } catch (error: unknown) {
        customConnectRef.current = false;
        setConnectPending(false);
        setConnectError(
          errorMessage(error, "Could not add the custom server."),
        );
      }
    },
    [identity],
  );

  // Terminal Connect — persist the chosen access mode on the connector the
  // OAuth round-trip just created, then close the modal. When the row isn't
  // in the list yet (defensive), close and let the SSE reflect it.
  const handleConnectConfirm = useCallback(
    async (
      slug: ConnectorSlug,
      permission: ConnectorAccessMode,
    ): Promise<void> => {
      const connector = connectorsRef.current.find((c) => c.slug === slug);
      if (connector === undefined) {
        handleCloseConnect();
        return;
      }
      setConnectPending(true);
      setConnectError(null);
      try {
        // Terminal Connect persists the chosen mode through the SAME port the
        // segment uses (PRD-06 D4) — one write path, reconciled into state.
        await accessPort.setAccessMode(connector.id, permission);
        handleCloseConnect();
      } catch (error: unknown) {
        setConnectPending(false);
        setConnectError(errorMessage(error, "Could not connect the tool."));
      }
    },
    [identity, handleCloseConnect],
  );

  const handleOpenCatalogEntry = useCallback(
    async (slug: ConnectorSlug): Promise<void> => {
      setPendingError(null);
      try {
        const res = await startConnectorOAuth(identity, slug);
        // OAuth completes through the existing /mcp/oauth/callback path
        // (connectors-prd §4.3 alias). Driving the user to the
        // authorization URL is the side-effect; the SSE channel picks up
        // the connector.created event on completion.
        if (typeof window !== "undefined") {
          window.location.assign(res.authorization_url);
        }
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not start OAuth flow."));
      }
    },
    [identity],
  );

  const handleReconnect = useCallback(
    async (id: ConnectorId): Promise<void> => {
      if (state.kind !== "ready") return;
      const connector = state.response.connectors.find((c) => c.id === id);
      if (connector === undefined) return;
      await handleOpenCatalogEntry(connector.slug);
    },
    [handleOpenCatalogEntry, state],
  );

  // ---- Counts + SectionResult wrapper for the destination ------------

  const counts = useMemo<ConnectorsFilterCounts>(() => {
    if (state.kind !== "ready") {
      return { connected: 0, available: 0, custom: 0 };
    }
    return {
      connected: state.response.connectors.length,
      available: state.response.available.length,
      custom: 0,
    };
  }, [state]);

  const items = useMemo<SectionResult<{
    readonly connectors: ReadonlyArray<Connector>;
    readonly available: ConnectorListResponse["available"];
  }> | null>(() => {
    if (state.kind === "loading") return null;
    if (state.kind === "error") {
      return { status: "error", error: state.message };
    }
    return {
      status: "ok",
      data: {
        connectors: state.response.connectors,
        available: state.response.available,
      },
    };
  }, [state]);

  const totalItems =
    state.kind === "ready"
      ? state.response.connectors.length + state.response.available.length
      : 0;

  // ConnectModal catalog — the server-provided available set, straight
  // through. Generic-SaaS-first; Safe/Dune are ordinary catalog rows, never
  // defaults (FR-4.24).
  const catalog = useMemo<ReadonlyArray<ConnectorCatalogEntry>>(
    () => (state.kind === "ready" ? state.response.available : []),
    [state],
  );

  // ---- Render --------------------------------------------------------

  return (
    <section
      aria-label="Connectors destination"
      data-testid="connectors-route"
      data-state={state.kind}
      data-item-count={totalItems}
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        gap: 0,
        boxSizing: "border-box",
      }}
    >
      <aside
        data-testid="connectors-route-panel"
        style={{
          flex: "0 0 240px",
          borderRight: "1px solid var(--color-border)",
          overflow: "auto",
        }}
      >
        <ConnectorsPanel
          filter={filter}
          onFilterChange={setFilter}
          counts={counts}
          onConnect={handleOpenConnect}
          onOpenWebhooks={onOpenWebhooks}
        />
      </aside>
      <div
        data-testid="connectors-route-main"
        style={{ flex: "1 1 auto", overflow: "auto" }}
      >
        {pendingError !== null && (
          <div
            role="status"
            data-testid="connectors-route-pending-error"
            style={inlineErrorStyle}
          >
            {pendingError}
          </div>
        )}
        {accessModeError !== null && (
          <div
            role="alert"
            data-testid="connectors-route-access-mode-error"
            style={inlineErrorStyle}
          >
            {accessModeError}
          </div>
        )}
        <ConnectorsDestination
          items={items}
          filter={filter}
          onFilterChange={setFilter}
          counts={counts}
          onConnect={handleOpenConnect}
          onOpenConnector={onOpenConnector}
          onOpenCatalogEntry={(slug) => {
            void handleOpenCatalogEntry(slug);
          }}
          onReconnect={(id) => {
            void handleReconnect(id);
          }}
          accessPort={accessPort}
          onOpenApprovalSettings={onOpenApprovalSettings}
          onRetry={() => setReloadToken((t) => t + 1)}
        />
      </div>
      <ConnectModal
        open={connectOpen}
        onClose={handleCloseConnect}
        catalog={catalog}
        onSelectEntry={(slug) => {
          void handleConnectSelectEntry(slug);
        }}
        onConnect={(slug, permission) => {
          void handleConnectConfirm(slug, permission);
        }}
        onAddCustomServer={(input) => {
          void handleAddCustomServer(input);
        }}
        pending={connectPending}
        error={connectError}
      />
    </section>
  );
}

const inlineErrorStyle: CSSProperties = {
  margin: 16,
  padding: 12,
  border: "1px solid var(--color-border-strong)",
  borderRadius: 8,
  backgroundColor: "var(--color-surface)",
  fontSize: 13,
};
