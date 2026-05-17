// Routines — destination shell (P5-B1).
//
// Pure-presentation list view per routines-prd §3.2 + §8:
//
//   1. PageHeader (cross-audit §1.6 shape) — title, subtitle, active/
//      errored count chips, "New routine" primary action.
//   2. FilterTabs — status axis: All / Active / Paused / Errored / Draft
//      (routines-prd §3.2 #2). Selected slug + counts driven by host.
//   3. List body — `<DocList>` slot mode, one row per routine. Status
//      pill + name + "next fire at" + trigger-kind chips + owner /
//      project / output-target chips + cross-destination `<ItemLink>`s.
//
// Mirrors P4-B1's Inbox shell shape (loading skeleton -> SectionResult
// error/unavailable branches -> ready). Same render-prop seam for the
// detail pane (P5-B3 layers detail; P5-B2 layers editor — both ship
// later as separate files).
//
// Hard correctness rules:
//   - SP-1 primitives only (PageHeader / FilterTabs / StatusPill /
//     DocList / EmptyState / ItemLink). No custom buttons.
//   - ItemLink for every cross-destination ref (agent, project,
//     output-target). Direct router.navigate from rows is forbidden
//     (cross-audit §1.1 + §3.3).
//   - Pure presentation: no fetch, no router calls, no SSE — the host
//     (apps/frontend P5-C) wires those.
//
// `_routines-stub.ts` carries wire-types until P5-A1's api-types land.
// Every import is marked `TODO(merge): rewire to "@enterprise-search/api-types"`.

import {
  useMemo,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { RoutineId, SectionResult } from "@enterprise-search/api-types";

import { DocList } from "../../shell/DocList";
import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { PageHeader } from "../../shell/PageHeader";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { ItemLink } from "../../refs/ItemLink";
import { formatRelativeTime } from "../../util/time";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type {
  Routine,
  RoutineStatus,
  RoutineTrigger,
  RoutineTriggerKind,
} from "./_routines-stub";

// ===========================================================================
// Filter slug
// ===========================================================================
//
// Single source of truth for the status filter axis. The slugs match
// routines-prd §3.2 #2 exactly so the same vocabulary feeds the panel
// (RoutinesPanel.tsx) and the URL `?filter[status]=…` axis. "all" is
// the default (no server filter).

export type RoutinesFilterSlug =
  | "all"
  | "active"
  | "paused"
  | "errored"
  | "draft";

const FILTER_ORDER: ReadonlyArray<RoutinesFilterSlug> = [
  "all",
  "active",
  "paused",
  "errored",
  "draft",
];

const FILTER_LABEL: Readonly<Record<RoutinesFilterSlug, string>> = {
  all: "All",
  active: "Active",
  paused: "Paused",
  errored: "Errored",
  draft: "Draft",
};

/** Per-filter counts driven by the host (same query result feeds list +
 *  filter chips so they don't drift). */
export type RoutinesFilterCounts = Readonly<Record<RoutinesFilterSlug, number>>;

// ===========================================================================
// Public props
// ===========================================================================

/** Slot for P5-B3's detail / P5-B2's editor pane. Rendered in place of
 *  the list body when `focusedRoutineId` is set. */
export type RenderRoutineDetailSlot = (props: {
  readonly routineId: RoutineId;
  readonly onClose: () => void;
}) => ReactNode;

export interface RoutinesDestinationProps {
  /**
   * Server-projected list result. `null` = loading skeleton; `error`
   * shows the destination-level error empty-state with retry; `ok`
   * renders the filtered list.
   *
   * `items` is wrapped in `SectionResult` even though `/v1/routines` is
   * a non-aggregating endpoint (cross-audit §2.3 only mandates the
   * wrapper for aggregators) — same rationale as inbox-shell: a uniform
   * "couldn't load" branch without inventing a second error path.
   */
  readonly items?: SectionResult<ReadonlyArray<Routine>> | null;

  /** Active status-filter slug. Defaults to "all". */
  readonly filter?: RoutinesFilterSlug;
  readonly onFilterChange?: (next: RoutinesFilterSlug) => void;

  /** Per-filter counts. When omitted, chips render without count chips. */
  readonly counts?: RoutinesFilterCounts;

  /** "New routine" CTA — pivots the host into the editor (P5-B2). */
  readonly onCreateRoutine?: () => void;

  /** Per-row hover actions — wired by the host (P5-C) so the shell stays
   *  pure presentation. */
  readonly onRunNow?: (id: RoutineId) => void;
  readonly onPauseRoutine?: (id: RoutineId) => void;
  readonly onActivateRoutine?: (id: RoutineId) => void;
  readonly onEditRoutine?: (id: RoutineId) => void;

  /** Retry callback when `items.status === "error"`. */
  readonly onRetry?: () => void;

  /** P5-B3 detail slot. When supplied AND `focusedRoutineId` is set,
   *  the slot replaces the list body. */
  readonly renderDetail?: RenderRoutineDetailSlot;
  readonly focusedRoutineId?: RoutineId | null;
  readonly onCloseDetail?: () => void;

  /** Reference instant — test seam for relative-time formatting. */
  readonly now?: number;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function RoutinesDestination(
  props: RoutinesDestinationProps = {},
): ReactElement {
  const {
    items = null,
    filter = "all",
    onFilterChange,
    counts,
    onCreateRoutine,
    onRunNow,
    onPauseRoutine,
    onActivateRoutine,
    onEditRoutine,
    onRetry,
    renderDetail,
    focusedRoutineId = null,
    onCloseDetail,
    now,
  } = props;

  // === Filter chip options (single source of truth) =====================
  const filterOptions = useMemo<
    ReadonlyArray<FilterTabOption<RoutinesFilterSlug>>
  >(
    () =>
      FILTER_ORDER.map((slug) => ({
        slug,
        label: FILTER_LABEL[slug],
        count: counts?.[slug],
      })),
    [counts],
  );

  const handleFilterChange = (next: RoutinesFilterSlug): void => {
    if (onFilterChange !== undefined) onFilterChange(next);
  };

  // === Styles ===========================================================
  const rootStyle: CSSProperties = {
    width: "100%",
    height: "100%",
    minHeight: 0,
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    overflow: "auto",
  };
  const containerStyle: CSSProperties = {
    width: "100%",
    maxWidth: 920,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  };

  // === Loading state ====================================================
  if (items === null) {
    return (
      <section
        aria-label="Routines destination"
        data-testid="routines-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Routines" subtitle="Loading…" />
          <div
            data-testid="routines-skeleton"
            aria-hidden="true"
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            {Array.from({ length: 3 }).map((_, i) => (
              <RowSkeleton key={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  // === Error state ======================================================
  if (items.status === "error") {
    return (
      <section
        aria-label="Routines destination"
        data-testid="routines-destination"
        data-state="error"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Routines" />
          <EmptyState
            title="Could not load routines"
            body={items.error ?? "Network error — try again."}
            action={
              onRetry !== undefined
                ? { label: "Retry", onClick: onRetry }
                : undefined
            }
          />
        </div>
      </section>
    );
  }

  if (items.status === "unavailable") {
    return (
      <section
        aria-label="Routines destination"
        data-testid="routines-destination"
        data-state="unavailable"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Routines" />
          <EmptyState
            title="Routines unavailable"
            body={
              items.error ??
              "This destination is not enabled for your workspace."
            }
          />
        </div>
      </section>
    );
  }

  // === Ready state ======================================================
  const rows = items.data ?? [];

  // Counts for the PageHeader subtitle / badges.
  const activeCount =
    counts?.active ?? rows.filter((r) => r.status === "active").length;
  const erroredCount =
    counts?.errored ?? rows.filter((r) => r.status === "errored").length;

  const subtitle =
    rows.length === 0
      ? "Scheduled and triggered runs"
      : `${activeCount} active${erroredCount > 0 ? ` · ${erroredCount} errored` : ""}`;

  const badges =
    erroredCount > 0 ? (
      <StatusPill status="error" label={`${erroredCount} errored`} />
    ) : activeCount > 0 ? (
      <StatusPill status="ok" label={`${activeCount} active`} />
    ) : undefined;

  const showingDetail = renderDetail !== undefined && focusedRoutineId !== null;

  return (
    <section
      aria-label="Routines destination"
      data-testid="routines-destination"
      data-state="ready"
      data-focused-routine-id={focusedRoutineId ?? undefined}
      data-filter={filter}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader
          title="Routines"
          subtitle={subtitle}
          badges={badges}
          primaryAction={
            onCreateRoutine !== undefined
              ? { label: "New routine", onClick: onCreateRoutine }
              : undefined
          }
        />

        <FilterTabs<RoutinesFilterSlug>
          value={filter}
          onChange={handleFilterChange}
          options={filterOptions}
          ariaLabel="Routines status filter"
          idPrefix="routines"
        />

        {showingDetail ? (
          <div
            data-testid="routines-detail-slot"
            data-focused-routine-id={focusedRoutineId!}
          >
            {renderDetail!({
              routineId: focusedRoutineId!,
              onClose: () => {
                if (onCloseDetail !== undefined) onCloseDetail();
              },
            })}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No routines yet"
            body="Routines run on a schedule, via webhook, on an event, or when you press Run now."
            action={
              onCreateRoutine !== undefined
                ? { label: "New routine", onClick: onCreateRoutine }
                : undefined
            }
          />
        ) : (
          <DocList<Routine>
            ariaLabel="Routines"
            items={rows}
            keyFor={(r) => r.id}
            renderRow={(routine) => (
              <RoutineRow
                routine={routine}
                onRunNow={onRunNow}
                onPauseRoutine={onPauseRoutine}
                onActivateRoutine={onActivateRoutine}
                onEditRoutine={onEditRoutine}
                now={now ?? Date.now()}
              />
            )}
          />
        )}
      </div>
    </section>
  );
}

// ===========================================================================
// RoutineRow — one item row
// ===========================================================================

interface RoutineRowProps {
  readonly routine: Routine;
  readonly onRunNow?: (id: RoutineId) => void;
  readonly onPauseRoutine?: (id: RoutineId) => void;
  readonly onActivateRoutine?: (id: RoutineId) => void;
  readonly onEditRoutine?: (id: RoutineId) => void;
  readonly now: number;
}

function RoutineRow({
  routine,
  onRunNow,
  onPauseRoutine,
  onActivateRoutine,
  onEditRoutine,
  now,
}: RoutineRowProps): ReactElement {
  const wrapStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    flex: 1,
    minWidth: 0,
  };
  const headStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    flex: 1,
    minWidth: 0,
  };
  const nameStyle: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: 600,
    color: "var(--color-text, #ededee)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const descriptionStyle: CSSProperties = {
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const metaStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
    fontSize: "var(--font-size-xs, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
  };
  const actionButtonStyle: CSSProperties = {
    background: "transparent",
    border: "none",
    color: "var(--color-text-subtle, #7e7e84)",
    cursor: "pointer",
    fontSize: "var(--font-size-xs, 12px)",
    padding: "2px 6px",
  };

  const triggerKinds = uniqueTriggerKinds(routine.triggers);
  const nextFireLabel = nextFireDisplay(routine, now);
  const isPaused = routine.status === "paused";
  const isActive = routine.status === "active";

  return (
    <div
      style={wrapStyle}
      data-testid="routine-row"
      data-routine-id={routine.id}
      data-status={routine.status}
    >
      <div style={headStyle}>
        <StatusPill
          status={statusTone(routine.status)}
          label={statusLabel(routine.status)}
        />
        <span style={nameStyle} data-testid="routine-row-name">
          {routine.name}
        </span>
        <span data-testid="routine-row-next-fire">{nextFireLabel}</span>
        {onRunNow !== undefined ? (
          <button
            type="button"
            data-testid="routine-row-run-now"
            onClick={() => onRunNow(routine.id)}
            style={actionButtonStyle}
            aria-label={`Run ${routine.name} now`}
          >
            Run now
          </button>
        ) : null}
        {isActive && onPauseRoutine !== undefined ? (
          <button
            type="button"
            data-testid="routine-row-pause"
            onClick={() => onPauseRoutine(routine.id)}
            style={actionButtonStyle}
            aria-label={`Pause ${routine.name}`}
          >
            Pause
          </button>
        ) : null}
        {isPaused && onActivateRoutine !== undefined ? (
          <button
            type="button"
            data-testid="routine-row-activate"
            onClick={() => onActivateRoutine(routine.id)}
            style={actionButtonStyle}
            aria-label={`Activate ${routine.name}`}
          >
            Activate
          </button>
        ) : null}
        {onEditRoutine !== undefined ? (
          <button
            type="button"
            data-testid="routine-row-edit"
            onClick={() => onEditRoutine(routine.id)}
            style={actionButtonStyle}
            aria-label={`Edit ${routine.name}`}
          >
            Edit
          </button>
        ) : null}
      </div>

      {routine.description.length > 0 ? (
        <div style={descriptionStyle} data-testid="routine-row-description">
          {routine.description}
        </div>
      ) : null}

      <div style={metaStyle} data-testid="routine-row-meta">
        {/* Trigger-kind chips — visible at a glance per §3.2. */}
        {triggerKinds.map((kind) => (
          <StatusPill
            key={kind}
            status={triggerTone(kind)}
            label={triggerLabel(kind)}
          />
        ))}
        {/* Owner / project / model chips — denormalized on the row. */}
        {routine.owner_display_name !== undefined ? (
          <span data-testid="routine-row-owner">
            {routine.owner_display_name}
          </span>
        ) : null}
        {routine.project_name !== undefined ? (
          <StatusPill status="muted" label={routine.project_name} />
        ) : null}
        <StatusPill status="muted" label={routine.model} />
        {/* Cross-destination ItemLink chips — ALWAYS via `<ItemLink>` per
            cross-audit §1.1. No router.navigate from rows. */}
        {routine.links.map((ref, idx) => (
          <ItemLink key={`${ref.kind}-${idx}`} ref={ref} />
        ))}
        {routine.last_fire_at !== null ? (
          <span data-testid="routine-row-last-fire">
            last fire {formatRelativeTime(routine.last_fire_at, now)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

const STATUS_TONE: Readonly<Record<RoutineStatus, StatusTone>> = {
  draft: "muted",
  active: "ok",
  paused: "info",
  errored: "error",
};

const STATUS_LABEL: Readonly<Record<RoutineStatus, string>> = {
  draft: "Draft",
  active: "Active",
  paused: "Paused",
  errored: "Errored",
};

function statusTone(status: RoutineStatus): StatusTone {
  return STATUS_TONE[status];
}

function statusLabel(status: RoutineStatus): string {
  return STATUS_LABEL[status];
}

const TRIGGER_LABEL: Readonly<Record<RoutineTriggerKind, string>> = {
  schedule: "Schedule",
  webhook: "Webhook",
  event: "Event",
  manual: "Manual",
};

const TRIGGER_TONE: Readonly<Record<RoutineTriggerKind, StatusTone>> = {
  schedule: "info",
  webhook: "warning",
  event: "ok",
  manual: "muted",
};

function triggerLabel(kind: RoutineTriggerKind): string {
  return TRIGGER_LABEL[kind];
}

function triggerTone(kind: RoutineTriggerKind): StatusTone {
  return TRIGGER_TONE[kind];
}

/** Dedupe trigger kinds in stable §3.6 order (schedule -> webhook ->
 *  event -> manual). A routine with multiple cron triggers shows a
 *  single "Schedule" chip. */
export function uniqueTriggerKinds(
  triggers: ReadonlyArray<RoutineTrigger>,
): ReadonlyArray<RoutineTriggerKind> {
  const seen = new Set<RoutineTriggerKind>();
  for (const t of triggers) seen.add(t.kind);
  const order: ReadonlyArray<RoutineTriggerKind> = [
    "schedule",
    "webhook",
    "event",
    "manual",
  ];
  return order.filter((k) => seen.has(k));
}

/**
 * Per §3.2: "Next fire at" shows `formatRelativeTime(next_fire_at, now)`.
 * When the routine has no schedule trigger, shows the next-event-source
 * label ("Webhook · waiting" / "Event · waiting" / "Manual only").
 */
export function nextFireDisplay(routine: Routine, now: number): string {
  if (routine.next_fire_at !== null) {
    return `next ${formatRelativeTime(routine.next_fire_at, now)}`;
  }
  const kinds = uniqueTriggerKinds(routine.triggers);
  if (kinds.includes("webhook")) return "Webhook · waiting";
  if (kinds.includes("event")) return "Event · waiting";
  return "Manual only";
}

// ===========================================================================
// RowSkeleton — loading placeholder
// ===========================================================================

function RowSkeleton(): ReactElement {
  const style: CSSProperties = {
    height: 56,
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div style={style} data-testid="routines-skeleton-row" aria-hidden="true" />
  );
}
