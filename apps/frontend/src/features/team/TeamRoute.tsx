// TeamRoute — data binder for the Phase 12 Team destination (sub-PRD
// `team-memory-cmdk-prd.md` §4.1 / §7.1).
//
// Mirrors the P10-C ToolsRoute / P11-C ConnectorsRoute pattern:
//   1. Fetches `GET /v1/team` via `teamApi` and owns loading / error /
//      ready states.
//   2. Opens the `/v1/team/stream` SSE channel with exponential-backoff
//      reconnect, tracking the highest `sequence_no` for
//      `?after_sequence=N` resume (cross-audit §5.2).
//   3. Merges `team.presence_changed` / `team.role_changed` /
//      `team.invited` / `team.joined` / `team.offboarded` envelopes
//      into the local list via the pure `applyTeamEnvelope` adapter.
//   4. Filter axes match sub-PRD §4.1 (role, presence, q).

import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import type {
  Person,
  Presence,
  TeamListResponse,
  TeamRole,
  TeamStreamEnvelope,
  UserId,
} from "@enterprise-search/api-types";

import type { RequestIdentity } from "../../api/config";
import {
  fetchTeam,
  streamTeamEvents,
  type TeamEventsStream,
} from "../../api/teamApi";
import { errorMessage } from "../../utils/errors";
import { applyTeamEnvelope, personToListRow } from "./adapters";

const RECONNECT_BACKOFF_MIN_MS = 1_000;
const RECONNECT_BACKOFF_MAX_MS = 30_000;

interface TeamRouteProps {
  readonly identity: RequestIdentity;
  readonly onOpenPerson: (id: UserId) => void;
}

type ViewState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | {
      readonly kind: "ready";
      readonly items: ReadonlyArray<Person>;
      readonly highestSequenceNo: number;
    };

interface FilterState {
  readonly role: TeamRole | "all";
  readonly presence: Presence | "all";
  readonly search: string;
}

const INITIAL_FILTERS: FilterState = {
  role: "all",
  presence: "all",
  search: "",
};

export function TeamRoute({
  identity,
  onOpenPerson,
}: TeamRouteProps): ReactElement {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS);

  // ---- Initial fetch -------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchTeam(identity, { limit: 50 })
      .then((list: TeamListResponse) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          items: list.people,
          highestSequenceNo: 0,
        });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          message: errorMessage(error, "Could not load team."),
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
    let activeHandle: TeamEventsStream | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    backoffRef.current = RECONNECT_BACKOFF_MIN_MS;

    function open(): void {
      if (cancelled) return;
      let afterSequence = 0;
      setState((prev) => {
        if (prev.kind === "ready") afterSequence = prev.highestSequenceNo;
        return prev;
      });

      activeHandle = streamTeamEvents({
        identity,
        afterSequence: afterSequence > 0 ? afterSequence : undefined,
        onOpen: () => {
          backoffRef.current = RECONNECT_BACKOFF_MIN_MS;
        },
        onEvent: (envelope: TeamStreamEnvelope) => {
          if (cancelled) return;
          setState((prev) => {
            if (prev.kind !== "ready") return prev;
            const items = applyTeamEnvelope(prev.items, envelope);
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
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
      }
      activeHandle?.close();
    };
  }, [identity, state.kind]);

  // ---- Filter pipeline (pure local view shaping) ---------------------
  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const needle = filters.search.trim().toLowerCase();
    return state.items.filter((p) => {
      if (filters.role !== "all" && p.role !== filters.role) return false;
      if (filters.presence !== "all" && p.presence !== filters.presence) {
        return false;
      }
      if (needle.length > 0) {
        const haystack = `${p.display_name} ${p.email}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [state, filters]);

  // ---- Render --------------------------------------------------------
  if (state.kind === "error") {
    return (
      <section
        aria-label="Team destination"
        data-testid="team-route"
        data-state="error"
        style={errorShellStyle}
      >
        <div role="alert" data-testid="team-route-error" style={errorCardStyle}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            Could not load team
          </div>
          <div
            style={{ fontSize: 13, color: "var(--color-text-muted)" }}
            data-testid="team-route-error-message"
          >
            {state.message}
          </div>
          <button
            type="button"
            data-testid="team-route-retry"
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
  const rows = filtered.map(personToListRow);

  return (
    <section
      aria-label="Team destination"
      data-testid="team-route"
      data-state={state.kind}
      data-item-count={items.length}
      style={paneStyle}
    >
      <header style={headerStyle}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Team</h2>
        <input
          type="search"
          data-testid="team-route-search"
          aria-label="Search team"
          placeholder="Search…"
          value={filters.search}
          onChange={(e) =>
            setFilters((prev) => ({ ...prev, search: e.target.value }))
          }
          style={searchInputStyle}
        />
        <select
          data-testid="team-route-role-filter"
          aria-label="Filter by role"
          value={filters.role}
          onChange={(e) =>
            setFilters((prev) => ({
              ...prev,
              role: e.target.value as FilterState["role"],
            }))
          }
        >
          <option value="all">All roles</option>
          <option value="owner">Owner</option>
          <option value="admin">Admin</option>
          <option value="member">Member</option>
          <option value="guest">Guest</option>
        </select>
      </header>
      {state.kind === "loading" ? (
        <div data-testid="team-route-loading" style={{ padding: 16 }}>
          Loading team…
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="team-route-empty"
          style={{ padding: 16, color: "var(--color-text-muted)" }}
        >
          {items.length === 0
            ? "No teammates yet."
            : "No teammates match your filters."}
        </div>
      ) : (
        <ul
          data-testid="team-route-list"
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {rows.map((row) => (
            <li
              key={row.id}
              data-testid="team-route-row"
              data-user-id={row.id}
              data-role={row.role}
              data-presence={row.presence}
              style={rowStyle}
            >
              <button
                type="button"
                data-testid="team-route-select"
                data-user-id={row.id}
                onClick={() => onOpenPerson(row.id)}
                style={rowButtonStyle}
              >
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {row.name}
                  {row.is_self ? " (you)" : ""}
                </div>
                <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                  {row.email} · {row.role} · {row.presence}
                </div>
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

const rowStyle = {
  padding: "10px 0",
  borderBottom: "1px solid var(--color-border)",
} as const;

const rowButtonStyle = {
  width: "100%",
  textAlign: "left",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: 0,
  color: "inherit",
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
