// chat-surface Home adapter shape (transitional; kept post-Phase-2 merge).
//
// Three Phase-2 agents (P2-B1 destination shell, P2-B2 sections A, P2-B3
// sections B) developed in parallel against slightly different shape
// conventions. The canonical backend wire (from P2-A1's `packages/api-types/
// src/home.ts`) is LEANER than the rich UI shape chat-surface consumes.
// Apps/frontend (P2-C's `HomeRoute`) adapts the lean wire to this rich
// shape at the host boundary.
//
// This file exports BOTH naming conventions (P2-B1's rich union vocabulary
// AND P2-B2/B3's simpler row vocabulary) as aliases so every consumer
// compiles without code rewrites. Wave 3+ may refactor chat-surface to
// consume `@enterprise-search/api-types` directly and delete this file.

import type {
  ConversationId,
  ItemRef,
  RunId,
  SectionResult,
  SkillId,
} from "@enterprise-search/api-types";

// ---- §4.1 Greeting --------------------------------------------------------

/** Server-computed against tenant timezone (home-prd §4.1). */
export type HomeTimeOfDay = "morning" | "afternoon" | "evening" | "late";
/** Alias used by P2-B1's shell code. */
export type TimeOfDay = HomeTimeOfDay;
/** Alias used by some test fixtures + the api-types canonical. */
export type TimeSegment = "morning" | "afternoon" | "evening";

export interface HomeGreeting {
  readonly time_of_day: HomeTimeOfDay;
  readonly user_first_name?: string;
  readonly tenant_local_date: string;
  readonly tenant_local_iso: string;
  readonly agents_working_count: number;
  readonly needs_you_count: number;
}

// ---- §4.2 Pinned chats ----------------------------------------------------

export interface PinnedChatSummary {
  readonly conversation_id: ConversationId;
  readonly title: string;
  readonly subtitle?: string;
  readonly last_message_at: string;
  readonly unread_message_count: number;
  readonly project_id?: string;
}
/** Alias used by P2-B2's PinnedChats section. */
export type HomePinnedChat = PinnedChatSummary;

// ---- §4.2 Recent runs -----------------------------------------------------

export type HomeRecentRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "queued";
/** Alias used by P2-B1's shell code. */
export type RecentRunStatus = HomeRecentRunStatus;
/** Alias used by some api-types-style imports. */
export type HomeRunStatus = HomeRecentRunStatus;

export interface RecentRunSummary {
  readonly run_id: RunId;
  readonly title: string;
  readonly status: HomeRecentRunStatus;
  readonly started_at: string;
  readonly completed_at?: string;
  readonly conversation_id?: ConversationId;
}
/** Alias used by P2-B3's RecentRuns section. */
export type HomeRecentRun = RecentRunSummary;

// ---- §4.2 Favorite tools --------------------------------------------------

export interface FavoriteToolSummary {
  readonly skill_id: SkillId;
  readonly name: string;
  readonly subtitle?: string;
  readonly tool_kind: "skill" | "mcp" | "api" | "builtin";
  readonly last_used_at?: string;
  /** P2-B3 uses use_count for sort + label pluralization. */
  readonly use_count?: number;
}
/** Alias used by P2-B3's FavoriteTools section. */
export type HomeFavoriteTool = FavoriteToolSummary;

// ---- §4.3 Agent activity (rich discriminated union — P2-B1's convention) --

export type AgentActivityKind =
  | "drafted_artifact"
  | "sent_message"
  | "queued_approval"
  | "risk_signal"
  | "completed_run"
  | "failed_run"
  | "extracted_todos"
  | "ingested_dataset";

export interface AgentActivityEntryBase {
  readonly id: string;
  readonly kind: AgentActivityKind;
  readonly agent_id: string;
  readonly agent_name: string;
  readonly summary: string;
  readonly created_at: string;
  readonly target: ItemRef;
  readonly tone: "neutral" | "positive" | "warning" | "alert";
}

export interface DraftedArtifactActivity extends AgentActivityEntryBase {
  readonly kind: "drafted_artifact";
  readonly artifact_kind:
    | "email"
    | "doc"
    | "sheet"
    | "slide"
    | "page"
    | "brief";
  readonly artifact_title: string;
  readonly word_count?: number;
}

export interface SentMessageActivity extends AgentActivityEntryBase {
  readonly kind: "sent_message";
  readonly surface: "email" | "slack" | "comment" | "salesforce" | "other";
  readonly recipient_display: string;
}

export interface QueuedApprovalActivity extends AgentActivityEntryBase {
  readonly kind: "queued_approval";
  readonly inbox_item_id: string;
  readonly priority: "low" | "med" | "high";
  readonly needs: "approval" | "decision" | "review";
}

export interface RiskSignalActivity extends AgentActivityEntryBase {
  readonly kind: "risk_signal";
  readonly severity: "low" | "med" | "high";
  readonly signal_summary: string;
}

export interface CompletedRunActivity extends AgentActivityEntryBase {
  readonly kind: "completed_run";
  readonly run_id: RunId;
  readonly duration_ms: number;
  readonly artifacts_produced: number;
}

export interface FailedRunActivity extends AgentActivityEntryBase {
  readonly kind: "failed_run";
  readonly run_id: RunId;
  readonly failure_reason_code: string;
  readonly failure_reason_message: string;
  readonly recoverable: boolean;
}

export interface ExtractedTodosActivity extends AgentActivityEntryBase {
  readonly kind: "extracted_todos";
  readonly todos_filed: number;
  readonly assigned_to_you: number;
}

export interface IngestedDatasetActivity extends AgentActivityEntryBase {
  readonly kind: "ingested_dataset";
  readonly dataset_id: string;
  readonly row_count: number;
}

export type AgentActivityEntry =
  | DraftedArtifactActivity
  | SentMessageActivity
  | QueuedApprovalActivity
  | RiskSignalActivity
  | CompletedRunActivity
  | FailedRunActivity
  | ExtractedTodosActivity
  | IngestedDatasetActivity;

/** Alias used by P2-B2's ActivityFeed section. Same shape as
 *  AgentActivityEntryBase (the union's common fields). */
export type HomeActivityRow = AgentActivityEntryBase;
/** P2-B3 imports HomeActivityKind for kind-mapping tables. */
export type HomeActivityKind = AgentActivityKind;

// ---- §4.4 Todos -----------------------------------------------------------

export type TodoSourceKind = "user" | "chat" | "agent";
export type TodoPriority = "low" | "med" | "high";

export interface TodoSummary {
  readonly todo_id: string;
  readonly text: string;
  readonly priority: TodoPriority;
  readonly due_iso?: string;
  readonly is_overdue: boolean;
  readonly source_kind: TodoSourceKind;
  readonly source_label?: string;
  readonly project_id?: string;
}
// P2-B3's TodaysFocus uses a richer focus-item shape (kind + urgency_score
// + due_at as ISO instant). Distinct from TodoSummary which is the
// destination-shell's view (text + priority + due_iso). Both ship.
export type HomeFocusKind = "todo" | "approval" | "review";
export type HomeFocusPriority = "low" | "med" | "high";
export interface HomeFocusItem {
  readonly todo_id: string;
  readonly title: string;
  readonly kind: HomeFocusKind;
  readonly priority?: HomeFocusPriority;
  readonly due_at?: string;
  readonly urgency_score: number;
  readonly is_overdue?: boolean;
  readonly source_label?: string;
  readonly project_id?: string;
}

// ---- §4.5 Meetings --------------------------------------------------------

export type MeetingConnectorKind =
  | "google_calendar"
  | "microsoft_calendar"
  | "other";
/** Alias used by P2-B3. */
export type HomeMeetingConnectorKind = MeetingConnectorKind;

export interface MeetingSummary {
  readonly meeting_id: string;
  readonly title: string;
  readonly start_iso: string;
  readonly end_iso: string;
  readonly attendee_count: number;
  readonly is_organizer: boolean;
  readonly conferencing_url?: string;
  readonly source_connector: MeetingConnectorKind;
}

// P2-B3's UpcomingMeetings uses `starts_at` ISO instant directly.
// Distinct from MeetingSummary which has start_iso/end_iso pair.
export interface HomeUpcomingMeeting {
  readonly meeting_id?: string;
  readonly title: string;
  readonly starts_at: string;
  readonly ends_at?: string;
  readonly source_connector: MeetingConnectorKind;
  readonly conferencing_url?: string;
  readonly attendee_count?: number;
  readonly is_organizer?: boolean;
}

/** Sentinel error code for UpcomingMeetings unavailable-due-to-no-calendar
 *  state. Used by P2-B3 + P2-A1 backend composer. */
export const HOME_MEETINGS_NO_CONNECTOR = "no_calendar_connector";

// ---- §4.6 Starred projects + quick actions --------------------------------

export interface StarredProjectSummary {
  readonly project_id: string;
  readonly name: string;
  readonly icon_emoji: string;
  readonly color_hue: number;
  readonly active_thread_count: number;
  readonly last_activity_at: string;
}

export interface QuickAction {
  readonly id: string;
  readonly label: string;
  readonly icon_name: string;
  readonly target: ItemRef;
  readonly is_admin_only?: boolean;
}

// ---- §4.1 Top-level payload ----------------------------------------------

export interface HomePayload {
  readonly greeting: HomeGreeting;
  readonly agent_activity: SectionResult<ReadonlyArray<AgentActivityEntry>>;
  readonly pinned_chats: SectionResult<ReadonlyArray<PinnedChatSummary>>;
  readonly recent_runs: SectionResult<ReadonlyArray<RecentRunSummary>>;
  readonly favorite_tools: SectionResult<ReadonlyArray<FavoriteToolSummary>>;
  readonly todays_focus: SectionResult<ReadonlyArray<TodoSummary>>;
  readonly upcoming_meetings: SectionResult<
    ReadonlyArray<MeetingSummary>
  > | null;
  readonly starred_projects: SectionResult<
    ReadonlyArray<StarredProjectSummary>
  >;
  readonly quick_actions: ReadonlyArray<QuickAction>;
  readonly cached_at: string;
}

export type HomeResponse = HomePayload;
