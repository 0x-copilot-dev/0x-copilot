// Home destination adapters — pure mappers from the api-types wire shape
// to the chat-surface destination props.
//
// One exported function per top-level `HomePayload` field (sub-PRD §4.1).
// Every adapter is total + side-effect-free: same wire shape in, same
// section-prop shape out. The adapters live here (not in the chat-surface
// package) because they are a host-app concern: the chat-surface section
// components define their prop interfaces, and the host decides how to
// pipe the wire payload into them. Keeping the mapping in apps/frontend
// keeps the chat-surface package usable by other host apps (desktop) that
// may compose Home differently.
//
// "Pure passthrough" is the design — the wire shape was designed to match
// the section's render input one-to-one (cross-audit §1.1). If a future
// section needs derived state (e.g. a sort or a grouping), it adds a
// pure function here, not a view-model dialect.

import type {
  HomeActivityRow,
  HomeGreeting,
  HomePayload,
  InFlightProject,
  QuickAction,
  SectionResult,
  TimelineEntry,
  TriageCounts,
  WhatsNewSection,
} from "@0x-copilot/api-types";

import type { HomeStreamEnvelope } from "../../api/homeApi";

// ─── Per-top-level-field adapters ─────────────────────────────────────

/** §3.1.1 HomeGreeting — wire shape already matches the section's render input. */
export function adaptGreeting(payload: HomePayload): HomeGreeting {
  return payload.greeting;
}

/** §3.1.2 TriageStrip — flat counts, never wrapped in `SectionResult`. */
export function adaptTriage(payload: HomePayload): TriageCounts {
  return payload.triage;
}

/** §3.1.3 TodayTimeline — `SectionResult` so the section owns its own
 * loading / error / empty states (sub-PRD §3.1, §14.5). */
export function adaptTodayTimeline(
  payload: HomePayload,
): SectionResult<readonly TimelineEntry[]> {
  return payload.today_timeline;
}

/** §3.1.4 WhatsNewDigest — `WhatsNewSection` (carries `since_iso` cutoff). */
export function adaptWhatsNew(payload: HomePayload): WhatsNewSection {
  return payload.whats_new;
}

/** §3.1.5 InFlightStrip. */
export function adaptInFlightProjects(
  payload: HomePayload,
): SectionResult<readonly InFlightProject[]> {
  return payload.in_flight_projects;
}

/** §3.1.6 LiveActivityRail — initial backfill; SSE prepends more. */
export function adaptLiveActivity(
  payload: HomePayload,
): SectionResult<readonly HomeActivityRow[]> {
  return payload.live_activity;
}

/** §3.2 HomePanel — quick-actions tile list. */
export function adaptQuickActions(
  payload: HomePayload,
): readonly QuickAction[] {
  return payload.quick_actions;
}

// ─── Stream-event reducers ────────────────────────────────────────────
//
// Each reducer consumes one `HomeStreamEnvelope` variant and returns the
// updated `HomePayload` (or the original reference when the event is a
// no-op, so React skips the re-render). Reducers are pure — they never
// mutate the input, never touch the network, never read clocks.

/** Cap for the LiveActivityRail (sub-PRD §3.1.6). */
const ACTIVITY_RAIL_CAP = 15;
/** Cap for WhatsNewDigest (sub-PRD §3.1.4). */
const WHATS_NEW_CAP = 7;
/** Cap for TodayTimeline (sub-PRD §3.1.3). */
const TIMELINE_CAP = 8;
/** Cap for InFlightStrip (sub-PRD §3.1.5). */
const IN_FLIGHT_CAP = 3;

/** Apply one Home SSE envelope to the cached payload. */
export function applyHomeStreamEvent(
  payload: HomePayload,
  envelope: HomeStreamEnvelope,
): HomePayload {
  switch (envelope.type) {
    case "home.heartbeat":
      return payload;
    case "home.triage_updated":
      return applyTriageUpdated(payload, envelope.triage);
    case "home.timeline_appended":
      return applyTimelineAppended(payload, envelope.entry);
    case "home.whats_new_appended":
      return applyWhatsNewAppended(payload, envelope.row);
    case "home.activity_appended":
      return applyActivityAppended(payload, envelope.row);
    case "home.in_flight_updated":
      return applyInFlightUpdated(payload, envelope.project);
  }
}

function applyTriageUpdated(
  payload: HomePayload,
  triage: TriageCounts,
): HomePayload {
  if (
    payload.triage.approvals_waiting === triage.approvals_waiting &&
    payload.triage.runs_failed_24h === triage.runs_failed_24h &&
    payload.triage.todos_overdue === triage.todos_overdue &&
    payload.triage.todos_due_today === triage.todos_due_today
  ) {
    return payload;
  }
  return { ...payload, triage };
}

function applyTimelineAppended(
  payload: HomePayload,
  entry: TimelineEntry,
): HomePayload {
  if (payload.today_timeline.status !== "ok") {
    return payload;
  }
  const existing = payload.today_timeline.data ?? [];
  if (existing.some((e) => e.id === entry.id)) {
    return payload;
  }
  // Timeline is chronological ascending; backend may push entries in any
  // order, so we resort on append. Cap to 8 (sub-PRD §3.1.3).
  const merged = [...existing, entry]
    .slice()
    .sort((a, b) => a.when_iso.localeCompare(b.when_iso))
    .slice(0, TIMELINE_CAP);
  return {
    ...payload,
    today_timeline: { ...payload.today_timeline, data: merged },
  };
}

function applyWhatsNewAppended(
  payload: HomePayload,
  row: HomeActivityRow,
): HomePayload {
  if (payload.whats_new.status !== "ok") {
    return payload;
  }
  const existing = payload.whats_new.data ?? [];
  if (isActivityDuplicate(existing, row)) {
    return payload;
  }
  const next = [row, ...existing].slice(0, WHATS_NEW_CAP);
  return { ...payload, whats_new: { ...payload.whats_new, data: next } };
}

function applyActivityAppended(
  payload: HomePayload,
  row: HomeActivityRow,
): HomePayload {
  if (payload.live_activity.status !== "ok") {
    return payload;
  }
  const existing = payload.live_activity.data ?? [];
  if (isActivityDuplicate(existing, row)) {
    return payload;
  }
  const next = [row, ...existing].slice(0, ACTIVITY_RAIL_CAP);
  return {
    ...payload,
    live_activity: { ...payload.live_activity, data: next },
  };
}

function applyInFlightUpdated(
  payload: HomePayload,
  project: InFlightProject,
): HomePayload {
  if (payload.in_flight_projects.status !== "ok") {
    return payload;
  }
  const existing = payload.in_flight_projects.data ?? [];
  const idx = existing.findIndex((p) => p.ref.id === project.ref.id);
  let next: readonly InFlightProject[];
  if (idx >= 0) {
    const copy = existing.slice();
    copy[idx] = project;
    next = copy;
  } else {
    next = [project, ...existing];
  }
  next = next
    .slice()
    .sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))
    .slice(0, IN_FLIGHT_CAP);
  return {
    ...payload,
    in_flight_projects: { ...payload.in_flight_projects, data: next },
  };
}

/**
 * `HomeActivityRow` does not carry an explicit id; dedupe by the `ItemRef`
 * (kind + id) + `occurred_at` triple. Sub-PRD §4.3 — the SSE channel may
 * replay rows after reconnect, and the rail must not show the same row
 * twice.
 */
function isActivityDuplicate(
  existing: readonly HomeActivityRow[],
  row: HomeActivityRow,
): boolean {
  return existing.some(
    (e) =>
      e.ref.kind === row.ref.kind &&
      e.ref.id === row.ref.id &&
      e.occurred_at === row.occurred_at,
  );
}
