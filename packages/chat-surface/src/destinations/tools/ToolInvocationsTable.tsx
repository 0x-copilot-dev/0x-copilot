// <ToolInvocationsTable /> — paginated invocation history for a single Tool.
//
// Source:
//   - docs/atlas-new-design/destinations/tools-prd.md §3.1 `ToolInvocation`.
//   - docs/atlas-new-design/destinations/tools-prd.md §7.2 — "paginated
//     ActivityList over ToolInvocation[]; rows are ItemLink-wrapped to
//     the run page".
//
// Invariants:
//   - Pure presentation. The host owns transport + cursor pagination;
//     the table just renders the page handed to it and emits an
//     `onLoadMore` callback when the user clicks "Load more".
//   - SP-1: rows go through `<ActivityList>` (one canonical list shell)
//     when the row shape matches; for the table's column-rich layout
//     we render rows directly but every callsite link still uses
//     `<ItemLink>` per cross-audit §1.1/§3.3.
//   - Filters render as chips and combine with AND across axes / OR
//     within an axis (master §1.5).
//   - The empty state distinguishes "no invocations at all" from "no
//     invocations match the filter".

import { useCallback, useMemo, useState } from "react";
import type { CSSProperties, ReactElement } from "react";

import type {
  ToolInvocation,
  ToolInvocationCallerKind,
} from "@0x-copilot/api-types";

import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

// ===========================================================================
// Public props.
// ===========================================================================

export type ToolInvocationStatusFilter = "all" | "ok" | "error";

export interface ToolInvocationsTableProps {
  readonly invocations: ReadonlyArray<ToolInvocation>;
  /**
   * When non-null, a "Load more" button is shown; clicking it calls
   * `onLoadMore` with the cursor. The host owns the next fetch.
   */
  readonly nextCursor?: string | null;
  readonly onLoadMore?: (cursor: string) => void;
  /**
   * Frozen `now` for tests; defaults to `Date.now()`. Threaded into
   * `formatRelativeTime` so the timestamp column is deterministic.
   */
  readonly now?: number;
  /**
   * Optional initial filters — host may persist filter state across
   * refetches and rehydrate here. When omitted, the table starts with
   * "all" on every axis.
   */
  readonly initialCallerKinds?: ReadonlyArray<ToolInvocationCallerKind>;
  readonly initialStatus?: ToolInvocationStatusFilter;
}

// ===========================================================================
// Component.
// ===========================================================================

const ALL_CALLER_KINDS: ReadonlyArray<ToolInvocationCallerKind> = [
  "agent",
  "routine",
  "chat",
];

export function ToolInvocationsTable(
  props: ToolInvocationsTableProps,
): ReactElement {
  const {
    invocations,
    nextCursor,
    onLoadMore,
    now,
    initialCallerKinds,
    initialStatus = "all",
  } = props;

  // Filter state — chip selection. Empty caller-kind set ⇒ "all kinds".
  const [callerKinds, setCallerKinds] = useState<
    ReadonlySet<ToolInvocationCallerKind>
  >(() => new Set(initialCallerKinds ?? []));
  const [status, setStatus] =
    useState<ToolInvocationStatusFilter>(initialStatus);

  const toggleCallerKind = useCallback((kind: ToolInvocationCallerKind) => {
    setCallerKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const filtered = useMemo(() => {
    return invocations.filter((inv) => {
      if (callerKinds.size > 0 && !callerKinds.has(inv.caller_kind))
        return false;
      if (status !== "all" && inv.status !== status) return false;
      return true;
    });
  }, [invocations, callerKinds, status]);

  const handleLoadMore = useCallback(() => {
    if (nextCursor !== null && nextCursor !== undefined && onLoadMore) {
      onLoadMore(nextCursor);
    }
  }, [nextCursor, onLoadMore]);

  return (
    <section
      data-testid="tool-invocations-table"
      aria-label="Tool invocations"
      style={containerStyle}
    >
      {/* Filter strip ----------------------------------------------------- */}
      <div
        style={filterStripStyle}
        data-testid="tool-invocations-filters"
        role="group"
        aria-label="Invocation filters"
      >
        <span style={filterLabelStyle}>Caller</span>
        {ALL_CALLER_KINDS.map((kind) => {
          const on = callerKinds.has(kind);
          return (
            <button
              key={kind}
              type="button"
              data-testid={`tool-invocations-filter-caller-${kind}`}
              data-active={on ? "true" : "false"}
              aria-pressed={on}
              onClick={() => toggleCallerKind(kind)}
              style={chipStyle(on)}
            >
              {kind}
            </button>
          );
        })}
        <span style={{ ...filterLabelStyle, marginLeft: 12 }}>Status</span>
        {(["all", "ok", "error"] as const).map((s) => {
          const on = status === s;
          return (
            <button
              key={s}
              type="button"
              data-testid={`tool-invocations-filter-status-${s}`}
              data-active={on ? "true" : "false"}
              aria-pressed={on}
              onClick={() => setStatus(s)}
              style={chipStyle(on)}
            >
              {s}
            </button>
          );
        })}
      </div>

      {/* Body -------------------------------------------------------------- */}
      {invocations.length === 0 ? (
        <p
          data-testid="tool-invocations-empty"
          role="status"
          style={emptyStyle}
        >
          No invocations yet.
        </p>
      ) : filtered.length === 0 ? (
        <p
          data-testid="tool-invocations-empty-filtered"
          role="status"
          style={emptyStyle}
        >
          No invocations match the current filters.
        </p>
      ) : (
        <ul
          style={listStyle}
          data-testid="tool-invocations-list"
          aria-label="Invocation rows"
        >
          {filtered.map((inv) => (
            <li
              key={inv.id}
              style={rowStyle}
              data-testid="tool-invocations-row"
              data-caller-kind={inv.caller_kind}
              data-status={inv.status}
            >
              <time
                dateTime={inv.started_at}
                data-testid="tool-invocations-row-time"
                style={timeStyle}
              >
                {formatRelativeTime(inv.started_at, now)}
              </time>
              <span style={callerCellStyle}>
                <ItemLink ref={inv.caller_ref} />
              </span>
              <span
                style={argsStyle}
                data-testid="tool-invocations-row-args"
                title={inv.args_summary}
              >
                {inv.args_summary}
              </span>
              <span
                data-testid="tool-invocations-row-status"
                data-status={inv.status}
                style={statusChipStyle(inv.status)}
              >
                {inv.status}
                {inv.error_kind !== undefined ? `: ${inv.error_kind}` : ""}
              </span>
              <span
                style={latencyStyle}
                data-testid="tool-invocations-row-latency"
              >
                {inv.latency_ms}ms
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Load-more --------------------------------------------------------- */}
      {nextCursor !== null && nextCursor !== undefined ? (
        <button
          type="button"
          data-testid="tool-invocations-load-more"
          onClick={handleLoadMore}
          style={loadMoreStyle}
        >
          Load more
        </button>
      ) : null}
    </section>
  );
}

// ===========================================================================
// Styles.
// ===========================================================================

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 12,
  background: "var(--color-bg)",
  color: "var(--color-text)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  boxSizing: "border-box",
};

const filterStripStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  alignItems: "center",
  padding: "4px 0",
};

const filterLabelStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  textTransform: "uppercase",
  letterSpacing: 0.4,
  marginRight: 4,
};

const chipStyle = (active: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  height: 22,
  padding: "0 8px",
  borderRadius: 999,
  border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
  background: active ? "var(--color-bg-accent-subtle)" : "transparent",
  color: active ? "var(--color-text)" : "var(--color-text-muted)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  letterSpacing: 0.3,
  textTransform: "lowercase",
  cursor: "pointer",
});

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

const rowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "80px 1fr 1fr 110px 70px",
  alignItems: "center",
  gap: 12,
  padding: "8px 10px",
  borderRadius: 6,
  background: "var(--color-bg-elevated)",
};

const timeStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
  fontVariantNumeric: "tabular-nums",
};

const callerCellStyle: CSSProperties = {
  display: "inline-flex",
  minWidth: 0,
};

const argsStyle: CSSProperties = {
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const statusChipStyle = (status: "ok" | "error"): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  height: 18,
  padding: "0 8px",
  borderRadius: 999,
  fontSize: "var(--font-size-2xs)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.3,
  color:
    status === "ok"
      ? "var(--color-success, #6ec48c)"
      : "var(--color-danger, #d97777)",
  background:
    status === "ok"
      ? "var(--color-success-bg, #1a2f23)"
      : "var(--color-danger-bg, #321a1a)",
  border: `1px solid ${
    status === "ok"
      ? "var(--color-success, #6ec48c)"
      : "var(--color-danger, #d97777)"
  }`,
  whiteSpace: "nowrap",
});

const latencyStyle: CSSProperties = {
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  fontVariantNumeric: "tabular-nums",
  textAlign: "right",
};

const emptyStyle: CSSProperties = {
  margin: "8px 0",
  padding: 16,
  fontSize: "var(--font-size-sm)",
  color: "var(--color-text-muted)",
  fontStyle: "italic",
  background: "var(--color-bg-elevated)",
  borderRadius: 6,
  textAlign: "center",
};

const loadMoreStyle: CSSProperties = {
  alignSelf: "center",
  marginTop: 4,
  background: "transparent",
  color: "var(--color-accent)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "6px 14px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};
