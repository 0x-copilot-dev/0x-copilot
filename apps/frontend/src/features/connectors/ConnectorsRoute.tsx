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
  type ReactElement,
} from "react";

import {
  ConnectorsDestination,
  ConnectorsPanel,
  type ConnectorsFilterCounts,
  type ConnectorsFilterSlug,
} from "@0x-copilot/chat-surface";
import type {
  Connector,
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
  startConnectorOAuth,
  streamConnectorEvents,
} from "../../api/connectorsApi";
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
}: ConnectorsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [filter, setFilter] = useState<ConnectorsFilterSlug>("connected");
  const [pendingError, setPendingError] = useState<string | null>(null);

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
  }, [identity, state.kind]);

  // ---- Mutations -----------------------------------------------------
  const handleConnect = useCallback(() => {
    // For "Connect a connector" without a slug pre-selected, route to the
    // Available tab so the user picks. The wizard for new-slug installs
    // lands in a follow-up wave.
    setFilter("available");
  }, []);

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
          onConnect={handleConnect}
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
            style={{
              margin: 16,
              padding: 12,
              border: "1px solid var(--color-border-strong)",
              borderRadius: 8,
              backgroundColor: "var(--color-surface)",
              fontSize: 13,
            }}
          >
            {pendingError}
          </div>
        )}
        <ConnectorsDestination
          items={items}
          filter={filter}
          onFilterChange={setFilter}
          counts={counts}
          onConnect={handleConnect}
          onOpenConnector={onOpenConnector}
          onOpenCatalogEntry={(slug) => {
            void handleOpenCatalogEntry(slug);
          }}
          onReconnect={(id) => {
            void handleReconnect(id);
          }}
          onRetry={() => setReloadToken((t) => t + 1)}
        />
      </div>
    </section>
  );
}
