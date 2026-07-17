// ArchiveBlockedDialog — P6.5-B2
//
// Rendered when `DELETE /v1/projects/{id}` responds 409 with a
// `LivenessReport` body (Projects Extensions PRD §6.3). Pure
// presentation: the host passes in the parsed report (and an optional
// "view active runs" navigation callback the host wires up). The
// dialog itself never fetches, never decides routing, never knows the
// archive endpoint URL.
//
// SP-1 primitives only:
//   - StatusPill — partial-failure warning banner (PRD §6.3 last
//     paragraph) and per-component breakdown chips.
//   - FilterTabs — breakdown by component with per-row counts (the
//     same "label + count" idiom the rest of the surface uses; here
//     the host doesn't need to wire selection, but FilterTabs is the
//     one-source-of-truth for "label + numeric chip" rows so we use
//     it rather than rolling a parallel chip primitive).
//   - EmptyState — defensive: if a host opens the dialog with an
//     `is_alive=false` (zero-count) report, we degrade gracefully
//     rather than render an empty modal.
//
// File-naming convention follows the kebab-case dialog style already
// established in this directory (cf. `transfer-ownership-dialog.tsx`).
//
// LivenessReport shape mirrors the PRD §3.3 / §3.4 wire contract. We
// define a LOCAL stub here per the task brief; the orchestrator
// rewires it to `@0x-copilot/api-types`'s `LivenessReport`
// at merge time (same pattern as `_projects-stub.ts`).

import { useMemo, type CSSProperties, type ReactElement } from "react";

import { EmptyState } from "../../shell/EmptyState";
import { FilterTabs, type FilterTabOption } from "../../shell/FilterTabs";
import { StatusPill } from "../../shell/StatusPill";

// ── Local LivenessReport stub (PRD §3.3 mirror) ──────────────────────
//
// TODO(merge): rewire to "@0x-copilot/api-types"
//   import type { LivenessReport, LivenessDetail } from
//     "@0x-copilot/api-types";

export type LivenessDetailSource =
  | "ai_backend.runs"
  | "ai_backend.approvals"
  | "backend.routines"
  | "backend.inbox";

export interface LivenessDetail {
  readonly source: LivenessDetailSource;
  readonly count: number;
  readonly is_alive: boolean;
  readonly error: string | null;
  readonly fetched_at: string;
}

export interface LivenessReport {
  readonly project_id: string;
  readonly tenant_id: string;
  readonly is_alive: boolean;
  readonly active_runs: number;
  readonly pending_approvals: number;
  readonly active_routines: number;
  readonly in_flight_inbox: number;
  readonly details: ReadonlyArray<LivenessDetail>;
  readonly computed_at: string;
  readonly cache_hit: boolean;
}

// ── Breakdown axis ───────────────────────────────────────────────────
//
// The PRD §6.3 modal lists four numbered components. We model them as
// FilterTab options so we share one chip-rendering path with every
// other destination's tab row.

type BreakdownSlug =
  | "active_runs"
  | "pending_approvals"
  | "active_routines"
  | "in_flight_inbox";

interface BreakdownRow {
  readonly slug: BreakdownSlug;
  readonly count: number;
  readonly singular: string;
  readonly plural: string;
}

function buildBreakdownRows(
  report: LivenessReport,
): ReadonlyArray<BreakdownRow> {
  return [
    {
      slug: "active_runs",
      count: report.active_runs,
      singular: "active run",
      plural: "active runs",
    },
    {
      slug: "pending_approvals",
      count: report.pending_approvals,
      singular: "pending approval",
      plural: "pending approvals",
    },
    {
      slug: "active_routines",
      count: report.active_routines,
      singular: "active routine",
      plural: "active routines",
    },
    {
      slug: "in_flight_inbox",
      count: report.in_flight_inbox,
      singular: "in-flight inbox item",
      plural: "in-flight inbox items",
    },
  ];
}

function headlineCounts(rows: ReadonlyArray<BreakdownRow>): string {
  // "N runs / M routines / K inbox items active" — the PRD §6 §3 title
  // says runs / routines / inbox; we additionally include approvals
  // (the fourth liveness source) so the user sees every blocking row.
  const nonZero = rows.filter((r) => r.count > 0);
  if (nonZero.length === 0) return "no active items";
  return nonZero
    .map((r) => `${r.count} ${r.count === 1 ? r.singular : r.plural}`)
    .join(" / ");
}

// ── Props ────────────────────────────────────────────────────────────

export interface ArchiveBlockedDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;

  /** The project's current name — rendered in the title. */
  readonly projectName: string;

  /** Parsed `liveness` field from the 409 archive response. */
  readonly livenessReport: LivenessReport;

  /**
   * Optional. When provided, the dialog renders a "View active runs"
   * button. The host wires the actual navigation (cross-audit §3.3
   * `<ItemLink>` registry or a destination route push).
   */
  readonly onViewActiveRuns?: () => void;
}

// ── Styles ───────────────────────────────────────────────────────────

const PANEL_BACKGROUND = "var(--color-surface)";
const PANEL_BORDER = "var(--color-border)";
const TEXT_PRIMARY = "var(--color-text)";
const TEXT_SECONDARY = "var(--color-text-muted)";

const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  backgroundColor: "rgba(0,0,0,0.6)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1100,
};

const cardStyle: CSSProperties = {
  width: 520,
  maxWidth: "calc(100vw - 32px)",
  backgroundColor: PANEL_BACKGROUND,
  color: TEXT_PRIMARY,
  border: `1px solid ${PANEL_BORDER}`,
  borderRadius: 12,
  padding: 20,
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const titleStyle: CSSProperties = {
  margin: 0,
  fontSize: "var(--font-size-lg, 16px)",
  fontWeight: 600,
};

const subtitleStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: TEXT_SECONDARY,
};

const buttonRow: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
  marginTop: 4,
};

const cancelStyle: CSSProperties = {
  height: 34,
  padding: "0 14px",
  borderRadius: 8,
  border: `1px solid ${PANEL_BORDER}`,
  backgroundColor: "transparent",
  color: TEXT_SECONDARY,
  fontSize: "var(--font-size-sm, 13px)",
  cursor: "pointer",
};

const primaryStyle: CSSProperties = {
  height: 34,
  padding: "0 14px",
  borderRadius: 8,
  border: "none",
  backgroundColor: "var(--color-accent, #d97757)",
  color: "var(--color-accent-contrast, #ffffff)",
  fontSize: "var(--font-size-sm, 13px)",
  fontWeight: 600,
  cursor: "pointer",
};

// ── Dialog ───────────────────────────────────────────────────────────

export function ArchiveBlockedDialog(
  props: ArchiveBlockedDialogProps,
): ReactElement | null {
  const { open, onClose, projectName, livenessReport, onViewActiveRuns } =
    props;

  const rows = useMemo(
    () => buildBreakdownRows(livenessReport),
    [livenessReport],
  );

  const hasErrors = useMemo(
    () => livenessReport.details.some((d) => d.error !== null),
    [livenessReport.details],
  );

  const headline = useMemo(() => headlineCounts(rows), [rows]);

  // Breakdown chips: FilterTabs in "display-only" mode (single value,
  // no-op onChange). One source of truth for "label + count" rows.
  const filterValue: BreakdownSlug = rows[0]!.slug;
  const filterOptions: ReadonlyArray<FilterTabOption<BreakdownSlug>> = rows.map(
    (r) => ({
      slug: r.slug,
      label: r.count === 1 ? r.singular : r.plural,
      count: r.count,
    }),
  );

  if (!open) return null;

  // Defensive zero-count branch: a host that opens the dialog with an
  // is_alive=false report (e.g. a race where the report cleared
  // between the failing call and the modal mount) should see the same
  // EmptyState shape every other destination uses, not an empty card.
  const everyRowZero = rows.every((r) => r.count === 0);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="archive-blocked-title"
      style={backdropStyle}
      data-testid="archive-blocked-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div style={cardStyle}>
        <h2 id="archive-blocked-title" style={titleStyle}>
          Can&apos;t archive &ldquo;{projectName}&rdquo;
        </h2>

        {everyRowZero ? (
          <EmptyState
            title="No active work blocking archive"
            body="The liveness report came back empty. Try archiving again — the previous request may have raced with completing work."
            action={{ label: "Close", onClick: onClose }}
          />
        ) : (
          <>
            <div style={subtitleStyle} data-testid="archive-blocked-headline">
              Can&apos;t archive — {headline} active.
            </div>

            {hasErrors ? (
              <div
                data-testid="archive-blocked-partial-failure"
                style={{ display: "inline-flex" }}
              >
                <StatusPill
                  status="warning"
                  label="Partial result — one or more checks errored"
                />
              </div>
            ) : null}

            <div data-testid="archive-blocked-breakdown">
              <FilterTabs<BreakdownSlug>
                value={filterValue}
                onChange={() => {
                  /* display-only; selection is not meaningful here */
                }}
                options={filterOptions}
                ariaLabel="Active work breakdown"
                idPrefix="archive-blocked-breakdown"
              />
            </div>
          </>
        )}

        <div style={buttonRow}>
          {onViewActiveRuns !== undefined && livenessReport.active_runs > 0 ? (
            <button
              type="button"
              style={primaryStyle}
              onClick={onViewActiveRuns}
              data-testid="archive-blocked-view-runs"
            >
              View active runs
            </button>
          ) : null}
          <button
            type="button"
            style={cancelStyle}
            onClick={onClose}
            data-testid="archive-blocked-cancel"
            aria-label="Close archive blocked dialog"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
