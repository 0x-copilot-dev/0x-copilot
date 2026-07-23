// ConnectorsRoute — data binder for the Tools (Connectors) destination.
//
// PRD-11: the destination is a single hairline ROW LIST — no filter tabs, no
// 240px aside. This route:
//   1. Fetches `GET /v1/connectors` and owns loading / error / ready states.
//   2. Opens the `/v1/connectors/stream` SSE channel with exponential-backoff
//      reconnect, tracking the highest `sequence_no` for `?after_sequence=N`.
//   3. Merges connector envelopes into the local list via the pure
//      `applyConnectorEnvelope` adapter.
//   4. Drives the shared <ConnectModal> connect flow through `useConnectFlow`
//      (PRD-11 D4): the host injects `authorize` (open a popup / start OAuth),
//      `addCustomServer` (create an MCP server, return its OAuth url), and
//      `onConnect` (persist the picked access mode). The SSE channel feeds
//      completion back through `flow.markConnected`.
//
// SSE goes through `streamConnectorEvents` (transport port) — no raw
// EventSource.

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
  useConnectFlow,
  type ConnectorAccessPort,
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
  /** Open Settings → Model & behavior from the Tools approval-policy note. */
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
  const [pendingError, setPendingError] = useState<string | null>(null);
  // Route-level banner for an access-mode PATCH failure. The shared
  // ConnectorsDestination already reverts the segment inline; this is the
  // web route's own surface so the failure is visible above the fold.
  const [accessModeError, setAccessModeError] = useState<string | null>(null);

  // Latest ready connectors, mirrored into a ref so the connect flow can
  // resolve a freshly-created connector by slug across an `await` boundary.
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
        setState({ kind: "ready", response, highestSequenceNo: 0 });
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

  // ---- Access-mode PATCH (PRD-06 D4) ---------------------------------
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

  // ---- Connect flow capabilities (PRD-11 D4) -------------------------

  // `authorize`: a catalog pick starts the provider OAuth round-trip and opens
  // a popup (keeping the modal alive); a custom server's OAuth url is opened
  // directly. Completion is reported by the SSE channel via `markConnected`.
  const authorize = useCallback(
    async (request: { slug?: ConnectorSlug; url?: string }): Promise<void> => {
      if (request.slug !== undefined) {
        const res = await startConnectorOAuth(identity, request.slug);
        if (typeof window !== "undefined") {
          window.open(res.authorization_url, "_blank", "noopener,noreferrer");
        }
        return;
      }
      if (request.url !== undefined && typeof window !== "undefined") {
        window.open(request.url, "_blank", "noopener,noreferrer");
      }
    },
    [identity],
  );

  // `addCustomServer`: create the MCP server, then, mirroring
  // `useConnectors.addServer`'s post-create guards, return its OAuth url when
  // the server still needs auth (so the hook opens it via `authorize`). A
  // server needing no auth returns no url → the hook clears pending + closes.
  const addCustomServer = useCallback(
    async (input: CustomServerInput): Promise<{ authorizeUrl?: string }> => {
      const server = await createMcpServer(
        input.url,
        identity,
        input.oauthClient,
      );
      const needsAuth =
        server.auth_mode !== "none" &&
        server.auth_state !== "auth_unsupported" &&
        server.auth_state !== "authenticated";
      if (!needsAuth) return {};
      const auth = await startMcpAuth(server.server_id, identity);
      return { authorizeUrl: auth.auth_url };
    },
    [identity],
  );

  // Terminal Connect — persist the chosen access mode on the connector the
  // OAuth round-trip just created, through the SAME port the segment uses.
  const persistConnect = useCallback(
    async (
      slug: ConnectorSlug,
      permission: ConnectorAccessMode,
    ): Promise<void> => {
      const connector = connectorsRef.current.find((c) => c.slug === slug);
      // Defensive: the row isn't in the list yet — close and let SSE reflect it.
      if (connector === undefined) return;
      await accessPort.setAccessMode(connector.id, permission);
    },
    [accessPort],
  );

  const flow = useConnectFlow({
    authorize,
    addCustomServer,
    onConnect: persistConnect,
  });

  // ---- SSE subscription with exponential-backoff reconnect -----------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  const markConnected = flow.markConnected;
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
          // Connect-flow OAuth completion: the server-side callback inserts the
          // connector row and emits `connector.created` / `status_changed`.
          // When a row lands connected, resolve the flow's pending spinner so
          // the modal auto-advances (catalog → permission) or closes (custom).
          const conn = envelope.connector;
          if (
            conn !== undefined &&
            (envelope.event_type === "connector.created" ||
              conn.status === "connected")
          ) {
            markConnected(conn.slug);
          }
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
  }, [identity, state.kind, markConnected]);

  // ---- Reconnect (FR-4.25) — restart OAuth for an error/expired row --
  const handleReconnect = useCallback(
    async (id: ConnectorId): Promise<void> => {
      const connector = connectorsRef.current.find((c) => c.id === id);
      if (connector === undefined) return;
      setPendingError(null);
      try {
        const res = await startConnectorOAuth(identity, connector.slug);
        if (typeof window !== "undefined") {
          window.location.assign(res.authorization_url);
        }
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not start OAuth flow."));
      }
    },
    [identity],
  );

  // ---- SectionResult wrapper for the destination ---------------------

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

  // ConnectModal catalog — the server-provided available set, straight through.
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
        flexDirection: "column",
        boxSizing: "border-box",
      }}
    >
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
          onConnect={flow.openConnect}
          onOpenConnector={onOpenConnector}
          onOpenWebhooks={onOpenWebhooks}
          onReconnect={(id) => {
            void handleReconnect(id);
          }}
          accessPort={accessPort}
          onOpenApprovalSettings={onOpenApprovalSettings}
          onRetry={() => setReloadToken((t) => t + 1)}
        />
      </div>
      <ConnectModal
        open={flow.open}
        onClose={flow.closeConnect}
        catalog={catalog}
        onSelectEntry={flow.onSelectEntry}
        onConnect={flow.onConnect}
        onAddCustomServer={flow.onAddCustomServer}
        pending={flow.pending}
        error={flow.error}
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
