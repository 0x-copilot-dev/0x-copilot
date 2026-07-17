// ToolsRoute — data binder for the Phase 10 Tools destination (the 11th
// destination per `docs/atlas-new-design/destinations/tools-prd.md`).
//
// Mirrors the P8-C AgentsRoute / P6-C ProjectsRoute / P5-C RoutinesRoute
// pattern:
//   1. Fetches `GET /v1/tools` via `toolsApi` and owns loading / error /
//      ready states (tools-prd §4.1 catalog list view).
//   2. Opens the `/v1/tools/stream` SSE channel (tools-prd §4.10) with
//      exponential-backoff reconnect, tracking the highest
//      `sequence_no` for `?after_sequence=N` resume (cross-audit §5.2).
//   3. Merges `tool.created` / `tool.updated` / `tool.deleted` /
//      `tool.error_threshold` envelopes into the local list via the
//      pure `applyToolEnvelope` adapter; `tool.invoked` events drive a
//      refetch of the affected row so the usage projection refreshes
//      without a parallel tracker (cross-audit §5.5 TU-1).
//   4. Owns selected-tool + onboarding-mode local state. The detail
//      pane (P10-C/§4) and the onboarding wizard (P10-C/§5) are
//      mounted as in-pane sub-views — the route is the data binder, so
//      ToolDetailRoute / ToolOnboardingRoute receive identity + id
//      props rather than re-deriving them from URL state.
//
// Why a feature-level wrapper, not props on `<ToolsDestination>` today:
// the package component currently renders a skills placeholder while
// the Phase 10 Tools components (sub-PRD §7) ship in sibling worktrees
// (P10-B1/B2/B3). Owning the data flow + state mutation + SSE here
// lets the destination component reshape without forcing an
// App.tsx-level rewrite — same compromise the InboxRoute /
// AgentsRoute / RoutinesRoute waves made.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";

import type {
  Tool,
  ToolId,
  ToolKind,
  ToolListResponse,
  ToolScope,
  ToolStatus,
  ToolStreamEnvelope,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  deleteTool as apiDeleteTool,
  disableTool as apiDisableTool,
  enableTool as apiEnableTool,
  fetchTool,
  fetchTools,
  openToolStream,
  type ToolStream,
} from "../../api/toolsApi";
import { errorMessage } from "../../utils/errors";
import { applyToolEnvelope, toolToListRow } from "./adapters";
import { ToolDetailRoute } from "./ToolDetailRoute";
import { ToolOnboardingRoute } from "./ToolOnboardingRoute";

/** Reconnect backoff bounds (mirrors AgentsRoute / RoutinesRoute). */
const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface ToolsRouteProps {
  readonly identity: RequestIdentity;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<Tool>;
      readonly highestSequenceNo: number;
    };

type PaneMode =
  | { readonly kind: "list" }
  | { readonly kind: "detail"; readonly toolId: ToolId }
  | { readonly kind: "onboard" };

interface FilterState {
  readonly kind: ToolKind | "all";
  readonly scope: ToolScope | "all";
  readonly status: ToolStatus | "all";
  readonly search: string;
}

const INITIAL_FILTERS: FilterState = {
  kind: "all",
  scope: "all",
  status: "all",
  search: "",
};

// ===========================================================================
// ToolsRoute
// ===========================================================================

export function ToolsRoute({ identity }: ToolsRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [pane, setPane] = useState<PaneMode>({ kind: "list" });
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS);

  // ---- Initial fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchTools(identity, { limit: 50 })
      .then((list: ToolListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.tools,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load tools."),
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
    let activeHandle: ToolStream | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = openToolStream({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope: ToolStreamEnvelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyToolEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
          });

          // For `tool.invoked` the wire envelope carries the invocation
          // (not the tool row) — the usage projection on the matched row
          // only refreshes when we refetch the detail. Keep the fetch
          // narrow: just the affected row.
          if (
            envelope.event_type === "tool.invoked" &&
            envelope.invocation !== undefined
          ) {
            const toolId = envelope.invocation.tool_id;
            void fetchTool(identity, toolId)
              .then((detail) => {
                if (cancelled) return;
                setState((prev) => {
                  if (prev.kind !== "ready") return prev;
                  const idx = prev.items.findIndex(
                    (t) => t.id === detail.tool.id,
                  );
                  if (idx === -1) return prev;
                  const next = prev.items.slice();
                  next[idx] = detail.tool;
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

  // ---- Mutation helpers (disable / enable / delete) ------------------

  const mergeUpdated = useCallback((updated: Tool): void => {
    setState((prev) => {
      if (prev.kind !== "ready") return prev;
      const idx = prev.items.findIndex((t) => t.id === updated.id);
      if (idx === -1) {
        return { ...prev, items: [updated, ...prev.items] };
      }
      const next = prev.items.slice();
      next[idx] = updated;
      return { ...prev, items: next };
    });
  }, []);

  const dropRow = useCallback((id: ToolId): void => {
    setState((prev) => {
      if (prev.kind !== "ready") return prev;
      return { ...prev, items: prev.items.filter((t) => t.id !== id) };
    });
  }, []);

  const handleDisable = useCallback(
    async (id: ToolId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await apiDisableTool(identity, id);
        mergeUpdated(updated);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not disable tool."));
      }
    },
    [identity, mergeUpdated],
  );

  const handleEnable = useCallback(
    async (id: ToolId): Promise<void> => {
      setPendingError(null);
      try {
        const updated = await apiEnableTool(identity, id);
        mergeUpdated(updated);
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not enable tool."));
      }
    },
    [identity, mergeUpdated],
  );

  const handleDelete = useCallback(
    async (id: ToolId): Promise<void> => {
      setPendingError(null);
      try {
        await apiDeleteTool(identity, id);
        dropRow(id);
        // If the deleted tool was open in the detail pane, close it.
        setPane((prev) =>
          prev.kind === "detail" && prev.toolId === id
            ? { kind: "list" }
            : prev,
        );
      } catch (error: unknown) {
        setPendingError(errorMessage(error, "Could not delete tool."));
      }
    },
    [identity, dropRow],
  );

  // ---- Filter pipeline (pure local view shaping) ---------------------

  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const needle = filters.search.trim().toLowerCase();
    return state.items.filter((t) => {
      if (filters.kind !== "all" && t.kind !== filters.kind) return false;
      if (filters.scope !== "all" && t.scope !== filters.scope) return false;
      if (filters.status !== "all" && t.status !== filters.status) return false;
      if (needle.length > 0) {
        const haystack =
          `${t.name} ${t.description} ${t.tags.join(" ")}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [state, filters]);

  // ---- Render --------------------------------------------------------

  if (state.kind === "error") {
    return (
      <section
        aria-label="Tools destination"
        data-testid="tools-route"
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
          data-testid="tools-route-error"
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
            Could not load tools
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="tools-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="tools-route-retry"
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

  // Onboarding pane fully replaces the list view (matches the wizard's
  // "full-bleed flow" pattern from sub-PRD §7).
  if (pane.kind === "onboard") {
    return (
      <ToolOnboardingRoute
        identity={identity}
        onCancel={() => setPane({ kind: "list" })}
        onCreated={(tool) => {
          mergeUpdated(tool);
          setPane({ kind: "detail", toolId: tool.id });
        }}
      />
    );
  }

  const items = state.kind === "ready" ? state.items : [];
  const rowsToRender = filtered.map(toolToListRow);

  return (
    <section
      aria-label="Tools destination"
      data-testid="tools-route"
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
      {/* List pane (always rendered; collapses to a narrower column when
          a detail pane is open). */}
      <div
        data-testid="tools-route-list-pane"
        style={{
          flex: pane.kind === "detail" ? "0 0 360px" : "1 1 auto",
          overflow: "auto",
          padding: 24,
          boxSizing: "border-box",
          borderRight:
            pane.kind === "detail" ? "1px solid var(--color-border)" : "none",
        }}
      >
        <ToolsListFilterBar
          filters={filters}
          onChange={setFilters}
          onAddNew={() => setPane({ kind: "onboard" })}
        />
        {pendingError !== null && (
          <div
            role="status"
            data-testid="tools-route-pending-error"
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
          <div data-testid="tools-route-loading" style={{ fontSize: 13 }}>
            Loading tools…
          </div>
        ) : rowsToRender.length === 0 ? (
          <div
            data-testid="tools-route-empty"
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
          >
            {items.length === 0
              ? "No tools yet."
              : "No tools match your filters."}
          </div>
        ) : (
          <ul
            data-testid="tools-route-list"
            style={{ listStyle: "none", margin: 0, padding: 0 }}
          >
            {rowsToRender.map((row) => (
              <li
                key={row.id}
                data-testid="tools-route-row"
                data-tool-id={row.id}
                data-tool-status={row.status}
                data-tool-kind={row.kind}
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
                  data-testid="tools-route-select"
                  data-tool-id={row.id}
                  onClick={() =>
                    setPane({ kind: "detail", toolId: row.id as ToolId })
                  }
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
                    {row.name}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {row.kind} · {row.scope} · {row.status}
                    {row.last_used_label !== null
                      ? ` · used ${row.last_used_label}`
                      : ""}
                  </div>
                </button>
                {row.status === "enabled" ? (
                  <button
                    type="button"
                    data-testid="tools-route-disable"
                    data-tool-id={row.id}
                    onClick={() => {
                      void handleDisable(row.id as ToolId);
                    }}
                  >
                    Disable
                  </button>
                ) : row.status === "disabled" ? (
                  <button
                    type="button"
                    data-testid="tools-route-enable"
                    data-tool-id={row.id}
                    onClick={() => {
                      void handleEnable(row.id as ToolId);
                    }}
                  >
                    Enable
                  </button>
                ) : null}
                <button
                  type="button"
                  data-testid="tools-route-delete"
                  data-tool-id={row.id}
                  onClick={() => {
                    void handleDelete(row.id as ToolId);
                  }}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Detail pane (mounted only when a tool is selected). */}
      {pane.kind === "detail" && (
        <ToolDetailRoute
          identity={identity}
          toolId={pane.toolId}
          onClose={() => setPane({ kind: "list" })}
          onUpdated={mergeUpdated}
          onError={(msg) => setPendingError(msg)}
        />
      )}
    </section>
  );
}

// ===========================================================================
// Filter bar — minimal host-side controls so the route renders end-to-end
// without depending on chat-surface ToolsPanel landing first.
// ===========================================================================

interface ToolsListFilterBarProps {
  readonly filters: FilterState;
  readonly onChange: (next: FilterState) => void;
  readonly onAddNew: () => void;
}

function ToolsListFilterBar({
  filters,
  onChange,
  onAddNew,
}: ToolsListFilterBarProps): ReactElement {
  return (
    <div
      data-testid="tools-route-filter-bar"
      style={{
        display: "flex",
        gap: 8,
        marginBottom: 16,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <input
        type="search"
        aria-label="Search tools"
        data-testid="tools-route-search"
        placeholder="Search tools"
        value={filters.search}
        onChange={(e) => onChange({ ...filters, search: e.target.value })}
        style={{ flex: 1, minWidth: 160 }}
      />
      <select
        data-testid="tools-route-filter-kind"
        value={filters.kind}
        onChange={(e) =>
          onChange({ ...filters, kind: e.target.value as FilterState["kind"] })
        }
        aria-label="Filter by kind"
      >
        <option value="all">All kinds</option>
        <option value="mcp">MCP</option>
        <option value="openapi">OpenAPI</option>
        <option value="builtin">Built-in</option>
        <option value="code">Code</option>
        <option value="skill">Skill</option>
      </select>
      <select
        data-testid="tools-route-filter-status"
        value={filters.status}
        onChange={(e) =>
          onChange({
            ...filters,
            status: e.target.value as FilterState["status"],
          })
        }
        aria-label="Filter by status"
      >
        <option value="all">All statuses</option>
        <option value="enabled">Enabled</option>
        <option value="disabled">Disabled</option>
        <option value="error">Error</option>
        <option value="pending_review">Pending review</option>
      </select>
      <button
        type="button"
        data-testid="tools-route-add-new"
        onClick={onAddNew}
      >
        New tool
      </button>
    </div>
  );
}
