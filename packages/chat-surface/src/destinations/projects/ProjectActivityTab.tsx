// ProjectActivityTab — P6-B2
//
// Renders the project's activity feed. Every row is wrapped in an
// <ItemLink ref={row.ref}> per cross-audit §1.1 (the only way a
// destination renders a cross-destination link). The ItemLink registry
// lives in `packages/chat-surface/src/refs/registry.ts` (Phase 0.6) —
// until that registry is shipped, we accept the registry as a render
// prop (`renderItemLink`) so this tab works in any host that already
// has a working resolver. When omitted, the row falls back to a plain
// label so the view is never broken.
//
// Pure presentation; no transport / no router.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { formatRelativeTime } from "../../util/time";

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const PANEL_BORDER_STRONG = "var(--color-border-strong)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";
const TEXT_FAINT = "var(--color-text-subtle)";

/** Mirror of `ItemRef` from `packages/api-types/src/refs.ts`. Kept as
 *  a local opaque shape until the api-types refs module lands — at
 *  that point this alias points to the canonical type and no consumer
 *  code changes. (cross-audit §1.1) */
export interface ProjectActivityItemRef {
  readonly kind: string;
  readonly id: string;
}

export interface ProjectActivity {
  readonly id: string;
  /** ItemRef pointing at the artifact this activity row references
   *  (a chat, run, todo, member-change, etc.). Renderers wrap the row
   *  in <ItemLink ref={ref}> per cross-audit §1.1. */
  readonly ref: ProjectActivityItemRef;
  /** Human-readable label for the row. */
  readonly label: string;
  /** Optional context line ("Sarah added Marcus as editor"). */
  readonly summary?: string;
  /** ISO timestamp; relative-time formatted at render. */
  readonly at: string;
  /** Optional actor (display name). */
  readonly actorName?: string;
}

export interface ProjectActivityTabProps {
  /** `null` = loading; empty array = "loaded, no activity yet". */
  readonly activity: ReadonlyArray<ProjectActivity> | null;
  /** Optional render prop wrapping a row in the substrate's <ItemLink>.
   *  If absent, the row renders the label as plain text (still valid
   *  presentation; the host can wire navigation later). */
  readonly renderItemLink?: (
    ref: ProjectActivityItemRef,
    children: ReactNode,
  ) => ReactNode;
}

function ActivityRow({
  row,
  renderItemLink,
}: {
  row: ProjectActivity;
  renderItemLink?: ProjectActivityTabProps["renderItemLink"];
}): ReactElement {
  const li: CSSProperties = {
    padding: "10px 12px",
    border: `1px solid ${PANEL_BORDER}`,
    borderRadius: 10,
    backgroundColor: PANEL_BACKGROUND,
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: 12,
  };
  const leftCol: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minWidth: 0,
    flex: 1,
  };
  const labelStyle: CSSProperties = {
    fontSize: 13,
    fontWeight: 500,
    color: TEXT_PRIMARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const summaryStyle: CSSProperties = {
    fontSize: 12,
    color: TEXT_SECONDARY,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
  const tsStyle: CSSProperties = {
    fontSize: 11,
    color: TEXT_FAINT,
    flexShrink: 0,
  };

  const inner: ReactNode = (
    <div style={leftCol}>
      <div
        style={labelStyle}
        data-testid="project-activity-row-label"
        data-ref-kind={row.ref.kind}
        data-ref-id={row.ref.id}
      >
        {row.label}
      </div>
      {row.summary !== undefined ? (
        <div style={summaryStyle} data-testid="project-activity-row-summary">
          {row.summary}
        </div>
      ) : null}
    </div>
  );

  return (
    <li
      style={li}
      data-testid="project-activity-row"
      data-activity-id={row.id}
      data-ref-kind={row.ref.kind}
      data-ref-id={row.ref.id}
    >
      {renderItemLink !== undefined ? renderItemLink(row.ref, inner) : inner}
      <time
        style={tsStyle}
        dateTime={row.at}
        title={
          row.actorName !== undefined ? `${row.actorName} · ${row.at}` : row.at
        }
        data-testid="project-activity-row-time"
      >
        {formatRelativeTime(row.at)}
      </time>
    </li>
  );
}

export function ProjectActivityTab(
  props: ProjectActivityTabProps,
): ReactElement {
  const { activity, renderItemLink } = props;
  const wrapper: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const list: CSSProperties = {
    listStyle: "none",
    padding: 0,
    margin: 0,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };
  const skeletonRow: CSSProperties = {
    height: 56,
    borderRadius: 10,
    border: `1px solid ${PANEL_BORDER}`,
    backgroundColor: PANEL_BACKGROUND,
    opacity: 0.6,
  };
  const emptyStyle: CSSProperties = {
    padding: 24,
    border: `1px dashed ${PANEL_BORDER_STRONG}`,
    borderRadius: 10,
    textAlign: "center",
    color: TEXT_SECONDARY,
    fontSize: 13,
  };

  if (activity === null) {
    return (
      <section
        data-testid="project-activity-tab"
        data-state="loading"
        style={wrapper}
      >
        <ul style={list} aria-busy="true">
          {Array.from({ length: 4 }).map((_, i) => (
            <li
              key={i}
              style={skeletonRow}
              data-testid="project-activity-skeleton"
              aria-hidden="true"
            />
          ))}
        </ul>
      </section>
    );
  }

  if (activity.length === 0) {
    return (
      <section
        data-testid="project-activity-tab"
        data-state="empty"
        style={wrapper}
      >
        <div style={emptyStyle} data-testid="project-activity-empty">
          No activity yet.
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="project-activity-tab"
      data-state="ready"
      style={wrapper}
    >
      <ul style={list} data-testid="project-activity-list">
        {activity.map((row) => (
          <ActivityRow key={row.id} row={row} renderItemLink={renderItemLink} />
        ))}
      </ul>
    </section>
  );
}
