// ConnectorDetailRoute — `/connectors/<id>` route binder (connectors-prd
// §4.2 + §7.5).
//
// Mounted standalone (full-bleed) from App.tsx when the host routes a
// connector id. Owns:
//   1. Fetching `GET /v1/connectors/{id}` for the connector row + the
//      "Used by" consumer projection.
//   2. Providing callbacks for the destination mutations — scope-patch,
//      disconnect, refresh — and merging the fresh row back into local
//      state after each successful mutation.
//   3. Surfacing pending-error banners for failed mutations without
//      collapsing the detail view.
//
// The detail VIEW component (`<ConnectorDetailView>`, `<ScopeReviewTab>`,
// `<ConsumersTab>`, `<ReadAuditTab>`) ships from `@0x-copilot/
// chat-surface` in a sibling worktree (P11-B-finish). Until that lands,
// the route renders a host-side scaffold that exposes the same
// callbacks via data-testid hooks so the route is exercisable today.
// When the chat-surface component lands, swap the scaffold for the
// package component without changing the binder's data flow.

import { useCallback, useEffect, useState, type ReactElement } from "react";

import type {
  Connector,
  ConnectorAuditResponse,
  ConnectorDetailResponse,
  ConnectorId,
  ConnectorScopeEntry,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  disconnectConnector,
  fetchConnector,
  fetchConnectorAudit,
  patchConnectorScopes,
  refreshConnector,
} from "../../api/connectorsApi";
import { errorMessage } from "../../utils/errors";
import { formatLastSync, statusLabel, statusTone } from "./adapters";

interface ConnectorDetailRouteProps {
  readonly identity: RequestIdentity;
  readonly connectorId: ConnectorId;
  readonly onClose: () => void;
  /** Optional callback when the row should be reflected back into the
   *  list route (e.g. status flipped to disconnected after a disconnect
   *  mutation). */
  readonly onUpdated?: (connector: Connector) => void;
  /** Admin gating for the audit tab — when true, the route fetches and
   *  renders the read-audit log. */
  readonly isAdmin?: boolean;
}

type DetailState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly response: ConnectorDetailResponse };

type Tab = "overview" | "scopes" | "consumers" | "audit";

export function ConnectorDetailRoute({
  identity,
  connectorId,
  onClose,
  onUpdated,
  isAdmin = false,
}: ConnectorDetailRouteProps): ReactElement {
  const [state, setState] = useState<DetailState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [audit, setAudit] = useState<ConnectorAuditResponse | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);

  // ---- Detail fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchConnector(identity, connectorId)
      .then((response) => {
        if (cancelled) return;
        setState({ kind: "ready", response });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load connector."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, connectorId, reloadToken]);

  // ---- Audit fetch (admin + only when the tab is open) --------------
  useEffect(() => {
    if (!isAdmin || tab !== "audit" || state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    setAuditError(null);
    fetchConnectorAudit(identity, connectorId, { limit: 50 })
      .then((res) => {
        if (cancelled) return;
        setAudit(res);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setAuditError(errorMessage(error, "Could not load audit log."));
      });
    return () => {
      cancelled = true;
    };
  }, [identity, connectorId, tab, isAdmin, state.kind]);

  // ---- Mutations ----------------------------------------------------
  const mergeConnector = useCallback(
    (connector: Connector) => {
      setState((prev) =>
        prev.kind === "ready"
          ? { ...prev, response: { ...prev.response, connector } }
          : prev,
      );
      onUpdated?.(connector);
    },
    [onUpdated],
  );

  const handleRefresh = useCallback(async (): Promise<void> => {
    setPendingError(null);
    try {
      const res = await refreshConnector(identity, connectorId);
      mergeConnector(res.connector);
    } catch (error: unknown) {
      setPendingError(errorMessage(error, "Could not refresh connector."));
    }
  }, [identity, connectorId, mergeConnector]);

  const handleDisconnect = useCallback(async (): Promise<void> => {
    setPendingError(null);
    try {
      const res = await disconnectConnector(identity, connectorId);
      mergeConnector(res.connector);
    } catch (error: unknown) {
      setPendingError(errorMessage(error, "Could not disconnect connector."));
    }
  }, [identity, connectorId, mergeConnector]);

  const handlePatchScopes = useCallback(
    async (scopes: ReadonlyArray<ConnectorScopeEntry>): Promise<void> => {
      setPendingError(null);
      try {
        const res = await patchConnectorScopes(identity, connectorId, {
          scopes,
        });
        // 202 ⇒ server is requesting a re-OAuth round-trip; redirect
        // the user. The SSE channel picks up the scope_changed event
        // when the re-OAuth completes.
        if (typeof window !== "undefined" && res.reauth_url) {
          window.location.assign(res.reauth_url);
        }
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not update scopes."));
      }
    },
    [identity, connectorId],
  );

  // ---- Render -------------------------------------------------------

  if (state.kind === "loading") {
    return (
      <div
        data-testid="connector-detail-route"
        data-connector-id={connectorId}
        data-state="loading"
        style={{ padding: 24, fontSize: 13 }}
      >
        Loading connector…
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div
        data-testid="connector-detail-route"
        data-connector-id={connectorId}
        data-state="error"
        role="alert"
        style={{ padding: 24, fontSize: 13 }}
      >
        <div
          style={{ fontWeight: 600, marginBottom: 8 }}
          data-testid="connector-detail-route-error"
        >
          Could not load connector
        </div>
        <div style={{ color: "var(--color-text-muted)", marginBottom: 12 }}>
          {state.message}
        </div>
        <button
          type="button"
          data-testid="connector-detail-route-retry"
          onClick={() => setReloadToken((t) => t + 1)}
        >
          Retry
        </button>
        <button
          type="button"
          data-testid="connector-detail-route-close"
          onClick={onClose}
          style={{ marginLeft: 8 }}
        >
          Back
        </button>
      </div>
    );
  }

  const { connector, consumers } = state.response;
  const tone = statusTone(connector.status);

  return (
    <section
      aria-label={`Connector ${connector.display_name}`}
      data-testid="connector-detail-route"
      data-connector-id={connectorId}
      data-state="ready"
      data-connector-status={connector.status}
      style={{
        padding: 24,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        height: "100%",
        overflow: "auto",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>{connector.display_name}</h2>
          <div
            style={{ fontSize: 12, color: "var(--color-text-muted)" }}
            data-testid="connector-detail-route-status"
            data-tone={tone}
          >
            {statusLabel(connector.status)} ·{" "}
            {formatLastSync(connector.last_sync_at, Date.now())}
          </div>
        </div>
        <button
          type="button"
          data-testid="connector-detail-route-close"
          onClick={onClose}
        >
          Back
        </button>
      </header>

      {pendingError !== null && (
        <div
          role="status"
          data-testid="connector-detail-route-pending-error"
          style={{
            padding: 12,
            border: "1px solid var(--color-border-strong)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {pendingError}
        </div>
      )}

      <nav
        role="tablist"
        aria-label="Connector tabs"
        data-testid="connector-detail-route-tabs"
        style={{ display: "flex", gap: 8 }}
      >
        {(
          [
            ["overview", "Overview"],
            ["scopes", "Scopes"],
            ["consumers", "Used by"],
            ...(isAdmin ? [["audit", "Audit"] as const] : []),
          ] as ReadonlyArray<readonly [Tab, string]>
        ).map(([slug, label]) => (
          <button
            key={slug}
            type="button"
            role="tab"
            aria-selected={tab === slug}
            data-testid={`connector-detail-route-tab-${slug}`}
            data-active={tab === slug ? "true" : "false"}
            onClick={() => setTab(slug)}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <div role="tabpanel" data-testid="connector-detail-route-overview">
          <p>{connector.description}</p>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button
              type="button"
              data-testid="connector-detail-route-refresh"
              onClick={() => {
                void handleRefresh();
              }}
            >
              Refresh
            </button>
            {connector.status !== "disconnected" && (
              <button
                type="button"
                data-testid="connector-detail-route-disconnect"
                onClick={() => {
                  void handleDisconnect();
                }}
              >
                Disconnect
              </button>
            )}
          </div>
        </div>
      )}

      {tab === "scopes" && (
        <div role="tabpanel" data-testid="connector-detail-route-scopes">
          <ScopesPanel
            scopes={connector.scopes}
            onApply={(next) => {
              void handlePatchScopes(next);
            }}
          />
        </div>
      )}

      {tab === "consumers" && (
        <div role="tabpanel" data-testid="connector-detail-route-consumers">
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {consumers.agents.map((ref) => (
              <li
                key={`agent:${ref.id}`}
                data-testid="connector-detail-route-consumer"
                data-kind={ref.kind}
                data-id={ref.id}
              >
                Agent {ref.id}
              </li>
            ))}
            {consumers.tools.map((ref) => (
              <li
                key={`tool:${ref.id}`}
                data-testid="connector-detail-route-consumer"
                data-kind={ref.kind}
                data-id={ref.id}
              >
                Tool {ref.id}
              </li>
            ))}
            {consumers.projects.map((ref) => (
              <li
                key={`project:${ref.id}`}
                data-testid="connector-detail-route-consumer"
                data-kind={ref.kind}
                data-id={ref.id}
              >
                Project {ref.id}
              </li>
            ))}
            <li data-testid="connector-detail-route-chat-count">
              Chats with grant: {consumers.chats_with_grant}
            </li>
          </ul>
        </div>
      )}

      {tab === "audit" && isAdmin && (
        <div role="tabpanel" data-testid="connector-detail-route-audit">
          {auditError !== null ? (
            <div role="alert">{auditError}</div>
          ) : audit === null ? (
            <div>Loading audit…</div>
          ) : audit.entries.length === 0 ? (
            <div data-testid="connector-detail-route-audit-empty">
              No audit entries yet.
            </div>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {audit.entries.map((entry) => (
                <li
                  key={entry.id}
                  data-testid="connector-detail-route-audit-row"
                  data-status={entry.status}
                >
                  {entry.ts} — {entry.endpoint} — {entry.status}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

// --- Local scope-edit scaffold ------------------------------------------

interface ScopesPanelProps {
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
  readonly onApply: (next: ReadonlyArray<ConnectorScopeEntry>) => void;
}

function ScopesPanel({ scopes, onApply }: ScopesPanelProps): ReactElement {
  const [draft, setDraft] =
    useState<ReadonlyArray<ConnectorScopeEntry>>(scopes);

  // Reset the draft when the underlying scope set changes (e.g. after a
  // successful re-OAuth round-trip the SSE channel pushes a refreshed
  // row). Pure idiom — no effect needed because React reuses the
  // reducer state across renders only when the input ref is stable.
  useEffect(() => {
    setDraft(scopes);
  }, [scopes]);

  const toggle = (scope: string): void => {
    setDraft((prev) =>
      prev.map((entry) =>
        entry.scope === scope ? { ...entry, granted: !entry.granted } : entry,
      ),
    );
  };

  return (
    <div>
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {draft.map((entry) => (
          <li
            key={entry.scope}
            data-testid="connector-detail-route-scope"
            data-scope={entry.scope}
            data-granted={entry.granted ? "true" : "false"}
            style={{ padding: "8px 0" }}
          >
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={entry.granted}
                onChange={() => toggle(entry.scope)}
                aria-label={`Scope ${entry.scope}`}
              />
              <span>
                <strong>{entry.scope}</strong>
                <span
                  style={{
                    marginLeft: 8,
                    color: "var(--color-text-muted)",
                    fontSize: 12,
                  }}
                >
                  {entry.description}
                </span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid="connector-detail-route-apply-scopes"
        onClick={() => onApply(draft)}
      >
        Apply scopes
      </button>
    </div>
  );
}
