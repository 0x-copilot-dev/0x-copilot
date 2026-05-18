// Home destination (Phase 9) — morning-briefing aggregator contract.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4 (the Phase 9
// HomePayload sections). Reconciled to the canonical cross-destination
// primitives in `./refs.ts` per cross-audit §1.1 (2026-05-17 binding).
//
// Phase 9 redesign supersedes Phase 2's 7-section model. The retired
// Phase 2 types (HomePinnedChat, HomeRecentRun, HomeFavoriteTool,
// HomeFocusItem, HomeUpcomingMeeting, HomeRunStatus, HomeResponse) are
// deleted from this file in the same change. Surviving Phase 2 types
// (HomeGreeting, TimeSegment, HomeActivityRow, HomeActivityKind) are
// kept verbatim — Phase 9 reuses them.
//
// Wire shape only. Every aggregated section is wrapped in
// `SectionResult<T>` (or the sibling `WhatsNewSection`, which carries an
// extra `since_iso` cutoff) so the frontend can render partial successes
// (e.g. timeline unreachable but triage fresh) without one upstream
// failure blanking the whole page.
//
// Consumers: apps/frontend Home destination + chat-surface HomePanel.
// Server composer lives in `services/backend/src/backend_app/home/`.

import type {
  AgentId,
  MeetingExternalId,
  ProjectId,
  RoutineId,
  RunId,
  TodoId,
} from "./brands";
import type { ItemRef, SectionResult } from "./refs";

// ─── Greeting ─────────────────────────────────────────────────────────

/** Time-of-day bucket for the greeting line. Server-derived from the
 * caller's tenant clock so the line matches what they expect (no
 * "Good morning" at 7 PM). Phase 9 keeps the Phase 2 three-value union;
 * a `"late"` extension is additive and may land in a later wave. */
export type TimeSegment = "morning" | "afternoon" | "evening";

/** Greeting payload. `display_name` is `null` only when neither the IdP
 * given_name nor a display_name first-token is available (e.g. a service
 * account); the frontend falls back to "Good morning." in that case.
 * Resolution chain (cross-audit §9.5): IdP given_name → first-token of
 * IdP name → null (FE shows generic).
 *
 * `tenant_local_date` / `tenant_local_iso` (Phase 9 additions) carry the
 * caller's tenant-local clock so the greeting + timeline render against
 * the same wall-clock the user expects, independent of the browser's
 * timezone. */
export interface HomeGreeting {
  readonly display_name: string | null;
  readonly time_segment: TimeSegment;
  /** ISO `YYYY-MM-DD`, in the caller's tenant timezone. */
  readonly tenant_local_date: string;
  /** Full ISO-8601 datetime, in the caller's tenant timezone. */
  readonly tenant_local_iso: string;
}

// ─── Triage strip ─────────────────────────────────────────────────────

/** Morning-briefing triage counts. Four small integers shown above the
 * timeline ("3 approvals waiting · 1 failed run · 2 overdue · 5 due
 * today"). Never wrapped in `SectionResult` — if any upstream fails the
 * composer returns zeros and logs (these are advisory, not load-bearing
 * for the page). */
export interface TriageCounts {
  readonly approvals_waiting: number;
  readonly runs_failed_24h: number;
  readonly todos_overdue: number;
  readonly todos_due_today: number;
}

// ─── Today's timeline (merged calendar / routines / todos / runs) ─────

/** Discriminator for `TimelineEntry`. UI dispatches on `kind` for icon +
 * subtitle copy; the backend pre-composes `subtitle` so localization
 * later wraps one server function, not a UI template maze. */
export type TimelineEntryKind =
  | "meeting"
  | "routine_fire"
  | "todo_due"
  | "run_scheduled";

/** Lifecycle status for a timeline entry. `upcoming` / `in_progress` /
 * `completed` apply to meetings + scheduled runs; `overdue` / `missed`
 * apply to todos + routine fires that did not fire on schedule. */
export type TimelineEntryStatus =
  | "upcoming"
  | "in_progress"
  | "completed"
  | "overdue"
  | "missed";

/** Common fields on every timeline entry. `id` is a per-payload synthetic
 * list key (stable within one HomePayload but not across requests); use
 * `target` for the entity reference. `target` is always an `ItemRef`
 * (cross-audit §1.1) — never a raw entity-id field. */
export interface TimelineEntryBase {
  readonly id: string;
  readonly kind: TimelineEntryKind;
  /** ISO-8601 UTC. When this happens / happened / is due. */
  readonly when_iso: string;
  readonly title: string;
  /** Backend-composed: "Calendar", "Routine fires", "Due", "Run
   * scheduled". Localized server-side. */
  readonly subtitle?: string;
  readonly status: TimelineEntryStatus;
  readonly target: ItemRef;
}

/** Calendar meeting from a connector. `target.kind === "meeting_external"`
 * (canonical for third-party calendar events; see `refs.ts`).
 * `conferencing_url` is carried on the entry (not in `target`) because
 * the join action opens an external URL in a new tab — it is not a
 * cross-destination route resolution. */
export interface MeetingTimelineEntry extends TimelineEntryBase {
  readonly kind: "meeting";
  readonly target: ItemRef & {
    kind: "meeting_external";
    id: MeetingExternalId;
  };
  /** ISO-8601 UTC. End of the meeting block. */
  readonly end_iso: string;
  readonly attendee_count: number;
  readonly is_organizer: boolean;
  /** External URL (Zoom / Meet / Teams). Frontend opens in a new tab. */
  readonly conferencing_url?: string;
  readonly source_connector: "google_calendar" | "microsoft_calendar" | "other";
}

/** Routine scheduled fire window. `target.kind === "routine"`. */
export interface RoutineFireTimelineEntry extends TimelineEntryBase {
  readonly kind: "routine_fire";
  readonly target: ItemRef & { kind: "routine"; id: RoutineId };
  readonly trigger_kind: "scheduled" | "event_driven" | "manual";
}

/** Todo whose due date falls today (or is overdue). `target.kind ===
 * "todo"`. `source_kind` is denormalized provenance metadata (matches
 * the Todos `TodoSource` discriminator); it is display-only — the
 * canonical provenance lives on the Todo row itself. */
export interface TodoDueTimelineEntry extends TimelineEntryBase {
  readonly kind: "todo_due";
  readonly target: ItemRef & { kind: "todo"; id: TodoId };
  readonly priority: "low" | "med" | "high";
  readonly is_overdue: boolean;
  readonly source_kind: "user" | "chat" | "agent";
}

/** Agent run scheduled for today, or already in flight. When the run has
 * not yet started, `target.kind === "agent"` (the agent that will fire
 * it); once started, `target.kind === "run"`. `agent_name` is a display
 * denorm for the subtitle row. */
export interface RunScheduledTimelineEntry extends TimelineEntryBase {
  readonly kind: "run_scheduled";
  readonly target:
    | (ItemRef & { kind: "agent"; id: AgentId })
    | (ItemRef & { kind: "run"; id: RunId });
  readonly agent_name: string;
}

/** Discriminated union over today's timeline entries. */
export type TimelineEntry =
  | MeetingTimelineEntry
  | RoutineFireTimelineEntry
  | TodoDueTimelineEntry
  | RunScheduledTimelineEntry;

// ─── What's new digest + live activity rail ──────────────────────────

/** Activity-log row kind. Phase 2 contract preserved verbatim — Phase 9
 * surfaces (WhatsNewDigest + LiveActivityRail) consume the same row
 * shape (a flat row with `ref: ItemRef + title + summary + kind +
 * occurred_at` already carries everything they render). New kinds are
 * additive; every existing client tolerates an unknown value by falling
 * back to a generic row. */
export type HomeActivityKind =
  | "run"
  | "approval"
  | "chat"
  | "todo"
  | "inbox"
  | "routine_fire"
  | "library_change"
  | "member_action";

/** A single activity-log row. `ref` is the canonical `ItemRef` so
 * `<ItemLink>` resolves the open path. `summary` is optional one-line
 * detail; `title` is required so the row always has something to
 * display. `occurred_at` is ISO-8601 UTC. */
export interface HomeActivityRow {
  readonly kind: HomeActivityKind;
  readonly ref: ItemRef;
  readonly title: string;
  readonly summary?: string;
  readonly occurred_at: string;
}

/** "What's new since you last visited" digest. Mirrors `SectionResult`
 * (status + optional error + data) but adds a `since_iso` cutoff so the
 * UI can label the digest ("Since 7:42 AM") without a second roundtrip.
 *
 * Sibling of `SectionResult<T>` rather than a parameterization because
 * `since_iso` is structural metadata about the digest itself — not
 * a property of any row in `data`. */
export interface WhatsNewSection {
  readonly status: "ok" | "error" | "unavailable";
  /** ISO-8601 UTC. The user's previous Home visit; cutoff for "new". */
  readonly since_iso: string;
  readonly data?: readonly HomeActivityRow[];
  /** Human-readable, frontend-displayable. Never an exception trace. */
  readonly error?: string;
  /** Optional backoff hint when `status === "error"`. */
  readonly retry_after_ms?: number;
}

// ─── In-flight projects strip ────────────────────────────────────────

/** Project summary card for the "currently in flight" strip. `ref`
 * carries the canonical `ProjectId` (cross-audit §1.1 — never a raw
 * `project_id` string). `open_item_count` is a cheap denormalized sum
 * (open chats + open approvals + open todos in the project); the
 * canonical breakdown is fetched on click. */
export interface InFlightProject {
  readonly ref: ItemRef & { kind: "project"; id: ProjectId };
  readonly name: string;
  /** Single-character emoji used as the project avatar. */
  readonly icon_emoji: string;
  /** 0-359; design tokens consume as oklch hue. */
  readonly color_hue: number;
  readonly open_item_count: number;
  /** ISO-8601 UTC. Most-recent activity in the project. */
  readonly last_activity_at: string;
}

// ─── Quick actions ───────────────────────────────────────────────────

/** Discriminator for `QuickActionTarget`. Quick actions express *intent
 * to create* (or to open a destination's onboarding flow) — they are
 * conceptually a different shape from `ItemRef`, which references an
 * *existing* entity. Per cross-audit §1.1, `_new` / "open-creator"
 * variants are NOT added to `ItemRef`; they live in this sibling
 * union. */
export type QuickActionTargetKind =
  | "chat_new"
  | "todo_new"
  | "routine_new"
  | "tools_onboard"
  | "team_invite";

/** Quick-action click target. Discriminated by `kind`; the frontend
 * dispatches on `kind` to either route to a destination's "new" form
 * (`chat_new` → `/chats/new`) or open an onboarding modal
 * (`tools_onboard` → tool-picker modal). */
export type QuickActionTarget =
  | { readonly kind: "chat_new" }
  | { readonly kind: "todo_new" }
  | { readonly kind: "routine_new" }
  | { readonly kind: "tools_onboard" }
  | { readonly kind: "team_invite" };

/** A single quick-action tile. Server-driven so admin role / plan tier /
 * tenant overrides can adjust without a UI deploy. `icon_name` matches
 * the design-system Icon registry (backend allowlist). */
export interface QuickAction {
  readonly id: string;
  readonly label: string;
  readonly icon_name: string;
  readonly target: QuickActionTarget;
  /** When `true`, the server omits this tile for non-admin callers (the
   * field exists only on payloads the admin actually receives — clients
   * never receive a tile they cannot use). */
  readonly is_admin_only?: boolean;
}

// ─── Aggregator response ─────────────────────────────────────────────

/** Phase 9 morning-briefing aggregator response. Returned by
 * `GET /v1/home`. Every aggregated section is wrapped in
 * `SectionResult<T>` (or the sibling `WhatsNewSection`) so a partial
 * outage degrades to "Section unavailable, retrying" instead of a 500.
 *
 * - `greeting` + `triage` + `quick_actions` are flat shapes (never
 *   expected to fail — derived from session identity, simple counts,
 *   and server config).
 * - `today_timeline`, `in_flight_projects`, `live_activity` use
 *   `SectionResult<readonly T[]>` (matches the Phase 2 convention —
 *   array literal as the inner `T`).
 * - `whats_new` uses the sibling `WhatsNewSection` (carries
 *   `since_iso`).
 *
 * `is_first_run` is `true` only when every aggregated section is empty
 * AND the user has no historical data — frontend renders the
 * empty-state onboarding card in that case. */
export interface HomePayload {
  readonly greeting: HomeGreeting;
  readonly triage: TriageCounts;
  readonly today_timeline: SectionResult<readonly TimelineEntry[]>;
  readonly whats_new: WhatsNewSection;
  readonly in_flight_projects: SectionResult<readonly InFlightProject[]>;
  /** Initial backfill for the LiveActivityRail; SSE streams further
   * rows on top. */
  readonly live_activity: SectionResult<readonly HomeActivityRow[]>;
  readonly quick_actions: readonly QuickAction[];
  /** ISO-8601 UTC; mirrors the `X-Atlas-Cached-At` response header. */
  readonly cached_at: string;
  readonly is_first_run: boolean;
}
