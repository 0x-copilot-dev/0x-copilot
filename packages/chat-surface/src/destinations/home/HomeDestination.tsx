// Home destination — Phase 9 rewrite.
//
// Pure-presentation morning-briefing shell. Composes the six Phase 9
// sections (per sub-PRD §3.1) in fixed order:
//
//   <HomeGreeting />        — §3.1.1
//   <TriageStrip />         — §3.1.2 (always above the fold)
//   <TodayTimeline />       — §3.1.3 (collapses if empty)
//   <WhatsNewDigest />      — §3.1.4 (collapses if empty)
//   <InFlightStrip />       — §3.1.5 (collapses if empty)
//   <LiveActivityRail />    — §3.1.6 (subordinate right-rail at ≥1024px;
//                              bottom strip below)
//
// No transport, no router, no fetch. The data binder (P9-C) owns
// fetching `/v1/home`, opening `/v1/home/stream`, merging SSE rows,
// and reconciling with the cached payload. This component renders
// whatever `homeResponse` the host hands it.

import type { CSSProperties, ReactElement } from "react";

import type {
  HomeActivityRow,
  HomePayload,
  ItemRef,
} from "@0x-copilot/api-types";

import { EmptyState } from "../../shell/EmptyState";

import {
  HomeGreeting,
  InFlightStrip,
  LiveActivityRail,
  TodayTimeline,
  TriageStrip,
  WhatsNewDigest,
} from "./sections";

export interface HomeDestinationProps {
  /**
   * Server-resolved morning-briefing payload. When `null`, the
   * destination renders a quiet loading skeleton. The data binder
   * (P9-C) supplies the payload; this component is pure-presentation.
   */
  readonly homeResponse?: HomePayload | null;

  /**
   * SSE-merged live activity rows for the right rail. Optional — when
   * omitted, the rail falls back to the payload's `live_activity.data`.
   * The data binder merges incoming `home_activity` SSE rows on top
   * and hands the merged buffer down.
   */
  readonly liveActivity?: ReadonlyArray<HomeActivityRow>;

  /**
   * Host-supplied router shim for the triage tiles. Receives the
   * `ItemRef` for the tile that was clicked; the host resolves to a
   * filtered destination view.
   */
  readonly onTriageSelect?: (ref: ItemRef) => void;

  /** Frozen `now` for tests; defaults to `Date.now()` at render. */
  readonly nowMs?: number;
}

const rootStyle: CSSProperties = {
  width: "100%",
  height: "100%",
  minHeight: 0,
  backgroundColor: "var(--color-bg)",
  color: "var(--color-text)",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "row",
  overflow: "hidden",
};

const mainStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: "auto",
  padding: "24px 28px 48px",
  boxSizing: "border-box",
  display: "flex",
  flexDirection: "column",
  gap: 24,
};

const railStyle: CSSProperties = {
  width: 240,
  flexShrink: 0,
  borderLeft: "1px solid var(--color-border)",
  padding: "24px 16px",
  overflow: "auto",
  boxSizing: "border-box",
};

const skeletonStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 24,
  padding: "24px 28px",
  width: "100%",
  height: "100%",
  boxSizing: "border-box",
};

const skeletonBlockStyle = (height: number): CSSProperties => ({
  height,
  borderRadius: "var(--radius-md, 12px)",
  border: "1px solid var(--color-border)",
  backgroundColor: "var(--color-surface-muted)",
  opacity: 0.5,
});

export function HomeDestination(
  props: HomeDestinationProps = {},
): ReactElement {
  const { homeResponse = null, liveActivity, onTriageSelect, nowMs } = props;

  // === Loading state ====================================================
  if (homeResponse === null) {
    return (
      <section
        aria-label="Home destination"
        data-testid="home-destination"
        data-state="loading"
        style={rootStyle}
      >
        <div style={skeletonStyle} aria-hidden="true">
          {[120, 80, 240, 200].map((h, i) => (
            <div
              key={i}
              style={skeletonBlockStyle(h)}
              data-testid="home-skeleton-section"
            />
          ))}
        </div>
      </section>
    );
  }

  // === First-run welcome ================================================
  if (homeResponse.is_first_run) {
    return (
      <section
        aria-label="Home destination"
        data-testid="home-destination"
        data-state="first-run"
        style={rootStyle}
      >
        <div style={mainStyle}>
          <HomeGreeting greeting={homeResponse.greeting} />
          <EmptyState
            title="Welcome to Copilot."
            body="As your agents work, today's plan and what's new will fill in here."
          />
        </div>
      </section>
    );
  }

  // === Ready state ======================================================
  const timeline =
    homeResponse.today_timeline.status === "ok"
      ? (homeResponse.today_timeline.data ?? [])
      : [];
  const projects =
    homeResponse.in_flight_projects.status === "ok"
      ? (homeResponse.in_flight_projects.data ?? [])
      : [];
  const rail =
    liveActivity ??
    (homeResponse.live_activity.status === "ok"
      ? (homeResponse.live_activity.data ?? [])
      : []);

  return (
    <section
      aria-label="Home destination"
      data-testid="home-destination"
      data-state="ready"
      data-cached-at={homeResponse.cached_at}
      style={rootStyle}
    >
      <div style={mainStyle} data-testid="home-main-column">
        <HomeGreeting greeting={homeResponse.greeting} />
        <TriageStrip counts={homeResponse.triage} onSelect={onTriageSelect} />
        <TodayTimeline entries={timeline} />
        <WhatsNewDigest section={homeResponse.whats_new} nowMs={nowMs} />
        <InFlightStrip projects={projects} nowMs={nowMs} />
      </div>
      <div style={railStyle} data-testid="home-side-rail">
        <LiveActivityRail rows={rail} nowMs={nowMs} />
      </div>
    </section>
  );
}
