// Home destination (Phase 2) — morning briefing aggregator contract.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4 (sections
// inventory) and docs/atlas-new-design/cross-audit.md §9.5 (Phase 2
// product decisions — greeting fallback chain, SectionResult wrapping).
//
// Wire shape only. Every section is wrapped in `SectionResult<T>` so the
// frontend can render partial successes (e.g. recent_runs unreachable
// but pinned_chats fresh) without one upstream failure blanking the
// whole page. Stubs land as `{status: "ok", data: []}` until the
// destination services they read from come online; the wire shape is
// stable from day one.
//
// Consumers: apps/frontend Home screen + chat-surface right-rail.

import type {
  ConnectorId,
  ConversationId,
  RunId,
  ToolId,
  TodoId,
} from "./brands";
import type { ItemRef, SectionResult } from "./refs";

/** Time-of-day bucket for the greeting line. Server-derived from the
 * caller's tenant clock so the line matches what they expect (no
 * "Good morning" at 7 PM). */
export type TimeSegment = "morning" | "afternoon" | "evening";

/** Activity-log row kind. Discriminator for downstream rendering; the
 * frontend picks an icon + verb per kind. New kinds are additive — every
 * existing client tolerates an unknown value by falling back to a
 * generic row. */
export type HomeActivityKind =
  | "run"
  | "approval"
  | "chat"
  | "todo"
  | "inbox"
  | "routine_fire"
  | "library_change"
  | "member_action";

/** Run lifecycle status — narrowed mirror of ai-backend's HomeRunStatus
 * enum. Server normalises so the frontend never sees a free-form
 * string. */
export type HomeRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "queued";

/** Greeting payload. `display_name` is `null` only when neither the
 * IdP given_name nor a display_name first-token is available (e.g. a
 * service account); the frontend falls back to "Good morning." in that
 * case. Resolution chain (cross-audit §9.5): IdP given_name →
 * first-token of IdP name → null (FE shows generic). */
export interface HomeGreeting {
  readonly display_name: string | null;
  readonly time_segment: TimeSegment;
}

/** A single activity-log row. `ref` is the canonical ItemRef so
 * <ItemLink> resolves the open path. `summary` is optional one-line
 * detail; `title` is required so the row always has something to
 * display. `occurred_at` is ISO-8601 UTC. */
export interface HomeActivityRow {
  readonly kind: HomeActivityKind;
  readonly ref: ItemRef;
  readonly title: string;
  readonly summary?: string;
  readonly occurred_at: string;
}

/** A pinned chat surface in the right rail / sidebar. */
export interface HomePinnedChat {
  readonly ref: ItemRef & { kind: "chat"; id: ConversationId };
  readonly title: string;
  readonly last_message_at: string;
}

/** A recent run card (link into Runs destination). */
export interface HomeRecentRun {
  readonly ref: ItemRef & { kind: "run"; id: RunId };
  readonly title: string;
  readonly status: HomeRunStatus;
  readonly started_at: string;
}

/** A favourite tool / skill / connector tile. `last_used_at` is
 * optional so a never-used-but-pinned tool can still surface. */
export interface HomeFavoriteTool {
  readonly ref: ItemRef & {
    kind: "tool" | "skill" | "connector";
    id: ToolId | ConnectorId;
  };
  readonly label: string;
  readonly last_used_at?: string;
  readonly use_count: number;
}

/** A "today's focus" item. Composite-scored against todos, approvals,
 * inbox priority, and routine schedule (see scoring.compute_focus_score
 * on the backend). `urgency_score` is monotonically comparable; the FE
 * sorts on it server-blind. */
export interface HomeFocusItem {
  readonly ref: ItemRef & {
    kind: "todo" | "approval" | "inbox_item";
    id: TodoId | string;
  };
  readonly title: string;
  readonly due_at?: string;
  readonly urgency_score: number;
}

/** Calendar-source meeting card. `ref` is optional because external
 * calendar events use a free-form `MeetingExternalId` shape; the FE
 * falls back to `source_connector` + `starts_at` when no canonical
 * link exists. */
export interface HomeUpcomingMeeting {
  readonly ref?: ItemRef;
  readonly title: string;
  readonly starts_at: string;
  readonly source_connector: string;
}

/** Aggregator response. Every section is wrapped in `SectionResult<T>`
 * so a partial outage degrades to "Activity unavailable, retrying"
 * instead of a 500. `greeting` is a flat shape (never expected to
 * fail — derived from session identity + server clock). */
export interface HomeResponse {
  readonly greeting: HomeGreeting;
  readonly activity: SectionResult<HomeActivityRow[]>;
  readonly pinned_chats: SectionResult<HomePinnedChat[]>;
  readonly recent_runs: SectionResult<HomeRecentRun[]>;
  readonly favorite_tools: SectionResult<HomeFavoriteTool[]>;
  readonly todays_focus: SectionResult<HomeFocusItem[]>;
  readonly upcoming_meetings: SectionResult<HomeUpcomingMeeting[]>;
}
