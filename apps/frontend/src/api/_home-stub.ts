// Local stub for the Phase 2 Home wire contract.
//
// The canonical types live in `@enterprise-search/api-types`
// (`packages/api-types/src/home.ts`), authored by the parallel P2-A1
// backend-types agent. This frontend wave (P2-C) runs in parallel and
// cannot import a type that is not yet on `main`, so this stub mirrors
// the sub-PRD §4 shape. The orchestrator rewires every `_home-stub`
// import to `@enterprise-search/api-types` at merge time.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §4.1 (`HomePayload`)
// + §4.2-4.6 (sub-types). The `ItemRef` and `SectionResult` types already
// live in api-types (`packages/api-types/src/refs.ts`) so we re-export from
// there to avoid drift on the cross-destination primitives — only the
// Home-specific types live in this stub.
//
// TODO(merge): delete this file. Replace every `_home-stub` import with
// `@enterprise-search/api-types`.

import type {
  ConversationId,
  ItemRef,
  RunId,
  SectionResult,
  SkillId,
} from "@enterprise-search/api-types";

export type TimeOfDay = "morning" | "afternoon" | "evening" | "late";

export interface HomeGreeting {
  readonly time_of_day: TimeOfDay;
  readonly user_first_name: string | null;
  readonly tenant_local_date: string;
  readonly tenant_local_iso: string;
  readonly agents_working_count: number;
  readonly needs_you_count: number;
}

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

export type AgentActivityKind =
  | "drafted_artifact"
  | "sent_message"
  | "queued_approval"
  | "risk_signal"
  | "completed_run"
  | "failed_run"
  | "extracted_todos"
  | "ingested_dataset";

export interface AgentActivityEntry {
  readonly id: string;
  readonly kind: AgentActivityKind;
  readonly agent_id: string;
  readonly agent_name: string;
  readonly summary: string;
  readonly created_at: string;
  readonly target: ItemRef;
  readonly tone: "neutral" | "positive" | "warning" | "alert";
}

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

/**
 * Aggregate response shape served by `GET /v1/home`. The activity-feed
 * SSE stream emits `AgentActivityEntry` events; the rest of the surface
 * is fully delivered in this single payload.
 */
export interface HomeResponse {
  readonly greeting: HomeGreeting;
  readonly agent_activity: SectionResult<readonly AgentActivityEntry[]>;
  readonly pinned_chats: SectionResult<readonly PinnedChatSummary[]>;
  readonly recent_runs: SectionResult<readonly RecentRunSummary[]>;
  readonly favorite_tools: SectionResult<readonly FavoriteToolSummary[]>;
  readonly todays_focus: SectionResult<readonly TodoSummary[]>;
  readonly upcoming_meetings: SectionResult<readonly MeetingSummary[]> | null;
  readonly starred_projects: SectionResult<readonly StarredProjectSummary[]>;
  readonly quick_actions: readonly QuickAction[];
  readonly cached_at: string;
}
