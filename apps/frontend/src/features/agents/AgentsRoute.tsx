// AgentsRoute — data binder for the Phase 8 Agents destination
// (the 14th destination per
// `docs/atlas-new-design/destinations/agents-prd.md`).
//
// Mirrors the P6-C ProjectsRoute / P5-C RoutinesRoute pattern:
//   1. Fetches `GET /v1/agents` via `agentsApi` and owns
//      loading / error / ready states (sub-PRD §4.1 list view).
//   2. Opens the `/v1/agents/stream` SSE channel (sub-PRD §4.12)
//      with exponential-backoff reconnect, tracking the highest
//      `sequence_no` for `?after_sequence=N` resume (cross-audit §5.2).
//   3. Merges `agent_installed` / `agent_uninstalled` /
//      `agent_updated` / `agent_status_changed` envelopes into the
//      local list — installs and status flips refetch the affected
//      row so the merged-overrides view stays correct (sub-PRD §3.3).
//   4. Proxies install / uninstall mutations back to the backend,
//      optimistically driving the SSE-merged local list while the
//      server confirms.
//   5. Renders a host-side scaffolding today with a detail panel
//      that surfaces the editor + version history + per-agent usage.
//      The package-shipped `<AgentsDestination>` exists as a Wave-0
//      placeholder; this route is the feature-binder that adds the
//      real fetch / mutate / SSE behaviour the destination
//      component does not own.
//
// Why a feature-level wrapper, not props on `<AgentsDestination>`
// today: the package component is intentionally a dignified
// placeholder (cross-audit + impl-plan §1.6). Owning the data flow
// + state mutation + SSE here lets the destination component reshape
// without forcing an App.tsx-level rewrite — same compromise the
// InboxRoute / TodosRoute / RoutinesRoute / ProjectsRoute waves made.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import type { RequestIdentity } from "../../api/config";
import {
  duplicateAgent,
  fetchAgent,
  fetchAgents,
  fetchAgentUsage,
  fetchAgentVersions,
  installAgent,
  patchAgent,
  snapshotAgentVersion,
  streamAgentEvents,
  uninstallAgent,
} from "../../api/agentsApi";
import type {
  Agent,
  AgentId,
  AgentListResponse,
  AgentStreamEnvelope,
  AgentUsageResponse,
  AgentVersion,
  AgentVersionListResponse,
} from "../../api/_agents-stub";
import { errorMessage } from "../../utils/errors";

/** Reconnect backoff bounds (mirrors ProjectsRoute / RoutinesRoute). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface AgentsRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<Agent>;
      readonly highestSequenceNo: number;
    };

/**
 * Apply one durable SSE envelope to the local agents list. Pure
 * function so a test can drive it without a mounted component.
 *
 * Semantics (sub-PRD §4.12 event types):
 * - `agent_installed`           → no-op at list layer; caller refetches the affected row.
 * - `agent_uninstalled`         → no-op at list layer; caller refetches the affected row.
 * - `agent_updated`             → no-op at list layer; caller refetches the affected row.
 * - `agent_version_snapshot`    → no-op (version row, not agent row).
 * - `agent_status_changed`      → in-place status flip when row is present.
 *
 * For every event type we leave list mutation that affects the
 * merged-overrides view to a refetch effect in the component — the
 * status flip is the only one that can be reflected purely from the
 * envelope payload itself.
 */
export function applyAgentEnvelope(
  items: ReadonlyArray<Agent>,
  envelope: AgentStreamEnvelope,
): ReadonlyArray<Agent> {
  if (envelope.event_type !== "agent_status_changed") {
    return items;
  }
  const idx = items.findIndex((a) => a.id === envelope.agent_id);
  if (idx === -1) {
    return items;
  }
  const payload = envelope.payload as {
    readonly status?: Agent["status"];
  };
  if (payload.status === undefined) {
    return items;
  }
  const next = items.slice();
  next[idx] = { ...next[idx], status: payload.status };
  return next;
}

export function AgentsRoute({ identity }: AgentsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<AgentId | null>(null);

  // ---- Initial fetch ------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchAgents(identity, { limit: 50 })
      .then((list: AgentListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.items,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load agents."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- SSE subscription with exponential-backoff reconnect ---------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") {
      return;
    }
    let cancelled = false;
    let activeHandle: { close(): void } | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamAgentEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyAgentEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
          });

          // Refetch-driven merge for events that touch the merged-
          // overrides view (sub-PRD §3.3). The reducer can't synthesize
          // these because the install state + 7d usage projection are
          // server-computed.
          if (
            envelope.event_type === "agent_installed" ||
            envelope.event_type === "agent_uninstalled" ||
            envelope.event_type === "agent_updated"
          ) {
            void fetchAgent(identity, envelope.agent_id)
              .then((agent) => {
                if (cancelled) return;
                setState((prev) => {
                  if (prev.kind !== "ready") return prev;
                  const idx = prev.items.findIndex((a) => a.id === agent.id);
                  if (idx === -1) {
                    return { ...prev, items: [agent, ...prev.items] };
                  }
                  const next = prev.items.slice();
                  next[idx] = agent;
                  return { ...prev, items: next };
                });
              })
              .catch(() => undefined);
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
  }, [identity, state.kind]);

  // ---- Mutation helpers (install / uninstall / duplicate / patch) --

  const mergeUpdated = useCallback((updated: Agent): void => {
    setState((prev) => {
      if (prev.kind !== "ready") return prev;
      const idx = prev.items.findIndex((a) => a.id === updated.id);
      if (idx === -1) {
        return { ...prev, items: [updated, ...prev.items] };
      }
      const next = prev.items.slice();
      next[idx] = updated;
      return { ...prev, items: next };
    });
  }, []);

  const handleInstall = useCallback(
    async (id: AgentId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await installAgent(identity, id);
        mergeUpdated(updated);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not install agent."));
      }
    },
    [identity, mergeUpdated],
  );

  const handleUninstall = useCallback(
    async (id: AgentId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await uninstallAgent(identity, id);
        mergeUpdated(updated);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not uninstall agent."));
      }
    },
    [identity, mergeUpdated],
  );

  const handleDuplicate = useCallback(
    async (id: AgentId): Promise<void> => {
      setPendingError(null);
      try {
        const forked = await duplicateAgent(identity, id);
        mergeUpdated(forked);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not duplicate agent."));
      }
    },
    [identity, mergeUpdated],
  );

  // ---- Render -------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Agents destination"
        data-testid="agents-route"
        data-state="error"
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          boxSizing: "border-box",
          backgroundColor: "var(--color-bg)",
          color: "var(--color-text)",
        }}
      >
        <div
          role="alert"
          data-testid="agents-route-error"
          style={{
            border: "1px solid var(--color-border)",
            borderRadius: 12,
            backgroundColor: "var(--color-surface)",
            padding: 32,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            maxWidth: 480,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load agents
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="agents-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="agents-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={{
              height: 32,
              padding: "0 14px",
              borderRadius: 8,
              border: "1px solid var(--color-border-strong)",
              backgroundColor: "transparent",
              color: "var(--color-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  const items = state.kind === "ready" ? state.items : [];
  const selected =
    selectedId !== null
      ? (items.find((a) => a.id === selectedId) ?? null)
      : null;

  return (
    <section
      aria-label="Agents destination"
      data-testid="agents-route"
      data-state={state.kind}
      data-item-count={items.length}
      style={{
        height: "100%",
        width: "100%",
        display: "flex",
        gap: 0,
        boxSizing: "border-box",
      }}
    >
      {/* List pane */}
      <div
        data-testid="agents-route-list-pane"
        style={{
          flex: selected !== null ? "0 0 360px" : "1 1 auto",
          overflow: "auto",
          padding: 24,
          boxSizing: "border-box",
          borderRight:
            selected !== null ? "1px solid var(--color-border)" : "none",
        }}
      >
        {pendingError !== null && (
          <div
            role="status"
            data-testid="agents-route-pending-error"
            style={{
              marginBottom: 16,
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
        {state.kind === "loading" ? (
          <div data-testid="agents-route-loading" style={{ fontSize: 13 }}>
            Loading agents…
          </div>
        ) : items.length === 0 ? (
          <div
            data-testid="agents-route-empty"
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
          >
            No agents yet.
          </div>
        ) : (
          <ul
            data-testid="agents-route-list"
            style={{ listStyle: "none", margin: 0, padding: 0 }}
          >
            {items.map((agent) => (
              <li
                key={agent.id}
                data-testid="agents-route-row"
                data-agent-id={agent.id}
                data-agent-status={agent.status}
                data-agent-origin={agent.origin}
                style={{
                  padding: "12px 0",
                  borderBottom: "1px solid var(--color-border)",
                  display: "flex",
                  gap: 12,
                  alignItems: "center",
                }}
              >
                <button
                  type="button"
                  data-testid="agents-route-select"
                  data-agent-id={agent.id}
                  onClick={() => setSelectedId(agent.id)}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    textAlign: "left",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    padding: 0,
                    color: "inherit",
                  }}
                >
                  <div style={{ fontSize: 14, fontWeight: 600 }}>
                    <span aria-hidden="true" style={{ marginRight: 6 }}>
                      {agent.icon_emoji}
                    </span>
                    {agent.name}
                  </div>
                  <div
                    style={{ fontSize: 12, color: "var(--color-text-muted)" }}
                  >
                    {agent.origin} · v{agent.version} · {agent.status}
                  </div>
                </button>
                {agent.viewer_install_status === "installed" ? (
                  <button
                    type="button"
                    data-testid="agents-route-uninstall"
                    data-agent-id={agent.id}
                    onClick={() => {
                      void handleUninstall(agent.id);
                    }}
                  >
                    Uninstall
                  </button>
                ) : (
                  <button
                    type="button"
                    data-testid="agents-route-install"
                    data-agent-id={agent.id}
                    onClick={() => {
                      void handleInstall(agent.id);
                    }}
                  >
                    Install
                  </button>
                )}
                <button
                  type="button"
                  data-testid="agents-route-duplicate"
                  data-agent-id={agent.id}
                  onClick={() => {
                    void handleDuplicate(agent.id);
                  }}
                >
                  Duplicate
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Detail pane (editor + version history + usage) */}
      {selected !== null && (
        <AgentDetailPanel
          identity={identity}
          agent={selected}
          onClose={() => setSelectedId(null)}
          onSaved={mergeUpdated}
          onError={(msg) => setPendingError(msg)}
        />
      )}
    </section>
  );
}

// ===========================================================================
// Detail panel — editor + version history + per-agent usage
// ===========================================================================

interface AgentDetailPanelProps {
  readonly identity: RequestIdentity;
  readonly agent: Agent;
  readonly onClose: () => void;
  readonly onSaved: (agent: Agent) => void;
  readonly onError: (message: string) => void;
}

function AgentDetailPanel({
  identity,
  agent,
  onClose,
  onSaved,
  onError,
}: AgentDetailPanelProps): ReactElement {
  // Editor — keyed by agent id so switching the selection resets the form.
  const [draftInstructions, setDraftInstructions] = useState(
    agent.instructions,
  );
  const [versionLabel, setVersionLabel] = useState("");
  const editorKey = agent.id;
  // Sync the draft instructions when the upstream agent record changes
  // (e.g. an SSE-driven refetch lands while the detail pane is open).
  useEffect(() => {
    setDraftInstructions(agent.instructions);
  }, [editorKey, agent.instructions]);

  const handleSave = useCallback(async (): Promise<void> => {
    try {
      const updated = await patchAgent(identity, agent.id, {
        instructions: draftInstructions,
      });
      onSaved(updated);
    } catch (error: unknown) {
      onError(errorMessage(error, "Could not save agent."));
    }
  }, [identity, agent.id, draftInstructions, onSaved, onError]);

  const handleSnapshot = useCallback(async (): Promise<void> => {
    try {
      await snapshotAgentVersion(identity, agent.id, {
        label: versionLabel.length > 0 ? versionLabel : undefined,
      });
      setVersionLabel("");
      // Refresh the agent record so the bumped version reflects in the
      // header — the explicit fetch sidesteps waiting for the SSE delta.
      const refreshed = await fetchAgent(identity, agent.id);
      onSaved(refreshed);
    } catch (error: unknown) {
      onError(errorMessage(error, "Could not snapshot agent version."));
    }
  }, [identity, agent.id, versionLabel, onSaved, onError]);

  return (
    <div
      data-testid="agents-route-detail"
      data-agent-id={agent.id}
      style={{
        flex: "1 1 auto",
        overflow: "auto",
        padding: 24,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <header
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>
            <span aria-hidden="true" style={{ marginRight: 8 }}>
              {agent.icon_emoji}
            </span>
            {agent.name}
          </div>
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
            {agent.origin} · v{agent.version} · {agent.status}
          </div>
        </div>
        <button
          type="button"
          data-testid="agents-route-detail-close"
          onClick={onClose}
        >
          Close
        </button>
      </header>

      {/* Editor */}
      <div>
        <label
          htmlFor={`agents-instructions-${agent.id}`}
          style={{ fontSize: 12, fontWeight: 600 }}
        >
          Instructions
        </label>
        <textarea
          id={`agents-instructions-${agent.id}`}
          data-testid="agents-route-instructions"
          value={draftInstructions}
          onChange={(e) => setDraftInstructions(e.target.value)}
          rows={8}
          style={{
            width: "100%",
            marginTop: 4,
            boxSizing: "border-box",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
          }}
        />
        <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="agents-route-save"
            onClick={() => {
              void handleSave();
            }}
          >
            Save
          </button>
          <input
            type="text"
            data-testid="agents-route-version-label"
            placeholder="Optional version label"
            value={versionLabel}
            onChange={(e) => setVersionLabel(e.target.value)}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            data-testid="agents-route-snapshot"
            onClick={() => {
              void handleSnapshot();
            }}
          >
            Snapshot version
          </button>
        </div>
      </div>

      {/* Version history */}
      <AgentVersionHistory identity={identity} agentId={agent.id} />

      {/* Usage chart (minimal numbers; full chart lands in a follow-up wave) */}
      <AgentUsageBlock identity={identity} agentId={agent.id} />
    </div>
  );
}

// ===========================================================================
// Version history block — fetches `GET /v1/agents/{id}/versions`
// ===========================================================================

function AgentVersionHistory({
  identity,
  agentId,
}: {
  readonly identity: RequestIdentity;
  readonly agentId: AgentId;
}): ReactElement {
  const [state, setState] = useState<
    | { readonly kind: "loading" }
    | { readonly kind: "error"; readonly message: string }
    | { readonly kind: "ready"; readonly items: ReadonlyArray<AgentVersion> }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchAgentVersions(identity, agentId, { limit: 20 })
      .then((res: AgentVersionListResponse) => {
        if (cancelled) return;
        setState({ kind: "ready", items: res.items });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load version history."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, agentId]);

  return (
    <section data-testid="agents-route-versions" data-state={state.kind}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0" }}>
        Version history
      </h3>
      {state.kind === "loading" ? (
        <div style={{ fontSize: 12 }}>Loading versions…</div>
      ) : state.kind === "error" ? (
        <div
          role="alert"
          data-testid="agents-route-versions-error"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          {state.message}
        </div>
      ) : state.items.length === 0 ? (
        <div
          data-testid="agents-route-versions-empty"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          No snapshots yet.
        </div>
      ) : (
        <ul
          data-testid="agents-route-versions-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {state.items.map((v) => (
            <li
              key={v.id}
              data-testid="agents-route-version-row"
              data-version={v.version}
              style={{
                padding: "6px 0",
                borderBottom: "1px solid var(--color-border)",
                fontSize: 12,
              }}
            >
              v{v.version}
              {v.label !== null ? ` — ${v.label}` : ""}
              <span style={{ color: "var(--color-text-muted)", marginLeft: 8 }}>
                {v.created_at}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ===========================================================================
// Usage block — fetches `GET /v1/agents/{id}/usage` on selection
// ===========================================================================

function AgentUsageBlock({
  identity,
  agentId,
}: {
  readonly identity: RequestIdentity;
  readonly agentId: AgentId;
}): ReactElement {
  const [state, setState] = useState<
    | { readonly kind: "loading" }
    | { readonly kind: "error"; readonly message: string }
    | { readonly kind: "ready"; readonly response: AgentUsageResponse }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchAgentUsage(identity, agentId, { period: "week" })
      .then((response: AgentUsageResponse) => {
        if (cancelled) return;
        setState({ kind: "ready", response });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load usage."),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [identity, agentId]);

  return (
    <section data-testid="agents-route-usage" data-state={state.kind}>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "8px 0" }}>
        Usage (last 7 days)
      </h3>
      {state.kind === "loading" ? (
        <div style={{ fontSize: 12 }}>Loading usage…</div>
      ) : state.kind === "error" ? (
        <div
          role="alert"
          data-testid="agents-route-usage-error"
          style={{ fontSize: 12, color: "var(--color-text-muted)" }}
        >
          {state.message}
        </div>
      ) : (
        <div
          data-testid="agents-route-usage-totals"
          data-run-count={state.response.totals.run_count}
          data-cost-usd-micro={state.response.totals.cost_usd_micro}
          style={{
            fontSize: 12,
            color: "var(--color-text-muted)",
            display: "flex",
            gap: 16,
          }}
        >
          <span>
            Runs:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.totals.run_count}
            </strong>
          </span>
          <span>
            Tokens in:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.totals.token_in}
            </strong>
          </span>
          <span>
            Tokens out:{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.totals.token_out}
            </strong>
          </span>
          <span>
            Cost (μUSD):{" "}
            <strong style={{ color: "var(--color-text)" }}>
              {state.response.totals.cost_usd_micro}
            </strong>
          </span>
        </div>
      )}
    </section>
  );
}
