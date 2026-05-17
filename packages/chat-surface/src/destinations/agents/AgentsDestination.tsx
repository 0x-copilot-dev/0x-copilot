import type { AgentRunStatus } from "@enterprise-search/api-types";
import {
  Button,
  Select,
  StatusPill,
  TextInput,
  type StatusTone,
} from "@enterprise-search/design-system";
import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { useRouter } from "../../providers/RouterProvider";
import { useTransport } from "../../providers/TransportProvider";
import type { ArtifactRoute } from "../../routing/router";

const BACKGROUND = "#11141B";
const SURFACE = "#16181F";
const BORDER = "#22252E";
const TEXT_PRIMARY = "#E4E5E9";
const TEXT_SECONDARY = "#7E8492";
const HEADER_BG = "#191C24";
const HEADER_TEXT = "#9CA1AE";
const ROW_HOVER = "#1B1F28";
const ACTIVE_HIGHLIGHT = "#7B9BFF";

export interface AgentRunRow {
  readonly run_id: string;
  readonly agent_name: string;
  readonly status: AgentRunStatus;
  readonly model: string;
  readonly tokens: number;
  readonly latency_ms: number;
  readonly started_at: string;
}

interface AgentRunsResponse {
  readonly runs: readonly AgentRunRow[];
}

type StatusFilter = "all" | "running" | "completed" | "failed";

type SortKey =
  | "started_at"
  | "agent_name"
  | "status"
  | "model"
  | "tokens"
  | "latency_ms";

type SortDirection = "asc" | "desc";

type FetchState =
  | { readonly kind: "loading" }
  | { readonly kind: "error"; readonly message: string }
  | { readonly kind: "ready"; readonly runs: readonly AgentRunRow[] };

interface ColumnSpec {
  readonly key: SortKey;
  readonly label: string;
  readonly width: string;
  readonly align: "left" | "right";
}

const COLUMNS: readonly ColumnSpec[] = [
  { key: "started_at", label: "Time", width: "160px", align: "left" },
  {
    key: "agent_name",
    label: "Agent",
    width: "minmax(160px, 1fr)",
    align: "left",
  },
  { key: "status", label: "Status", width: "140px", align: "left" },
  { key: "model", label: "Model", width: "160px", align: "left" },
  { key: "tokens", label: "Tokens", width: "100px", align: "right" },
  { key: "latency_ms", label: "Latency", width: "100px", align: "right" },
];

function statusTone(status: AgentRunStatus): StatusTone {
  if (status === "running" || status === "queued" || status === "cancelling")
    return "running";
  if (status === "completed") return "ready";
  return "idle";
}

function matchesStatusFilter(
  filter: StatusFilter,
  status: AgentRunStatus,
): boolean {
  if (filter === "all") return true;
  if (filter === "running")
    return (
      status === "running" || status === "queued" || status === "cancelling"
    );
  if (filter === "completed") return status === "completed";
  return (
    status === "failed" || status === "timed_out" || status === "cancelled"
  );
}

function compareRows(
  a: AgentRunRow,
  b: AgentRunRow,
  key: SortKey,
  direction: SortDirection,
): number {
  const flip = direction === "asc" ? 1 : -1;
  if (key === "tokens" || key === "latency_ms") {
    return (a[key] - b[key]) * flip;
  }
  const av = a[key];
  const bv = b[key];
  if (av < bv) return -1 * flip;
  if (av > bv) return 1 * flip;
  return 0;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatLatency(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${ms}ms`;
}

function formatTimestamp(value: string): string {
  return value
    .replace("T", " ")
    .replace(/\.\d+Z?$/, "")
    .slice(0, 16);
}

export function AgentsDestination(): ReactElement {
  const transport = useTransport();
  const router = useRouter<ArtifactRoute>();

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [agentNameFilter, setAgentNameFilter] = useState<string>("");
  const [sortKey, setSortKey] = useState<SortKey>("started_at");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [fetchTick, setFetchTick] = useState(0);
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    const query: Record<string, string> = {};
    if (statusFilter !== "all") query.status = statusFilter;
    const trimmed = agentNameFilter.trim();
    if (trimmed !== "") query.agent_name = trimmed;
    transport
      .request<AgentRunsResponse>({
        method: "GET",
        path: "/v1/agent/runs",
        query,
      })
      .then((res) => {
        if (cancelled) return;
        setState({ kind: "ready", runs: res.runs });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof Error ? err.message : "Failed to load agent runs.";
        setState({ kind: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [transport, statusFilter, agentNameFilter, fetchTick]);

  const sorted = useMemo(() => {
    if (state.kind !== "ready") return [];
    const copy = state.runs.slice();
    copy.sort((a, b) => compareRows(a, b, sortKey, sortDirection));
    return copy;
  }, [state, sortKey, sortDirection]);

  const handleSort = (key: SortKey): void => {
    if (key === sortKey) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDirection(key === "started_at" ? "desc" : "asc");
  };

  const handleRowClick = (runId: string): void => {
    router.navigate({ kind: "run", runId });
  };

  const gridTemplate = COLUMNS.map((c) => c.width).join(" ");

  const containerStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    backgroundColor: BACKGROUND,
    color: TEXT_PRIMARY,
    boxSizing: "border-box",
  };
  const filterBarStyle: CSSProperties = {
    position: "sticky",
    top: 0,
    zIndex: 2,
    display: "flex",
    gap: 12,
    padding: "12px 16px",
    backgroundColor: BACKGROUND,
    borderBottom: `1px solid ${BORDER}`,
    alignItems: "center",
  };
  const bodyStyle: CSSProperties = {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
  };
  const tableStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: gridTemplate,
    fontSize: 13,
  };
  const headerStyle = (align: "left" | "right"): CSSProperties => ({
    position: "sticky",
    top: 0,
    backgroundColor: HEADER_BG,
    color: HEADER_TEXT,
    padding: "10px 12px",
    fontWeight: 600,
    textAlign: align,
    borderBottom: `1px solid ${BORDER}`,
    cursor: "pointer",
    userSelect: "none",
    fontFamily: "inherit",
    fontSize: 12,
    border: "none",
    width: "100%",
  });
  const cellStyle = (align: "left" | "right"): CSSProperties => ({
    padding: "10px 12px",
    borderBottom: `1px solid ${BORDER}`,
    textAlign: align,
    color: TEXT_PRIMARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
  const emptyStyle: CSSProperties = {
    padding: 24,
    color: TEXT_SECONDARY,
    fontSize: 13,
  };

  const ariaSort = (key: SortKey): "ascending" | "descending" | "none" =>
    key === sortKey
      ? sortDirection === "asc"
        ? "ascending"
        : "descending"
      : "none";

  return (
    <section
      data-component="agents-destination"
      aria-label="Agents destination"
      style={containerStyle}
    >
      <div style={filterBarStyle} data-testid="agents-filter-bar">
        <Select
          aria-label="Filter by status"
          data-testid="agents-status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
        >
          <option value="all">All statuses</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </Select>
        <TextInput
          aria-label="Filter by agent name"
          data-testid="agents-name-filter"
          placeholder="Search agent name"
          value={agentNameFilter}
          onChange={(e) => setAgentNameFilter(e.target.value)}
        />
      </div>
      <div style={bodyStyle}>
        <div
          role="table"
          aria-label="Agent runs"
          data-testid="agents-table"
          style={tableStyle}
        >
          <div role="row" style={{ display: "contents" }}>
            {COLUMNS.map((col) => (
              <div
                role="columnheader"
                key={col.key}
                aria-sort={ariaSort(col.key)}
              >
                <button
                  type="button"
                  onClick={() => handleSort(col.key)}
                  data-testid={`agents-sort-${col.key}`}
                  style={{
                    ...headerStyle(col.align),
                    color: col.key === sortKey ? ACTIVE_HIGHLIGHT : HEADER_TEXT,
                  }}
                >
                  {col.label}
                  {col.key === sortKey
                    ? sortDirection === "asc"
                      ? " ▲"
                      : " ▼"
                    : ""}
                </button>
              </div>
            ))}
          </div>
          {state.kind === "loading" ? (
            <AgentsSkeleton />
          ) : state.kind === "error" ? (
            <AgentsError
              message={state.message}
              onRetry={() => setFetchTick((n) => n + 1)}
            />
          ) : sorted.filter((r) => matchesStatusFilter(statusFilter, r.status))
              .length === 0 ? (
            <div
              role="row"
              style={{ display: "contents" }}
              data-testid="agents-empty"
            >
              <div
                role="cell"
                style={{
                  ...emptyStyle,
                  gridColumn: `1 / span ${COLUMNS.length}`,
                  textAlign: "left",
                }}
              >
                No agent runs yet.
              </div>
            </div>
          ) : (
            sorted
              .filter((r) => matchesStatusFilter(statusFilter, r.status))
              .map((row) => (
                <div
                  key={row.run_id}
                  role="row"
                  data-testid="agents-row"
                  data-run-id={row.run_id}
                  onClick={() => handleRowClick(row.run_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleRowClick(row.run_id);
                    }
                  }}
                  tabIndex={0}
                  style={{
                    display: "contents",
                    cursor: "pointer",
                  }}
                >
                  <div
                    role="cell"
                    style={{ ...cellStyle("left"), backgroundColor: SURFACE }}
                  >
                    {formatTimestamp(row.started_at)}
                  </div>
                  <div
                    role="cell"
                    style={{ ...cellStyle("left"), backgroundColor: SURFACE }}
                  >
                    {row.agent_name}
                  </div>
                  <div
                    role="cell"
                    style={{ ...cellStyle("left"), backgroundColor: SURFACE }}
                  >
                    <StatusPill
                      tone={statusTone(row.status)}
                      label={row.status}
                    />
                  </div>
                  <div
                    role="cell"
                    style={{
                      ...cellStyle("left"),
                      backgroundColor: SURFACE,
                      color: TEXT_SECONDARY,
                    }}
                  >
                    {row.model}
                  </div>
                  <div
                    role="cell"
                    style={{
                      ...cellStyle("right"),
                      backgroundColor: SURFACE,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {formatTokens(row.tokens)}
                  </div>
                  <div
                    role="cell"
                    style={{
                      ...cellStyle("right"),
                      backgroundColor: SURFACE,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {formatLatency(row.latency_ms)}
                  </div>
                </div>
              ))
          )}
        </div>
      </div>
      <style>{`
        [data-testid='agents-row']:hover > [role='cell'] { background-color: ${ROW_HOVER} !important; }
      `}</style>
    </section>
  );
}

function AgentsSkeleton(): ReactElement {
  const rows = [0, 1, 2, 3, 4];
  return (
    <>
      {rows.map((i) => (
        <div
          key={i}
          role="row"
          data-testid="agents-skeleton-row"
          style={{ display: "contents" }}
        >
          {COLUMNS.map((col) => (
            <div
              key={col.key}
              role="cell"
              style={{
                padding: "10px 12px",
                borderBottom: `1px solid ${BORDER}`,
                backgroundColor: SURFACE,
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: "70%",
                  height: 10,
                  borderRadius: 4,
                  backgroundColor: HEADER_BG,
                }}
                aria-hidden="true"
              />
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

interface AgentsErrorProps {
  readonly message: string;
  readonly onRetry: () => void;
}

function AgentsError({ message, onRetry }: AgentsErrorProps): ReactElement {
  return (
    <div role="row" style={{ display: "contents" }} data-testid="agents-error">
      <div
        role="cell"
        style={{
          padding: 16,
          gridColumn: `1 / span ${COLUMNS.length}`,
          borderBottom: `1px solid ${BORDER}`,
          display: "flex",
          gap: 12,
          alignItems: "center",
          color: TEXT_PRIMARY,
        }}
      >
        <span>{message}</span>
        <Button
          variant="secondary"
          size="sm"
          onClick={onRetry}
          data-testid="agents-retry"
        >
          Retry
        </Button>
      </div>
    </div>
  );
}
