// Home — the morning briefing destination shell (P2-B1).
//
// This file is the *layout + data-flow scaffolding*. The seven section
// bodies (HomeGreeting, HomeAgentActivityFeed, HomePinnedChatsGrid,
// HomeRecentRunsList, HomeFavoriteToolsList, HomeTodaysFocusList,
// HomeUpcomingMeetingsList) are owned by P2-B2 / P2-B3 and ship as files
// under `./sections/`. The shell defines:
//
//   1. The fixed vertical section order (sub-PRD §3.1, Q6 — Wave 4+
//      reorder).
//   2. Per-section status routing — `SectionResult.status` → either the
//      section's data renderer (P2-B2/B3) or an `<EmptyState>` with the
//      section-specific retry / CTA copy (sub-PRD §12.6).
//   3. The SSE subscription that keeps the activity feed live, with
//      silent exponential-backoff reconnect (1s → 30s, sub-PRD Q8).
//
// `_home-stub.ts` carries the wire-types matching sub-PRD §4 until
// P2-A1's `@enterprise-search/api-types/home` merges; every import of
// the stub is annotated `TODO(merge): rewire to "@enterprise-search/api-types"`.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

import type { SectionResult } from "@enterprise-search/api-types";

import { useTransport } from "../../providers/TransportProvider";
import { EmptyState } from "../../shell/EmptyState";
import { PageHeader } from "../../shell/PageHeader";

// TODO(merge): rewire to "@enterprise-search/api-types"
import type {
  AgentActivityEntry,
  HomeGreeting as HomeGreetingT,
  HomePayload,
} from "./_home-stub";

// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  AgentActivityEntry,
  AgentActivityKind,
  FavoriteToolSummary,
  HomeGreeting,
  HomePayload,
  HomeResponse,
  MeetingSummary,
  PinnedChatSummary,
  QuickAction,
  RecentRunStatus,
  RecentRunSummary,
  StarredProjectSummary,
  TimeOfDay,
  TodoSummary,
} from "./_home-stub";

// === SSE reconnect schedule (sub-PRD Q8) ===================================
// Silent exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s cap. No
// "paused" indicator — mobile-network blips are common and a chip adds
// anxiety.
const SSE_BACKOFF_SCHEDULE_MS: ReadonlyArray<number> = [
  1_000, 2_000, 4_000, 8_000, 16_000, 30_000,
];
const SSE_MAX_BACKOFF_MS = 30_000;
const SSE_ACTIVITY_EVENT_NAME = "home_activity";
const SSE_PATH = "/v1/home/stream";

// Cap on the live agent-activity feed (sub-PRD §3.5).
const ACTIVITY_FEED_CAP = 15;

// ===========================================================================
// Public props
// ===========================================================================

/** Stable section keys used for telemetry + per-section retry routing. */
export type HomeSectionKey =
  | "agent_activity"
  | "pinned_chats"
  | "recent_runs"
  | "favorite_tools"
  | "todays_focus"
  | "upcoming_meetings"
  | "starred_projects";

export interface HomeDestinationProps {
  /**
   * Server-resolved home payload. When `null`, the shell renders the
   * skeleton; when set, the shell mounts each section with its
   * `SectionResult<T>`.
   *
   * P2-C wires this from `apps/frontend` after fetching `/v1/home`. The
   * shell does **not** fetch on its own — keeping fetch concerns in the
   * host app is what lets the same shell power web + desktop substrates
   * with their own caching/transport strategies.
   */
  readonly homeResponse?: HomePayload | null;

  /**
   * Optional host-supplied retry callback. Wired to every section's
   * "Retry section" empty-state action when `SectionResult.status ===
   * "error"`. P2-C wires this to `GET /v1/home?refresh_section=<key>`.
   */
  readonly onRetrySection?: (sectionKey: HomeSectionKey) => void;

  /**
   * When true, the destination opens the SSE activity stream and
   * prepends live `home_activity` events to the feed. Defaults to
   * `true` — disable in tests or when the host already owns the stream.
   */
  readonly enableActivityStream?: boolean;
}

// ===========================================================================
// Top-level shell
// ===========================================================================

export function HomeDestination(
  props: HomeDestinationProps = {},
): ReactElement {
  const {
    homeResponse = null,
    onRetrySection,
    enableActivityStream = true,
  } = props;
  const transport = useTransport();

  // === Live activity feed (SSE) =========================================
  // Stream-delivered entries are stored newest-first and merged with the
  // server-rendered backlog at display time. We keep them in shell state
  // (not in a section component) because the section component lives
  // below the shell and re-mounts on every section result transition;
  // the stream is a cross-render concern owned by the shell.
  const [liveActivity, setLiveActivity] = useState<
    ReadonlyArray<AgentActivityEntry>
  >([]);

  // Reset live entries whenever the server payload's `cached_at` changes
  // — the new payload's `agent_activity.data` already contains
  // everything we need; keeping local live entries would duplicate rows.
  const cachedAt = homeResponse?.cached_at ?? null;
  useEffect(() => {
    setLiveActivity([]);
  }, [cachedAt]);

  // SSE subscription with 1s → 30s exponential backoff (sub-PRD Q8).
  useEffect(() => {
    if (!enableActivityStream) return;
    if (homeResponse === null) return; // wait for first payload

    let cancelled = false;
    let backoffIndex = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let subscription: { close(): void } | null = null;

    const connect = (): void => {
      if (cancelled) return;
      subscription = transport.subscribeServerSentEvents({
        path: SSE_PATH,
        eventName: SSE_ACTIVITY_EVENT_NAME,
        onMessage: (raw) => {
          if (cancelled) return;
          // A successful message confirms the stream is healthy — reset
          // the backoff counter so the next disconnect starts from 1s.
          backoffIndex = 0;
          try {
            const parsed = JSON.parse(raw) as AgentActivityEntry;
            setLiveActivity((prev) => prependLiveActivity(prev, parsed));
          } catch {
            // Malformed payload: silently drop. The server emits the
            // schema; a parse error is a server bug, not user-visible.
          }
        },
        onOpen: () => {
          if (cancelled) return;
          backoffIndex = 0;
        },
        onError: () => {
          if (cancelled) return;
          // Schedule a reconnect along the silent-retry schedule. No
          // user-visible "paused" indicator (sub-PRD Q8).
          const delay =
            SSE_BACKOFF_SCHEDULE_MS[backoffIndex] ?? SSE_MAX_BACKOFF_MS;
          backoffIndex = Math.min(
            backoffIndex + 1,
            SSE_BACKOFF_SCHEDULE_MS.length - 1,
          );
          if (subscription !== null) {
            subscription.close();
            subscription = null;
          }
          reconnectTimer = setTimeout(connect, delay);
        },
      });
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (subscription !== null) subscription.close();
    };
  }, [enableActivityStream, transport, homeResponse]);

  // === Merge backlog + live entries =====================================
  // Live entries (newest first) come before the server-rendered backlog;
  // de-dup by `id` so a server refresh that catches up to a live event
  // doesn't double-render. Cap at 15 (sub-PRD §3.1.2).
  const mergedActivity = useMemo(
    () => mergeActivity(homeResponse, liveActivity),
    [homeResponse, liveActivity],
  );

  // === Greeting (PageHeader) ============================================
  const greetingTitle = formatGreetingTitle(homeResponse?.greeting ?? null);
  const greetingSubtitle = formatGreetingSubtitle(
    homeResponse?.greeting ?? null,
  );

  // === Per-section retry shim ===========================================
  const retryFor = useCallback(
    (key: HomeSectionKey) => () => {
      if (onRetrySection !== undefined) onRetrySection(key);
    },
    [onRetrySection],
  );

  // === Styles ============================================================
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
    maxWidth: 1000,
    margin: "0 auto",
    padding: "24px 28px 48px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };
  const sectionGridStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 24,
  };

  // === Loading state ====================================================
  if (homeResponse === null) {
    return (
      <section
        aria-label="Home destination"
        data-testid="home-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={containerStyle}>
          <PageHeader title="Good morning." subtitle="Loading your briefing…" />
          <div
            style={sectionGridStyle}
            data-testid="home-sections"
            data-state="loading"
            aria-hidden="true"
          >
            {SECTION_ORDER.map((key) => (
              <SectionSkeleton key={key} sectionKey={key} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  // === Ready state ======================================================
  return (
    <section
      aria-label="Home destination"
      data-testid="home-destination"
      data-state="ready"
      data-cached-at={homeResponse.cached_at}
      style={rootStyle}
    >
      <div style={containerStyle}>
        <PageHeader title={greetingTitle} subtitle={greetingSubtitle} />
        <div
          style={sectionGridStyle}
          data-testid="home-sections"
          data-state="ready"
        >
          {/* §3.1.2 Agent activity feed — the only live-updated section. */}
          <SectionShell
            sectionKey="agent_activity"
            heading="Agent activity"
            result={withReplacedData(
              homeResponse.agent_activity,
              mergedActivity,
            )}
            onRetry={retryFor("agent_activity")}
            emptyTitle="Nothing's happened yet today."
            emptyBody="Atlas activity will appear here as agents work in the background."
            renderOk={(rows) => (
              // TODO(P2-B2): replace with <HomeAgentActivityFeed rows={rows} />.
              <SectionPlaceholder
                kind="agent_activity"
                testId="home-section-agent-activity-content"
                count={rows.length}
              />
            )}
          />

          {/* §3.1.3 Pinned chats grid */}
          <SectionShell
            sectionKey="pinned_chats"
            heading="Pinned chats"
            result={homeResponse.pinned_chats}
            onRetry={retryFor("pinned_chats")}
            emptyTitle="Pin a chat to keep it here."
            renderOk={(rows) => (
              // TODO(P2-B2): replace with <HomePinnedChatsGrid items={rows} />.
              <SectionPlaceholder
                kind="pinned_chats"
                testId="home-section-pinned-content"
                count={rows.length}
              />
            )}
          />

          {/* §3.1.4 Recent runs */}
          <SectionShell
            sectionKey="recent_runs"
            heading="Recent runs"
            result={homeResponse.recent_runs}
            onRetry={retryFor("recent_runs")}
            emptyTitle="Recent runs will appear here as Atlas works."
            renderOk={(rows) => (
              // TODO(P2-B2): replace with <HomeRecentRunsList items={rows} />.
              <SectionPlaceholder
                kind="recent_runs"
                testId="home-section-recent-runs-content"
                count={rows.length}
              />
            )}
          />

          {/* §3.1.5 Favorite tools */}
          <SectionShell
            sectionKey="favorite_tools"
            heading="Favorite tools"
            result={homeResponse.favorite_tools}
            onRetry={retryFor("favorite_tools")}
            emptyTitle="Star a tool to bookmark it."
            renderOk={(rows) => (
              // TODO(P2-B3): replace with <HomeFavoriteToolsList items={rows} />.
              <SectionPlaceholder
                kind="favorite_tools"
                testId="home-section-favorites-content"
                count={rows.length}
              />
            )}
          />

          {/* §3.1.6 Today's focus (depends on Todos — may be unavailable) */}
          <SectionShell
            sectionKey="todays_focus"
            heading="Today's focus"
            result={homeResponse.todays_focus}
            onRetry={retryFor("todays_focus")}
            emptyTitle="Nothing on your list for today."
            emptyBody="Want to plan? Open todos to add the first one."
            unavailableTitle="Todos coming soon"
            unavailableBody="Today's focus will appear here once the Todos destination is enabled for your workspace."
            renderOk={(rows) => (
              // TODO(P2-B3): replace with <HomeTodaysFocusList items={rows} />.
              <SectionPlaceholder
                kind="todays_focus"
                testId="home-section-focus-content"
                count={rows.length}
              />
            )}
          />

          {/* §3.1.7 Upcoming meetings — null = no calendar connector. */}
          <SectionShell
            sectionKey="upcoming_meetings"
            heading="Upcoming meetings"
            result={homeResponse.upcoming_meetings}
            onRetry={retryFor("upcoming_meetings")}
            emptyTitle="No meetings today."
            connectorMissing={homeResponse.upcoming_meetings === null}
            connectorMissingTitle="Connect a calendar"
            connectorMissingBody="See today's meetings here once your Google Calendar or Microsoft Calendar is connected."
            connectorMissingAction={{
              label: "Connect a calendar",
              onClick: () => {
                /* P2-C wires this to /connectors */
              },
            }}
            renderOk={(rows) => (
              // TODO(P2-B3): replace with <HomeUpcomingMeetingsList items={rows} />.
              <SectionPlaceholder
                kind="upcoming_meetings"
                testId="home-section-meetings-content"
                count={rows.length}
              />
            )}
          />
        </div>
      </div>
    </section>
  );
}

// ===========================================================================
// §3.1 fixed section order
// ===========================================================================

// Sub-PRD Q6 — order is fixed in Phase 2; per-user reorder is Wave 4+.
const SECTION_ORDER: ReadonlyArray<HomeSectionKey> = [
  "agent_activity",
  "pinned_chats",
  "recent_runs",
  "favorite_tools",
  "todays_focus",
  "upcoming_meetings",
];

// ===========================================================================
// SectionShell — uniform partial-failure rendering for every section
// ===========================================================================

interface SectionShellProps<T> {
  readonly sectionKey: HomeSectionKey;
  readonly heading: string;
  /**
   * Per-section status. `null` here is the *special* upcoming-meetings
   * path: server returns `upcoming_meetings: null` when no calendar
   * connector is present (sub-PRD Q4). The shell pairs `result === null`
   * with `connectorMissing === true` to render the CTA.
   */
  readonly result: SectionResult<ReadonlyArray<T>> | null;
  readonly onRetry: () => void;
  readonly emptyTitle: string;
  readonly emptyBody?: string;
  readonly emptyAction?: {
    readonly label: string;
    readonly onClick: () => void;
  };
  readonly unavailableTitle?: string;
  readonly unavailableBody?: string;
  readonly renderOk: (data: ReadonlyArray<T>) => ReactNode;
  /** Upcoming-meetings only: render the connect-a-calendar CTA. */
  readonly connectorMissing?: boolean;
  readonly connectorMissingTitle?: string;
  readonly connectorMissingBody?: string;
  readonly connectorMissingAction?: {
    readonly label: string;
    readonly onClick: () => void;
  };
}

function SectionShell<T>({
  sectionKey,
  heading,
  result,
  onRetry,
  emptyTitle,
  emptyBody,
  emptyAction,
  unavailableTitle,
  unavailableBody,
  renderOk,
  connectorMissing,
  connectorMissingTitle,
  connectorMissingBody,
  connectorMissingAction,
}: SectionShellProps<T>): ReactElement {
  const wrapperStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 12,
  };
  const headingStyle: CSSProperties = {
    fontSize: "var(--font-size-md, 14px)",
    fontWeight: 600,
    color: "var(--color-text)",
    margin: 0,
  };

  let body: ReactNode;
  let sectionStatus: string;

  if (connectorMissing === true) {
    sectionStatus = "connector_missing";
    body = (
      <EmptyState
        title={connectorMissingTitle ?? "Not available"}
        body={connectorMissingBody}
        action={connectorMissingAction}
      />
    );
  } else if (result === null) {
    // Defensive: a section reported `null` outside the upcoming-meetings
    // CTA path. Render a generic unavailable state rather than
    // collapsing the layout.
    sectionStatus = "null";
    body = (
      <EmptyState
        title={unavailableTitle ?? "Coming soon"}
        body={unavailableBody}
      />
    );
  } else if (result.status === "error") {
    sectionStatus = "error";
    body = (
      <EmptyState
        title="Could not load this section"
        body={
          result.error !== undefined
            ? result.error
            : "Other sections are unaffected. Retry to fetch this one again."
        }
        action={{ label: "Retry section", onClick: onRetry }}
      />
    );
  } else if (result.status === "unavailable") {
    sectionStatus = "unavailable";
    body = (
      <EmptyState
        title={unavailableTitle ?? "Not available yet"}
        body={
          unavailableBody ??
          (result.error !== undefined
            ? result.error
            : "This section will be enabled in a future release.")
        }
      />
    );
  } else {
    sectionStatus = "ok";
    const data = (result.data ?? []) as ReadonlyArray<T>;
    if (data.length === 0) {
      body = (
        <EmptyState title={emptyTitle} body={emptyBody} action={emptyAction} />
      );
    } else {
      body = renderOk(data);
    }
  }

  return (
    <section
      aria-labelledby={`home-section-${sectionKey}-heading`}
      data-testid={`home-section-${sectionKey}`}
      data-section-status={sectionStatus}
      style={wrapperStyle}
    >
      <h2 id={`home-section-${sectionKey}-heading`} style={headingStyle}>
        {heading}
      </h2>
      {body}
    </section>
  );
}

// ===========================================================================
// SectionPlaceholder — TODO marker for P2-B2/B3 section components
// ===========================================================================

interface SectionPlaceholderProps {
  readonly kind: string;
  readonly testId: string;
  readonly count: number;
}

/**
 * Lightweight render-target for P2-B2 / P2-B3 sections. Each section
 * component will replace this with its own primitive composition
 * (`<ActivityList>`, `<CardGrid>`, `<DocList>`). The placeholder
 * preserves enough information (kind + count) for the shell tests to
 * verify section mounting without depending on B2/B3 internals.
 */
function SectionPlaceholder({
  kind,
  testId,
  count,
}: SectionPlaceholderProps): ReactElement {
  const style: CSSProperties = {
    padding: "12px 14px",
    border: "1px dashed var(--color-border, #232325)",
    borderRadius: "var(--radius-md, 12px)",
    color: "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
  };
  return (
    <div
      style={style}
      data-testid={testId}
      data-section-kind={kind}
      data-section-count={count}
    >
      {`${count} ${kind.replace(/_/g, " ")} entr${count === 1 ? "y" : "ies"} — section component lands in P2-B2/B3`}
    </div>
  );
}

// ===========================================================================
// Loading skeleton — fixed heights to prevent CLS (sub-PRD §10)
// ===========================================================================

const SECTION_SKELETON_HEIGHTS: Readonly<Record<HomeSectionKey, number>> = {
  agent_activity: 320,
  pinned_chats: 200,
  recent_runs: 200,
  favorite_tools: 200,
  todays_focus: 160,
  upcoming_meetings: 160,
  starred_projects: 160,
};

function SectionSkeleton({
  sectionKey,
}: {
  sectionKey: HomeSectionKey;
}): ReactElement {
  const style: CSSProperties = {
    height: SECTION_SKELETON_HEIGHTS[sectionKey],
    borderRadius: "var(--radius-md, 12px)",
    border: "1px solid var(--color-border, #232325)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    opacity: 0.5,
  };
  return (
    <div
      style={style}
      data-testid="home-skeleton-section"
      data-section-key={sectionKey}
      aria-hidden="true"
    />
  );
}

// ===========================================================================
// Greeting copy (sub-PRD §3.1.1 + Q5 deviation)
// ===========================================================================

// Q5 (orchestrator deviation 2026-05-17): IdP `given_name` → first token
// of IdP `name` → no name (no email-local-part fallback). The backend
// composes `user_first_name`; when empty, the shell renders the
// nameless variant.
function formatGreetingTitle(greeting: HomeGreetingT | null): string {
  if (greeting === null) return "Good morning.";
  const slot = timeOfDayLabel(greeting.time_of_day);
  const name = (greeting.user_first_name ?? "").trim();
  if (name.length === 0) return `Good ${slot}.`;
  return `Good ${slot}, ${name}.`;
}

function formatGreetingSubtitle(greeting: HomeGreetingT | null): string {
  if (greeting === null) return "";
  const parts: string[] = [];
  if (greeting.agents_working_count > 0) {
    parts.push(
      `${greeting.agents_working_count} agent${greeting.agents_working_count === 1 ? "" : "s"} working`,
    );
  }
  if (greeting.needs_you_count > 0) {
    parts.push(
      `${greeting.needs_you_count} need${greeting.needs_you_count === 1 ? "s" : ""} you`,
    );
  }
  parts.push(greeting.tenant_local_date);
  return parts.join(" · ");
}

function timeOfDayLabel(slot: HomeGreetingT["time_of_day"]): string {
  switch (slot) {
    case "morning":
      return "morning";
    case "afternoon":
      return "afternoon";
    case "evening":
      return "evening";
    case "late":
      return "evening"; // sub-PRD lumps "late" into evening for the greeting word
  }
}

// ===========================================================================
// Activity merge (live SSE entries + backlog)
// ===========================================================================

/**
 * Prepend a single live SSE entry to the running buffer. De-dup by
 * `id` and cap at 15 (sub-PRD §3.5).
 */
function prependLiveActivity(
  current: ReadonlyArray<AgentActivityEntry>,
  incoming: AgentActivityEntry,
): ReadonlyArray<AgentActivityEntry> {
  const filtered = current.filter((e) => e.id !== incoming.id);
  return [incoming, ...filtered].slice(0, ACTIVITY_FEED_CAP);
}

/**
 * Merge live entries (newest first) with the server backlog, de-duping
 * by `id` and capping at 15.
 */
function mergeActivity(
  payload: HomePayload | null,
  live: ReadonlyArray<AgentActivityEntry>,
): ReadonlyArray<AgentActivityEntry> {
  if (payload === null) return live;
  const backlog =
    payload.agent_activity.status === "ok"
      ? ((payload.agent_activity.data ??
          []) as ReadonlyArray<AgentActivityEntry>)
      : [];
  if (live.length === 0) return backlog;
  const seen = new Set<string>();
  const out: AgentActivityEntry[] = [];
  for (const e of live) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    out.push(e);
    if (out.length >= ACTIVITY_FEED_CAP) return out;
  }
  for (const e of backlog) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    out.push(e);
    if (out.length >= ACTIVITY_FEED_CAP) return out;
  }
  return out;
}

function withReplacedData<T>(
  result: SectionResult<ReadonlyArray<T>>,
  data: ReadonlyArray<T>,
): SectionResult<ReadonlyArray<T>> {
  // Only carry `data` through when the section is "ok" — for error /
  // unavailable, the section renders its CTA from the original result.
  if (result.status !== "ok") return result;
  return { ...result, data };
}
