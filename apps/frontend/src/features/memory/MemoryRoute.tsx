// MemoryRoute — data binder for the Phase 12 Memory destination
// (sub-PRD §4.2 / §7.2). Same shape as TeamRoute / ToolsRoute /
// ConnectorsRoute: list + filter + SSE merge.

import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import type {
  MemoryItem,
  MemoryItemId,
  MemoryKind,
  MemoryListResponse,
  MemoryScope,
  MemoryStreamEnvelope,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  deleteMemory as apiDeleteMemory,
  fetchMemory,
  streamMemoryEvents,
  type MemoryEventsStream,
} from "../../api/memoryApi";
import { errorMessage } from "../../utils/errors";
import { applyMemoryEnvelope, memoryToListRow } from "./adapters";

const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface MemoryRouteProps {
  readonly identity: RequestIdentity;
  readonly onOpenItem: (id: MemoryItemId) => void;
  readonly onOpenProposals: () => void;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<MemoryItem>;
      readonly highestSequenceNo: number;
    };

interface FilterState {
  readonly kind: MemoryKind | "all";
  readonly scope: MemoryScope | "all";
  readonly search: string;
}

const INITIAL_FILTERS: FilterState = {
  kind: "all",
  scope: "all",
  search: "",
};

export function MemoryRoute({
  identity,
  onOpenItem,
  onOpenProposals,
}: MemoryRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS);

  // ---- Initial fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchMemory(identity, { limit: 50 })
      .then((list: MemoryListResponse) => {
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
          message: errorMessage(error, "Could not load memory."),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [identity, reloadToken]);

  // ---- SSE with exponential-backoff reconnect ------------------------
  const backoffRef = useRef(RECONNECT_BACKOFF_MIN_MS);
  useEffect(() => {
    if (state.kind !== "ready") return;
    let cancelled = false;
    let activeHandle: MemoryEventsStream | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamMemoryEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope: MemoryStreamEnvelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyMemoryEnvelope(prev.items, envelope);
            const highestSequenceNo = Math.max(
              prev.highestSequenceNo,
              envelope.sequence_no,
            );
            return { kind: "ready", items, highestSequenceNo };
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
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      activeHandle?.close();
    };
  }, [identity, state.kind]);

  // ---- Mutations -----------------------------------------------------
  const handleDelete = async (id: MemoryItemId): Promise<void> => {
    setPendingError(null);
    try {
      await apiDeleteMemory(identity, id);
      setState((prev) =>
        prev.kind === "ready"
          ? { ...prev, items: prev.items.filter((m) => m.id !== id) }
          : prev,
      );
    } catch (err) {
      setPendingError(errorMessage(err, "Could not delete memory."));
    }
  };

  // ---- Filter pipeline -----------------------------------------------
  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const needle = filters.search.trim().toLowerCase();
    return state.items.filter((m) => {
      if (filters.kind !== "all" && m.kind !== filters.kind) return false;
      if (filters.scope !== "all" && m.scope !== filters.scope) return false;
      if (needle.length > 0) {
        const haystack =
          `${m.title} ${m.body} ${m.tags.join(" ")}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [state, filters]);

  // ---- Render --------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Memory destination"
        data-testid="memory-route"
        data-state="error"
        style={errorShellStyle}
      >
        <div
          role="alert"
          data-testid="memory-route-error"
          style={errorCardStyle}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load memory
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="memory-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="memory-route-retry"
            onClick={() => setReloadToken((t) => t + 1)}
            style={retryButtonStyle}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  const items = state.kind === "ready" ? state.items : [];
  const rows = filtered.map(memoryToListRow);

  return (
    <section
      aria-label="Memory destination"
      data-testid="memory-route"
      data-state={state.kind}
      data-item-count={items.length}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Memory</h2>
        <input
          type="search"
          data-testid="memory-route-search"
          aria-label="Search memory"
          placeholder="Search…"
          value={filters.search}
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, search: e.target.value }))
          }
          style={searchInputStyle}
        />
        <select
          data-testid="memory-route-kind-filter"
          aria-label="Filter by kind"
          value={filters.kind}
          onChange={(e) =>
            setFilters((prev) => ({
              ...prev,
              kind: e.target.value as FilterState["kind"],
            }))
          }
        >
          <option value="all">All kinds</option>
          <option value="skill">Skills</option>
          <option value="fact">Facts</option>
          <option value="preference">Preferences</option>
        </select>
        <button
          type="button"
          data-testid="memory-route-open-proposals"
          onClick={onOpenProposals}
          style={proposalsButtonStyle}
        >
          Proposals
        </button>
      </header>
      {pendingError !== null && (
        <div
          role="status"
          data-testid="memory-route-pending-error"
          style={pendingErrorStyle}
        >
          {pendingError}
        </div>
      )}
      {state.kind === "loading" ? (
        <div data-testid="memory-route-loading" style={{ padding: 16 }}>
          Loading memory…
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="memory-route-empty"
          style={{ padding: 16, color: "var(--color-text-muted)" }}
        >
          {items.length === 0
            ? "No memory rows yet."
            : "No memory matches your filters."}
        </div>
      ) : (
        <ul
          data-testid="memory-route-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {rows.map((row) => (
            <li
              key={row.id}
              data-testid="memory-route-row"
              data-memory-id={row.id}
              data-kind={row.kind}
              data-scope={row.scope}
              style={rowStyle}
            >
              <button
                type="button"
                data-testid="memory-route-select"
                data-memory-id={row.id}
                onClick={() => onOpenItem(row.id)}
                style={rowButtonStyle}
              >
                <div style={{ fontSize: 14, fontWeight: 600 }}>{row.title}</div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  {row.kind} · {row.scope}
                  {row.tags.length > 0 ? ` · #${row.tags.join(" #")}` : ""}
                </div>
              </button>
              <button
                type="button"
                data-testid="memory-route-delete"
                data-memory-id={row.id}
                onClick={() => {
                  void handleDelete(row.id);
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const paneStyle = {
  height: "100%",
  width: "100%",
  display: "flex",
  flexDirection: "column",
  padding: 16,
  boxSizing: "border-box",
  background: "var(--color-bg)",
  color: "var(--color-text)",
} as const;

const headerStyle = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginBottom: 12,
} as const;

const searchInputStyle = {
  flex: 1,
  height: 32,
  padding: "0 10px",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  background: "var(--color-surface)",
  color: "inherit",
} as const;

const proposalsButtonStyle = {
  height: 32,
  padding: "0 14px",
  borderRadius: 8,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
} as const;

const rowStyle = {
  padding: "10px 0",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 12,
  alignItems: "center",
} as const;

const rowButtonStyle = {
  flex: 1,
  minWidth: 0,
  textAlign: "left",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: 0,
  color: "inherit",
} as const;

const pendingErrorStyle = {
  marginBottom: 12,
  padding: 12,
  border: "1px solid var(--color-border-strong)",
  borderRadius: 8,
  background: "var(--color-surface)",
  fontSize: 13,
} as const;

const errorShellStyle = {
  height: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
  background: "var(--color-bg)",
  color: "var(--color-text)",
} as const;

const errorCardStyle = {
  border: "1px solid var(--color-border)",
  borderRadius: 12,
  background: "var(--color-surface)",
  padding: 32,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 12,
  maxWidth: 480,
} as const;

const retryButtonStyle = {
  height: 32,
  padding: "0 14px",
  borderRadius: 8,
  border: "1px solid var(--color-border-strong)",
  background: "transparent",
  color: "var(--color-accent)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
} as const;
