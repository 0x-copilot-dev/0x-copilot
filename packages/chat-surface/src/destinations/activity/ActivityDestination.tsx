// Activity — destination shell (desktop redesign, Phase 4 · PR-4.5).
//
// Source: docs/plan/desktop-redesign/phase-4/PRD.md §3 (US-4.4/4.5),
// §4 (FR-4.14…FR-4.19), §9 (UI/UX checklist), and
// docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §3
// (List destinations — Activity).
//
// Activity is the single run-history feed that ABSORBS the former Agents,
// Inbox, and audit-log surfaces (PRD US-4.5). It renders every run the
// agent has done, grouped by day (`.act-day` dividers), most-recent day
// first. Each row shows: title, a meta line (tools/connectors touched),
// a mono relative time, and a status chip.
//
// This file is PURE PRESENTATION (FR-4.3): no fetch, no direct
// `router.navigate`, no SSE. The host binder (PR-4.6) composes
// `/v1/agent/conversations` + `/v1/audit` into a flat `ActivityRunRow[]`
// (there is no dedicated run-list endpoint yet — PRD §11) and passes it in
// wrapped in a `SectionResult`. The component groups by day in-shell using
// the injected `now` (FR-4.14) — day grouping never rides the wire.
//
// Navigation (FR-4.16; reshaped by PRD-04 Seam C):
//   * EVERY row is the click target (the design's row-as-button affordance).
//     Activating a row calls `onOpenRun({ conversationId, runId })` — a host
//     callback that opens the Run cockpit bound to the run's CONVERSATION (the
//     cockpit binds by conversation id, never by run id). This replaces the old
//     split where running rows called `onOpenRun(run_id)` and non-running rows
//     navigated through a cross-destination run link whose resolver returned the
//     constant noun "Run" and (on web) a route that landed on
//     `/settings#undefined`. The title is now plain text for every status.
//
// Wire types (`ActivityRunRow`, `ActivityRunStatus`, `ACTIVITY_RUN_STATUSES`)
// come from `@0x-copilot/api-types` (PR-4.1, already merged) — never
// re-declared here (FR-4.33).

import { useMemo, type CSSProperties, type ReactElement } from "react";

import type {
  ActivityRunRow,
  ActivityRunStatus,
  ConversationId,
  RunId,
  SectionResult,
} from "@0x-copilot/api-types";

import { Icon } from "../../icons/Icon";
import { BrandMark } from "../../shell/BrandMark";
import { EmptyState } from "../../shell/EmptyState";
import { StatusPill, type StatusTone } from "../../shell/StatusPill";
import { statusTone as runStatusTone } from "../../shell/statusTone";
import { formatClockTime } from "../../util/time";
import { PageLead } from "../_shared/PageLead";
import { Page } from "../_shared/Page";
import { Row } from "../_shared/Row";
import { RowList } from "../_shared/RowList";

// ===========================================================================
// Copy (DESIGN-SPEC §3 — Activity) — exported so the host + tests assert the
// exact strings rather than re-typing them.
// ===========================================================================

/**
 * Lead paragraph — the design's first two sentences verbatim
 * (`copilot-app.jsx:27-30`), restoring the "most recent first" ordering promise
 * PRD-05 makes true (PRD-08 D10). The third sentence is split so the anchor
 * wraps ONLY the phrase (below), not the whole sentence.
 */
export const ACTIVITY_LEAD_COPY =
  'Everything the agent has done, most recent first. This is the record the old build buried in an "audit log" — here it\'s a place you visit.';

/**
 * Prefix of the retention sentence; the link phrase follows and a "." closes it
 * (PRD-08 D10). The design links only `ACTIVITY_RETENTION_LINK_COPY`, not the
 * whole sentence (`copilot-app.jsx:31-37`).
 */
export const ACTIVITY_RETENTION_PREFIX_COPY =
  "Retention, export, and delete live in ";

/**
 * The retention/export/delete link PHRASE only. Rendered as an inline link that
 * invokes `onOpenRetentionSettings` (host → Settings → Privacy). FR-4.17.
 */
export const ACTIVITY_RETENTION_LINK_COPY = "Settings → Privacy";

// ===========================================================================
// Status → tone / label (single source; StatusPill renders the tone token)
// ===========================================================================

/**
 * Map an activity run status to a `StatusPill` tone. One declaration site
 * so the status→color choice is never inlined per-row (DESIGN-SPEC §9
 * single-accent discipline). Running is jade/success; stopped is
 * ember/error; paused is amber/warning; needs-input is the accent-tinted
 * "info" call-to-action; done is neutral/muted.
 */
export function activityStatusTone(status: ActivityRunStatus): StatusTone {
  // Delegate to the shell status-tone SSOT (PRD-B): done → success (jade, not
  // grey) and stopped → muted (not danger-red) — the design's semantics.
  return runStatusTone(status).tone;
}

// NOTE (PRD-02): the former per-destination label switch is deleted. Labels come
// from the shell SSOT `runStatusTone(status).label` — one lowercase vocabulary
// for run status across Activity and Chats, resolving the old `needs_input`
// disagreement ("Needs input" here vs "Needs you" in the SSOT) to `needs you`.

// ===========================================================================
// Day grouping (in-shell; FR-4.14) — pure + exported for tests
// ===========================================================================

/**
 * One day's worth of run rows. `key` is a stable local calendar-day key
 * (`YYYY-MM-DD`); `label` is the human divider ("Today" / "Yesterday" /
 * an explicit date). Rows within a group are most-recent first.
 */
export interface ActivityDayGroup {
  readonly key: string;
  readonly label: string;
  readonly rows: ReadonlyArray<ActivityRunRow>;
}

function startOfLocalDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function localDayKey(ms: number): string {
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function dayLabel(rowMs: number, nowMs: number, locale?: string): string {
  const rowMidnight = startOfLocalDay(rowMs);
  const nowMidnight = startOfLocalDay(nowMs);
  if (rowMidnight === nowMidnight) return "Today";
  // Round the midnight delta so a DST transition (a 23h/25h civil day)
  // doesn't misclassify the boundary.
  const diffDays = Math.round((nowMidnight - rowMidnight) / 86_400_000);
  if (diffDays === 1) return "Yesterday";
  // The design's `.act-day` label is `"Mon, Jul 14"` — weekday + month + day,
  // NO year (`copilot-data.jsx:648`). The year is appended ONLY when the row is
  // in a previous calendar year, so a January user reading December still gets
  // an unambiguous date without every divider carrying a redundant "2026"
  // (PRD-08 D7).
  const sameYear =
    new Date(rowMs).getFullYear() === new Date(nowMs).getFullYear();
  return new Intl.DateTimeFormat(locale ?? undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  }).format(new Date(rowMs));
}

function startedAtMs(iso: string): number {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? Number.NaN : ms;
}

// Sentinel bucket for rows with an unparseable `started_at`. Sorted last so
// bad data never hides real runs; still visible rather than dropped.
const UNKNOWN_DAY_KEY = "unknown";

interface MutableDayGroup {
  label: string;
  sortKey: number;
  rows: ActivityRunRow[];
}

/**
 * Group a flat `ActivityRunRow[]` into day buckets, most-recent day first,
 * with each day's rows sorted most-recent first. `now` is an explicit test
 * seam (FR-4.4) — the Today/Yesterday derivation is pinned by the caller,
 * never by an implicit `Date.now()`.
 */
export function groupActivityByDay(
  rows: ReadonlyArray<ActivityRunRow>,
  now: number,
  locale?: string,
): ReadonlyArray<ActivityDayGroup> {
  const buckets = new Map<string, MutableDayGroup>();

  for (const row of rows) {
    const ms = startedAtMs(row.started_at);
    const valid = !Number.isNaN(ms);
    const key = valid ? localDayKey(ms) : UNKNOWN_DAY_KEY;
    let bucket = buckets.get(key);
    if (bucket === undefined) {
      bucket = {
        label: valid ? dayLabel(ms, now, locale) : "Earlier",
        sortKey: valid ? startOfLocalDay(ms) : Number.NEGATIVE_INFINITY,
        rows: [],
      };
      buckets.set(key, bucket);
    }
    bucket.rows.push(row);
  }

  const groups = Array.from(buckets.entries()).map(([key, bucket]) => ({
    key,
    label: bucket.label,
    sortKey: bucket.sortKey,
    rows: [...bucket.rows].sort((a, b) => rowTsDesc(a, b)),
  }));

  groups.sort((a, b) => b.sortKey - a.sortKey);

  return groups.map(({ key, label, rows: dayRows }) => ({
    key,
    label,
    rows: dayRows,
  }));
}

function rowTsDesc(a: ActivityRunRow, b: ActivityRunRow): number {
  const ax = startedAtMs(a.started_at);
  const bx = startedAtMs(b.started_at);
  const aFinite = Number.isNaN(ax) ? Number.NEGATIVE_INFINITY : ax;
  const bFinite = Number.isNaN(bx) ? Number.NEGATIVE_INFINITY : bx;
  return bFinite - aFinite;
}

// ===========================================================================
// Public props
// ===========================================================================

export interface ActivityDestinationProps {
  /**
   * Server-projected run history. `null`/`undefined` = loading skeleton;
   * `status:"error"` = error + Retry; `status:"unavailable"` = distinct
   * "not enabled" empty-state; `status:"ok"` with rows = the day-grouped
   * feed; `status:"ok"` with zero rows = the "No activity yet" empty-state
   * (FR-4.2). The rows are a FLAT list — grouping happens in-shell.
   */
  readonly items?: SectionResult<ReadonlyArray<ActivityRunRow>> | null;

  /**
   * Row activation — the host opens the Run cockpit bound to the row's
   * CONVERSATION (FR-4.16; PRD-04 Seam C). Fired for EVERY row (running and
   * finished alike — the design's row-as-button affordance), carrying both the
   * conversation id (the cockpit's bind target) and the run id (which specific
   * run within the conversation the row names).
   */
  readonly onOpenRun?: (target: {
    readonly conversationId: ConversationId;
    readonly runId: RunId;
  }) => void;

  /**
   * Retention/export/delete link — host opens Settings → Privacy
   * (FR-4.17). When omitted, the pointer copy renders as plain text.
   */
  readonly onOpenRetentionSettings?: () => void;

  /** Retry callback for the `status:"error"` branch. */
  readonly onRetry?: () => void;

  /** Reference instant — test seam for day grouping + wall-clock time. */
  readonly now?: number;

  /** BCP-47 locale for the explicit-date dividers + wall-clock time; defaults to runtime. */
  readonly locale?: string;

  /**
   * IANA time zone for the row's wall-clock time — an explicit test seam
   * (mirrors `now`) so a numeric time assertion is machine-independent (D3).
   * Defaults to the runtime's zone.
   */
  readonly timeZone?: string;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function ActivityDestination(
  props: ActivityDestinationProps = {},
): ReactElement {
  const {
    items = null,
    onOpenRun,
    onOpenRetentionSettings,
    onRetry,
    now,
    locale,
    timeZone,
  } = props;

  const nowMs = now ?? Date.now();

  const groups = useMemo<ReadonlyArray<ActivityDayGroup>>(() => {
    if (items === null || items === undefined) return [];
    if (items.status !== "ok" || items.data === undefined) return [];
    return groupActivityByDay(items.data, nowMs, locale);
  }, [items, nowMs, locale]);

  const dataState = resolveDataState(items, groups.length);

  return (
    <section
      role="region"
      aria-label="Activity"
      data-component="activity-destination"
      data-testid="activity-destination"
      data-state={dataState}
      style={rootStyle}
    >
      <Page style={innerStyle}>
        <ActivityLead onOpenRetentionSettings={onOpenRetentionSettings} />
        {/* Retry lives in the page header, not on the error branch (D8): a
            successfully-loaded but stale list also needs a refresh control, so
            it renders in every non-loading state (error, empty, ready). */}
        {onRetry !== undefined && dataState !== "loading" ? (
          <div style={retryRowStyle}>
            <button
              type="button"
              onClick={onRetry}
              style={retryButtonStyle}
              data-testid="activity-retry"
            >
              Refresh
            </button>
          </div>
        ) : null}
        <div style={bodyStyle} data-testid="activity-body">
          {renderBody({ items, groups, onOpenRun, locale, timeZone })}
        </div>
      </Page>
    </section>
  );
}

function resolveDataState(
  items: ActivityDestinationProps["items"],
  groupCount: number,
): "loading" | "error" | "empty" | "ready" {
  if (items === null || items === undefined) return "loading";
  // `unavailable` folds into `error` (D8): no binder constructs it, and an
  // Activity that can't load IS a failure to load — not a distinct "not
  // licensed" state. `SectionResult.status` keeps the member for other surfaces.
  if (items.status === "error" || items.status === "unavailable")
    return "error";
  return groupCount === 0 ? "empty" : "ready";
}

// ===========================================================================
// Lead paragraph (`.pg-lead`) + retention link
// ===========================================================================

function ActivityLead({
  onOpenRetentionSettings,
}: {
  readonly onOpenRetentionSettings?: () => void;
}): ReactElement {
  return (
    <PageLead data-testid="activity-lead">
      <span>{ACTIVITY_LEAD_COPY} </span>
      <span>{ACTIVITY_RETENTION_PREFIX_COPY}</span>
      {onOpenRetentionSettings !== undefined ? (
        <button
          type="button"
          onClick={onOpenRetentionSettings}
          style={leadLinkStyle}
          data-testid="activity-retention-link"
        >
          {ACTIVITY_RETENTION_LINK_COPY}
        </button>
      ) : (
        <span data-testid="activity-retention-copy">
          {ACTIVITY_RETENTION_LINK_COPY}
        </span>
      )}
      <span>.</span>
    </PageLead>
  );
}

// ===========================================================================
// Body — the 4-state machine (FR-4.2)
// ===========================================================================

interface BodyArgs {
  readonly items: ActivityDestinationProps["items"];
  readonly groups: ReadonlyArray<ActivityDayGroup>;
  readonly onOpenRun: ActivityDestinationProps["onOpenRun"];
  readonly locale?: string;
  readonly timeZone?: string;
}

function renderBody(args: BodyArgs): ReactElement {
  const { items, groups, onOpenRun, locale, timeZone } = args;

  // Loading — skeleton day-groups (FR-4.2). role="status" announces the
  // busy state; the skeleton chrome itself is aria-hidden.
  if (items === null || items === undefined) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading activity"
        data-testid="activity-loading"
        data-state="loading"
        style={groupsWrapStyle}
      >
        {[0, 1].map((i) => (
          <DaySkeleton key={i} />
        ))}
      </div>
    );
  }

  // Error — role="alert" on the error node (DESIGN-SPEC §9). Retry lives in the
  // page header (D8), so this branch carries no action of its own. `unavailable`
  // folds in here (D8): it is unreachable and, if it ever arrived, an Activity
  // that can't load IS a load failure.
  if (items.status === "error" || items.status === "unavailable") {
    return (
      <div role="alert" data-testid="activity-error">
        <EmptyState
          title="Couldn't load activity"
          body={items.error ?? "Network error — try again."}
        />
      </div>
    );
  }

  // Ready-but-empty — per-view empty copy (FR-4.2 / §9). The copy no longer
  // asserts "the agent hasn't run anything" (which the client cannot know); it
  // states only what the surface will do once a run exists (D8).
  if (groups.length === 0) {
    return (
      <div data-testid="activity-empty">
        <EmptyState
          title="Nothing here yet"
          body="Start a run and it'll show up here, grouped by day."
        />
      </div>
    );
  }

  // Ready — day-grouped run rows, most-recent day first.
  return (
    <div style={groupsWrapStyle} data-testid="activity-groups">
      {groups.map((group) => (
        <DayGroup
          key={group.key}
          group={group}
          onOpenRun={onOpenRun}
          locale={locale}
          timeZone={timeZone}
        />
      ))}
    </div>
  );
}

// ===========================================================================
// DayGroup — one `.act-day` divider + its run rows
// ===========================================================================

function DayGroup({
  group,
  onOpenRun,
  locale,
  timeZone,
}: {
  readonly group: ActivityDayGroup;
  readonly onOpenRun: ActivityDestinationProps["onOpenRun"];
  readonly locale?: string;
  readonly timeZone?: string;
}): ReactElement {
  const headingId = `activity-day-${group.key}`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid="activity-day-group"
      data-day-key={group.key}
      data-row-count={group.rows.length}
      style={dayGroupStyle}
    >
      <h2
        id={headingId}
        className="act-day"
        data-testid="activity-day"
        data-day-key={group.key}
        style={dayDividerStyle}
      >
        <span>{group.label}</span>
        {/* `.act-day::after` — a hairline trailing the label to the row edge. */}
        <span aria-hidden="true" style={dayHairlineStyle} />
      </h2>
      <RowList<ActivityRunRow>
        ariaLabel={`Runs on ${group.label}`}
        items={group.rows}
        keyFor={(row) => row.run_id}
        data-testid="activity-day-rowlist"
        renderRow={(row) => (
          <ActivityRow
            row={row}
            onOpenRun={onOpenRun}
            locale={locale}
            timeZone={timeZone}
          />
        )}
      />
    </section>
  );
}

// ===========================================================================
// ActivityRow — one run row (title, meta, status, time)
// ===========================================================================

function ActivityRow({
  row,
  onOpenRun,
  locale,
  timeZone,
}: {
  readonly row: ActivityRunRow;
  readonly onOpenRun: ActivityDestinationProps["onOpenRun"];
  readonly locale?: string;
  readonly timeZone?: string;
}): ReactElement {
  const isRunning = row.status === "running";
  const tone = activityStatusTone(row.status);
  const presentation = runStatusTone(row.status);

  // Leading icon (`.lrow__ic`): a live run shows the brand turbine, every other
  // run a clock — a DIRECT child of `<Row>`'s 28x28 tile. The "this is live"
  // jade tint rides `iconTone="success"` on the tile itself (below), NOT on a
  // wrapper span around the glyph (which never reached the tile — the bug D5
  // deletes). The glyph is sized 18 as authored and the `.ui-list-row` recipe
  // forces it to 15px inside the tile, exactly as the design overrides its own
  // `<Mark size={18}>`.
  const icon = isRunning ? (
    <BrandMark size={18} />
  ) : (
    <Icon name="clock" size={18} />
  );

  // The title is PLAIN TEXT for every status (PRD-04 Seam C). The row itself is
  // the click target, so the title is not a link — no accent, no anchor. The
  // real `row.title` renders directly; the old cross-destination run link that
  // discarded it in favour of the constant "Run" is gone.
  const title = <span data-testid="activity-row-title">{row.title}</span>;

  // The tools/connectors line is BODY font (the row `sub`), not mono — the mono
  // is reserved for the relative time in the right meta column.
  const sub =
    row.meta.length > 0 ? (
      <span data-testid="activity-row-meta">{row.meta}</span>
    ) : undefined;

  const chip = (
    <StatusPill
      status={tone}
      label={presentation.label}
      showDot={presentation.showDot}
    />
  );

  // Wall-clock time (D3): Activity is day-grouped, so the container already
  // establishes the date — a relative "1d ago" under a "Yesterday" heading is
  // redundant and erases within-day ordering. The `<time dateTime>` wrapper
  // keeps machine-readable exactness regardless of display format.
  const meta = (
    <time
      dateTime={row.started_at}
      data-testid="activity-row-time"
      style={{ font: "inherit", color: "inherit" }}
    >
      {formatClockTime(row.started_at, locale, timeZone)}
    </time>
  );

  // Every row activates (PRD-04 Seam C — the design's row-as-button). The host
  // opens the Run cockpit bound to the row's CONVERSATION; the run id names the
  // specific run within it.
  const activate =
    onOpenRun !== undefined
      ? () =>
          onOpenRun({
            conversationId: row.conversation_id,
            runId: row.run_id,
          })
      : undefined;

  return (
    <Row
      data-testid="activity-row"
      data-run-id={row.run_id}
      data-conversation-id={row.conversation_id}
      data-status={row.status}
      data-row-title={row.title}
      data-open={isRunning ? "run" : "detail"}
      icon={icon}
      iconTone={isRunning ? "success" : "default"}
      title={title}
      chip={chip}
      sub={sub}
      meta={meta}
      // Navigation affordance (D4): the design marks the navigable (live) row
      // with a trailing chevron and reserves a 16px spacer on every other row.
      // First call site of the chevron glyph that has sat unused in the icon
      // SSOT, byte-identical to the design's `Icon.chevR`.
      trailing={isRunning ? <Icon name="chevronRight" size={15} /> : undefined}
      onActivate={activate}
      ariaLabel={activate !== undefined ? `Open run: ${row.title}` : undefined}
    />
  );
}

// ===========================================================================
// DaySkeleton — loading placeholder
// ===========================================================================

function DaySkeleton(): ReactElement {
  return (
    <div style={dayGroupStyle} aria-hidden="true">
      <span
        data-testid="activity-skeleton-day"
        style={{ ...dayDividerStyle, ...skeletonBar(30) }}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            data-testid="activity-skeleton-row"
            style={skeletonRowStyle}
          >
            <span style={skeletonBar(45)} />
            <span style={skeletonBar(15)} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// Styles — tokens only (no hardcoded palette; DESIGN-SPEC §0 / §9)
// ===========================================================================

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  background: "var(--color-bg, #131316)",
  color: "var(--color-text, #ededee)",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
};

// The `.pg` content-column shell is now the shared `_shared/Page` primitive
// (PRD-10 D4 / addendum): 960px column + `20px 24px 40px` padding, LEFT-aligned
// (no `margin: 0 auto`). `innerStyle` keeps ONLY the feed's own column layout;
// the shell geometry moved to `<Page>`, so the column-width cap no longer lives
// here.
const innerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

// Header Retry row — right-aligned above the feed. Renders in every non-loading
// state (D8), so a stale-but-loaded list has a refresh control too.
const retryRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
};

const retryButtonStyle: CSSProperties = {
  background: "transparent",
  border: "1px solid var(--color-border)",
  borderRadius: "var(--radius-sm, 6px)",
  padding: "4px 10px",
  font: "inherit",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-muted)",
  cursor: "pointer",
};

const leadLinkStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  font: "inherit",
  color: "var(--color-accent, #d97757)",
  textDecoration: "underline",
  textUnderlineOffset: 2,
  cursor: "pointer",
};

const bodyStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  padding: "8px 0",
};

const groupsWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 20,
};

const dayGroupStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

// `.act-day` — a QUIET mono day divider (the design's `.act-day`,
// copilot.css:1683-1697): 10px mono, regular weight, NO tracking, NO transform,
// with a trailing hairline (`.act-day::after`) running to the row edge. It is
// NOT the section header (`.sect-h` — 9.5px, tracked, uppercase); the live
// divider used to wear both classes and be styled as the wrong one (PRD-08 D7).
const dayDividerStyle: CSSProperties = {
  margin: 0,
  display: "flex",
  alignItems: "center",
  gap: 10,
  fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, monospace)",
  fontSize: "var(--font-size-mono-10, 10px)",
  fontWeight: "var(--font-weight-regular)",
  // No `letter-spacing` and no `text-transform` — the design's `.act-day` sets
  // neither (initial `normal` / `none`). The old divider set both (the `.sect-h`
  // clothes); dropping them is the fix (D7).
  color: "var(--color-text-subtle, #7e7e84)",
};

// The trailing hairline of `.act-day`.
const dayHairlineStyle: CSSProperties = {
  flex: 1,
  height: 1,
  background: "var(--color-border, #232325)",
};

const skeletonRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  padding: "8px 10px",
  borderRadius: "var(--radius-sm, 6px)",
  border: "1px solid var(--color-border, #232325)",
  backgroundColor: "var(--color-bg-elevated, #161617)",
};

function skeletonBar(widthPercent: number): CSSProperties {
  return {
    display: "inline-block",
    width: `${widthPercent}%`,
    height: 10,
    borderRadius: 4,
    background: "var(--color-border, #232325)",
    opacity: 0.7,
  };
}
