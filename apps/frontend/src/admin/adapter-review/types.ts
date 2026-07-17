// Frontend types for the tier-2 adapter review pipeline (Phase 7C).
//
// These mirror 7A's wire contract (see
// docs/plan/desktop/phase-7/7C-tier2-review-pipeline.md §2). They are
// re-declared in the FE rather than imported from @0x-copilot/
// api-types because 7A hasn't merged its types-package contribution yet;
// when it does, swap the local declarations for the package import in a
// single follow-up PR.

export type LayoutTemplate = "form" | "table" | "kanban" | "definition-list";

export type CandidateStatus =
  | "submitted"
  | "in-review"
  | "changes-requested"
  | "approved"
  | "rejected";

export type DecisionAction = "approve" | "reject" | "request-changes";

export interface AdapterReviewCandidateSummary {
  readonly candidate_id: string;
  readonly scheme: string;
  readonly layout_template: LayoutTemplate;
  readonly origin_tenant_redacted: string;
  readonly generator_model: string;
  readonly submitted_at: string;
  readonly status: CandidateStatus;
  readonly session_count: number;
}

export interface AdapterReviewDecisionRecord {
  readonly decided_at: string;
  readonly decided_by_user_id: string;
  readonly action: DecisionAction;
  readonly notes: string;
}

export interface AdapterReviewCandidateDetail {
  readonly candidate_id: string;
  readonly scheme: string;
  readonly layout_template: LayoutTemplate;
  readonly origin_tenant_redacted: string;
  readonly generator_model: string;
  readonly submitted_at: string;
  readonly status: CandidateStatus;
  readonly candidate_source: string;
  readonly schema_version: number;
  readonly history: readonly AdapterReviewDecisionRecord[];
}

export interface AdapterReviewCandidatesResponse {
  readonly candidates: readonly AdapterReviewCandidateSummary[];
  readonly next_cursor: string | null;
  readonly has_more: boolean;
}

export interface AdapterReviewListFilters {
  readonly status?: CandidateStatus;
  readonly layout?: LayoutTemplate;
  readonly scheme?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export interface AdapterReviewDecisionRequest {
  readonly action: DecisionAction;
  readonly notes: string;
}

export interface AdapterReviewDecisionResponse {
  readonly candidate_id: string;
  readonly status: CandidateStatus;
  readonly decided_at: string;
  readonly decided_by_user_id: string;
  readonly action: DecisionAction;
  readonly notes: string;
}

export const LAYOUT_TEMPLATES: readonly LayoutTemplate[] = [
  "form",
  "table",
  "kanban",
  "definition-list",
];

export const CANDIDATE_STATUSES: readonly CandidateStatus[] = [
  "submitted",
  "in-review",
  "changes-requested",
  "approved",
  "rejected",
];
