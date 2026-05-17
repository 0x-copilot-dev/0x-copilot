// LOCAL STUB — at merge, orchestrator rewires imports to
// "@enterprise-search/api-types".
//
// Field-for-field with `docs/atlas-new-design/destinations/home-prd.md` §4.
// Every import of these types must carry a `// TODO(merge): rewire to
// "@enterprise-search/api-types"` comment so the orchestrator can find
// them mechanically at merge.
//
// Owned-by: P2-A1 (api-types/home.ts) will land first; this stub is a
// scaffold so P2-B1 can develop in parallel without waiting on the
// type-package merge.

import type {
  ConversationId,
  ItemRef,
  RunId,
  SectionResult,
  SkillId,
} from "@enterprise-search/api-types";

// ---- §4.1 HomeGreeting + HomePayload --------------------------------------

export type TimeOfDay = "morning" | "afternoon" | "evening" | "late";

export interface HomeGreeting {
  readonly time_of_day: TimeOfDay;
  readonly user_first_name: string;
  readonly tenant_local_date: string;
  readonly tenant_local_iso: string;
  readonly agents_working_count: number;
  readonly needs_you_count: number;
}

// ---- §4.2 reused sub-types ------------------------------------------------

export interface PinnedChatSummary {
  readonly conversation_id: ConversationId;
  readonly title: string;
  readonly subtitle?: string;
  readonly last_message_at: string;
  readonly unread_message_count: number;
  readonly project_id?: string;
}

export type RecentRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "queued";

export interface RecentRunSummary {
  readonly run_id: RunId;
  readonly title: string;
  readonly status: RecentRunStatus;
  readonly started_at: string;
  readonly completed_at?: string;
  readonly conversation_id?: ConversationId;
}

export interface FavoriteToolSummary {
  readonly skill_id: SkillId;
  readonly name: string;
  readonly subtitle?: string;
  readonly tool_kind: "skill" | "mcp" | "api" | "builtin";
  readonly last_used_at?: string;
}

// ---- §4.3 AgentActivityEntry discriminated union --------------------------

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

// ---- §4.4 TodoSummary -----------------------------------------------------

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

// ---- §4.5 MeetingSummary --------------------------------------------------

export type MeetingConnectorKind =
  | "google_calendar"
  | "microsoft_calendar"
  | "other";

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

// ---- §4.6 StarredProjectSummary + QuickAction -----------------------------

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

// ---- §4.1 HomePayload (top-level) -----------------------------------------

/**
 * Top-level payload returned by `GET /v1/home`. The orchestrator at merge
 * rewires this to `import { HomeResponse } from "@enterprise-search/api-types"`.
 *
 * Aliased as `HomeResponse` in some sub-PRD revisions — both names refer
 * to the same shape; only `HomePayload` is exported here to keep the
 * stub canonical.
 */
export interface HomePayload {
  readonly greeting: HomeGreeting;
  readonly agent_activity: SectionResult<ReadonlyArray<AgentActivityEntry>>;
  readonly pinned_chats: SectionResult<ReadonlyArray<PinnedChatSummary>>;
  readonly recent_runs: SectionResult<ReadonlyArray<RecentRunSummary>>;
  readonly favorite_tools: SectionResult<ReadonlyArray<FavoriteToolSummary>>;
  readonly todays_focus: SectionResult<ReadonlyArray<TodoSummary>>;
  /** `null` = no calendar connector — render the connect-CTA per §3.1.7. */
  readonly upcoming_meetings: SectionResult<
    ReadonlyArray<MeetingSummary>
  > | null;
  readonly starred_projects: SectionResult<
    ReadonlyArray<StarredProjectSummary>
  >;
  readonly quick_actions: ReadonlyArray<QuickAction>;
  readonly cached_at: string;
}

/**
 * Alias used by the P2-B1 task brief. Same shape as `HomePayload`; kept
 * so destination code can import either name during the stub window. The
 * orchestrator collapses to a single name at merge.
 */
export type HomeResponse = HomePayload;
