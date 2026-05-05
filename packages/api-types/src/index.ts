export type McpTransport = "http" | "sse" | "stdio";
export type McpAuthMode = "none" | "oauth2" | "api_key" | "service_account";
export type McpAuthState =
  | "unauthenticated"
  | "auth_skipped"
  | "auth_pending"
  | "authenticated"
  | "auth_failed"
  | "auth_unsupported";
export type McpServerHealth =
  | "healthy"
  | "degraded"
  | "unavailable"
  | "disabled";

export interface McpServer {
  server_id: string;
  name: string;
  display_name: string;
  url: string;
  transport: McpTransport;
  auth_mode: McpAuthMode;
  auth_state: McpAuthState;
  health: McpServerHealth;
  enabled: boolean;
  oauth_client_configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface McpOAuthClientConfigRequest {
  client_id: string;
  client_secret?: string;
  token_endpoint_auth_method?: "none" | "client_secret_post" | string;
  scope?: string;
  authorization_endpoint?: string;
  token_endpoint?: string;
}

export interface CreateMcpServerRequest {
  org_id: string;
  user_id: string;
  url: string;
  display_name?: string;
  transport?: McpTransport;
  auth_mode?: McpAuthMode;
  oauth_client?: McpOAuthClientConfigRequest;
}

export interface UpdateMcpServerRequest {
  display_name?: string;
  enabled?: boolean;
  oauth_client?: McpOAuthClientConfigRequest | null;
}

export interface McpServerListResponse {
  servers: McpServer[];
}

export interface McpAuthStartResponse {
  server_id: string;
  auth_url: string;
  expires_at: string;
}

export interface McpAuthRequiredEventPayload {
  approval_id?: string;
  action_id?: string;
  approval_kind?: "mcp_auth" | string;
  server_id: string;
  server_name: string;
  display_name: string;
  auth_url: string;
  expires_at: string;
  message: string;
  source_tool_call_id?: string;
}

export type ConversationStatus = "active" | "archived";
export type MessageRole = "user" | "assistant" | "tool" | "system";
export type MessageStatus = "created" | "deleted";
export type AgentRunStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed"
  | "timed_out";
export const AGENT_RUN_STATUSES = [
  "queued",
  "running",
  "waiting_for_approval",
  "cancelling",
  "cancelled",
  "completed",
  "failed",
  "timed_out",
] as const satisfies readonly AgentRunStatus[];

export type RuntimeEventVisibility = "user" | "internal" | "audit";
export type RuntimeEventRedactionState = "redacted" | "truncated" | "offloaded";
export type RuntimeActivityKind =
  | "run"
  | "message"
  | "tool"
  | "subagent"
  | "reasoning"
  | "mcp_auth"
  | "approval"
  | "heartbeat"
  | "event"
  | "draft";
export type RuntimeEventSource =
  | "main_agent"
  | "runtime"
  | "model"
  | "tool"
  | "mcp"
  | "subagent"
  | "summarization"
  | "system";
export type RuntimeApiEventType =
  | "run_queued"
  | "run_started"
  | "run_cancelling"
  | "run_cancelled"
  | "run_completed"
  | "run_failed"
  | "progress"
  | "reasoning_summary"
  | "reasoning_summary_delta"
  | "tool_call"
  | "tool_call_started"
  | "tool_call_delta"
  | "tool_result"
  | "tool_call_completed"
  | "mcp_auth_required"
  | "subagent_update"
  | "subagent_started"
  | "subagent_progress"
  | "subagent_completed"
  | "approval_requested"
  | "approval_resolved"
  | "observation"
  | "error"
  | "model_call_started"
  | "model_call_completed"
  | "model_delta"
  | "final_response"
  | "heartbeat"
  | "presentation_updated"
  | "budget_warning"
  | "run_rejected"
  | "draft_updated";

export const RUNTIME_EVENT_SOURCES = [
  "main_agent",
  "runtime",
  "model",
  "tool",
  "mcp",
  "subagent",
  "summarization",
  "system",
] as const satisfies readonly RuntimeEventSource[];

export const RUNTIME_API_EVENT_TYPES = [
  "run_queued",
  "run_started",
  "run_cancelling",
  "run_cancelled",
  "run_completed",
  "run_failed",
  "progress",
  "reasoning_summary",
  "reasoning_summary_delta",
  "tool_call",
  "tool_call_started",
  "tool_call_delta",
  "tool_result",
  "tool_call_completed",
  "mcp_auth_required",
  "subagent_update",
  "subagent_started",
  "subagent_progress",
  "subagent_completed",
  "approval_requested",
  "approval_resolved",
  "observation",
  "error",
  "model_call_started",
  "model_call_completed",
  "model_delta",
  "final_response",
  "heartbeat",
  "presentation_updated",
  "budget_warning",
  "run_rejected",
  "draft_updated",
] as const satisfies readonly RuntimeApiEventType[];

export const RUNTIME_ACTIVITY_KINDS = [
  "run",
  "message",
  "tool",
  "subagent",
  "reasoning",
  "mcp_auth",
  "approval",
  "heartbeat",
  "event",
  "draft",
] as const satisfies readonly RuntimeActivityKind[];

export type ApprovalDecision = "approved" | "rejected";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface SessionIdentity {
  org_id: string;
  user_id: string;
  roles: string[];
  permission_scopes: string[];
}

export interface SessionResponse {
  identity: SessionIdentity;
}

export interface CreateConversationRequest {
  org_id: string;
  user_id: string;
  assistant_id?: string;
  title?: string | null;
  metadata?: Record<string, unknown>;
  idempotency_key?: string | null;
}

export interface Conversation {
  conversation_id: string;
  org_id: string;
  user_id: string;
  assistant_id: string;
  title: string | null;
  status: ConversationStatus;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  metadata: Record<string, unknown>;
  schema_version: number;
}

export interface ConversationListResponse {
  conversations: Conversation[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface Message {
  message_id: string;
  conversation_id: string;
  org_id: string;
  run_id: string | null;
  role: MessageRole;
  content_text: string;
  content_format: string;
  content?: RunContentPart[];
  attachments?: RunAttachmentRequest[];
  quote?: RunQuoteMetadata | null;
  metadata?: Record<string, unknown>;
  parent_message_id: string | null;
  source_message_id?: string | null;
  branch_id?: string | null;
  token_count: number | null;
  trace_id: string | null;
  status: MessageStatus;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
}

export interface AssistantPerformanceMetrics {
  started_at: string;
  completed_at: string;
  duration_ms: number;
  chunk_count: number;
  first_chunk_at?: string;
  first_chunk_ms?: number;
  usage?: {
    input?: number;
    output?: number;
    total?: number;
    cached_input?: number;
    output_per_second?: number;
  };
}

export interface AssistantSubagentUsageRollup {
  input: number;
  output: number;
  cached_input: number;
  total: number;
  call_count: number;
}

// ------------------------------------------------------------------
// Usage endpoints (B4)
//
// All cost figures are micro-USD integers (1 USD = 1_000_000 micro_usd)
// — never floats — and are `null` when the model has no priced row in
// the server-side pricing catalog. Currency code is always "USD" today;
// it is returned alongside so consumers don't have to hard-code it.
// ------------------------------------------------------------------

export type UsagePeriod = "today" | "7d" | "30d" | "month";

export interface UsageTotals {
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsageDailyRow {
  day: string;
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsageModelRow {
  provider: string;
  model: string;
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsageConversationRow {
  conversation_id: string;
  title: string | null;
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsagePeriodWindow {
  start: string;
  end: string;
}

export interface UsageMeResponse {
  period: UsagePeriodWindow;
  currency: "USD";
  total: UsageTotals;
  by_day: UsageDailyRow[];
  by_model: UsageModelRow[];
  cold_start_fallback: boolean;
}

export interface UsageOrgResponse {
  period: UsagePeriodWindow;
  currency: "USD";
  total: UsageTotals;
  by_day: UsageDailyRow[];
  by_model: UsageModelRow[];
  by_user: UsageConversationRow[];
  cold_start_fallback: boolean;
}

export interface RunUsageCallRow {
  id: string;
  parent_event_id: string | null;
  task_id: string | null;
  subagent_id: string | null;
  model_provider: string;
  model_name: string;
  input: number;
  output: number;
  cached_input: number;
  total: number;
  duration_ms: number;
  cost_micro_usd: number | null;
  created_at: string;
}

export interface RunUsageBreakdown {
  run_id: string;
  org_id: string;
  user_id: string;
  conversation_id: string;
  model_provider: string;
  model_name: string;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  chunk_count: number;
  status: string;
  total: UsageTotals;
  by_call: RunUsageCallRow[];
}

export interface UsageRunRow {
  run_id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  total: UsageTotals;
}

export interface ConversationUsageResponse {
  conversation_id: string;
  period: UsagePeriodWindow;
  currency: "USD";
  total: UsageTotals;
  by_run: UsageRunRow[];
}

// ---------------------------------------------------------------------------
// Conversation context view (B5 — `/context` slash command).
//
// Server-computed view of "where did the tokens go in this conversation".
// `headroom_pct` is an integer percent computed by the server; the UI
// must render the value verbatim and never re-derive percentages from
// `available_tokens / context_window_tokens`.
// ---------------------------------------------------------------------------

export interface ContextWindowSummary {
  provider: string;
  name: string;
  context_window_tokens: number | null;
}

export interface ContextCurrentSlice {
  last_run_id: string | null;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  available_tokens: number | null;
  headroom_pct: number | null;
}

export interface ContextCallRow {
  event_id: string;
  model_name: string;
  input: number;
  output: number;
  cached_input: number;
  task_id: string | null;
}

export interface ContextSubagentRow {
  subagent_id: string;
  name: string;
  total: number;
  call_count: number;
}

export interface ContextCompressionRow {
  before: number;
  after: number;
  strategy: string;
  at: string;
}

export interface ContextBreakdown {
  by_call: ContextCallRow[];
  by_subagent: ContextSubagentRow[];
  compression_events: ContextCompressionRow[];
}

export interface ConversationContextResponse {
  model: ContextWindowSummary;
  current: ContextCurrentSlice;
  breakdown: ContextBreakdown;
}

export interface MessageListResponse {
  conversation_id: string;
  messages: Message[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ModelSelectionRequest {
  provider?: string | null;
  model_name?: string | null;
  temperature?: number | null;
  timeout_seconds?: number | null;
  max_input_tokens?: number | null;
  supports_streaming?: boolean | null;
  reasoning?: Record<string, unknown> | null;
}

export interface ModelCatalogModel {
  id: string;
  provider: string;
  model_name: string;
  name: string;
  description?: string | null;
  configured: boolean;
  supports_streaming?: boolean;
  supports_attachments?: boolean;
  supports_reasoning?: boolean;
  reasoning?: Record<string, unknown> | null;
}

export interface ModelCatalogResponse {
  default_model_id: string;
  models: ModelCatalogModel[];
}

export type RunContentPartType = "text" | "image" | "document" | "file";

export interface RunContentPart {
  type: RunContentPartType | (string & {});
  text?: string;
  image?: string;
  data?: string;
  mime_type?: string;
  filename?: string;
  name?: string;
  size?: number | null;
  file_id?: string | null;
  url?: string | null;
  content?: unknown;
  metadata?: Record<string, unknown>;
}

export interface RunAttachmentRequest {
  id: string;
  type: RunContentPartType | (string & {});
  name: string;
  content_type?: string | null;
  size?: number | null;
  file_id?: string | null;
  url?: string | null;
  content: RunContentPart[];
  metadata?: Record<string, unknown>;
}

export interface RunQuoteMetadata {
  text?: string;
  message_id?: string | null;
  part_index?: number | null;
  start_index?: number | null;
  end_index?: number | null;
  source?: string | null;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RunBranchMetadata {
  branch_id?: string | null;
  parent_message_id?: string | null;
  source_message_id?: string | null;
  regenerate_from_message_id?: string | null;
  replace_from_message_id?: string | null;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface RuntimeRequestContext {
  roles?: string[];
  permission_scopes?: string[];
  connector_scopes?: Record<string, unknown>;
  context?: Record<string, unknown>;
  trace_metadata?: Record<string, unknown>;
  feature_flags?: string[];
}

export interface CreateRunRequest {
  conversation_id: string;
  org_id: string;
  user_id: string;
  user_input: string;
  content_format?: string;
  idempotency_key?: string | null;
  model?: ModelSelectionRequest | null;
  content?: RunContentPart[];
  attachments?: RunAttachmentRequest[];
  quote?: RunQuoteMetadata | null;
  parent_message_id?: string | null;
  source_message_id?: string | null;
  regenerate_from_message_id?: string | null;
  branch_id?: string | null;
  branch?: RunBranchMetadata | null;
  request_context?: RuntimeRequestContext;
  request_options?: Record<string, unknown>;
}

export interface CreateRunResponse {
  run_id: string;
  conversation_id: string;
  user_message_id: string;
  trace_id: string;
  status: AgentRunStatus;
  stream_url: string;
  events_url: string;
  created_at: string;
  prior_run_ids?: string[];
}

export interface RunStatus {
  run_id: string;
  conversation_id: string;
  org_id: string;
  user_id: string;
  status: AgentRunStatus;
  trace_id: string;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  safe_error: Record<string, unknown> | null;
  latest_sequence_no: number;
}

export interface CancelRunRequest {
  reason?: string | null;
  requested_by_user_id: string;
}

export interface CancelRunResponse {
  run_id: string;
  status: AgentRunStatus;
  cancel_requested_at: string | null;
  latest_sequence_no: number;
}

export interface RuntimeEventEnvelope {
  event_protocol_version?: number;
  event_id: string;
  run_id: string;
  conversation_id: string;
  sequence_no: number;
  source?: RuntimeEventSource;
  event_type: RuntimeApiEventType;
  trace_id?: string;
  parent_event_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  parent_task_id?: string | null;
  task_id?: string | null;
  subagent_id?: string | null;
  display_title?: string | null;
  summary?: string | null;
  status?: string | null;
  activity_kind: RuntimeActivityKind;
  visibility?: RuntimeEventVisibility;
  redaction_state?: RuntimeEventRedactionState;
  presentation?: RuntimeEventPresentation | null;
  payload: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export type RuntimeEventPresentationKind =
  | "progress"
  | "result"
  | "approval"
  | "auth"
  | "error";
export type RuntimeEventPresentationStatus =
  | "Running"
  | "Waiting for permission"
  | "Done"
  | "Failed";

export interface RuntimeEventPresentationPreviewRow {
  title: string;
  subtitle?: string | null;
  url?: string | null;
  badge?: string | null;
}

export interface RuntimeEventPresentation {
  title: string;
  summary?: string | null;
  status_label: RuntimeEventPresentationStatus;
  kind: RuntimeEventPresentationKind;
  group_key?: string | null;
  primary_entity?: string | null;
  action_label?: string | null;
  result_preview?: RuntimeEventPresentationPreviewRow[];
  debug_label?: string | null;
}

export interface RuntimeEventReplayResponse {
  run_id: string;
  events: RuntimeEventEnvelope[];
  latest_sequence_no: number;
  run_status: AgentRunStatus;
  has_more: boolean;
}

export interface ApprovalDecisionRequest {
  decision: ApprovalDecision;
  decided_by_user_id: string;
  reason?: string | null;
  answer?: string | null;
}

export interface ApprovalDecisionResponse {
  approval_id: string;
  run_id: string;
  status: ApprovalStatus;
  decided_at: string;
}

export interface QuestionOption {
  label: string;
  description?: string | null;
  recommended?: boolean;
}

export interface ApprovalRequestedPayload {
  approval_id: string;
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  server_id?: string;
  server_name?: string;
  display_name?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  risk_level?: "low" | "medium" | "high" | "critical" | string;
  read_only?: boolean;
  grant_options?: string[];
  message?: string;
  reason?: string;
  status?: string;
  source_tool_call_id?: string;
  // ask_a_question-specific fields. Present only when approval_kind is
  // "ask_a_question". `options` is widened to a structured shape; bare-string
  // entries from older callers are coerced server-side to `{label}`.
  header?: string | null;
  question?: string;
  hint?: string | null;
  options?: QuestionOption[];
  multi_select?: boolean;
  allow_free_text?: boolean;
  [key: string]: unknown;
}

export interface RuntimeTextPayload {
  message?: string;
  delta?: string;
  summary?: string;
  display_title?: string;
  performance_metrics?: AssistantPerformanceMetrics;
  [key: string]: unknown;
}

export interface ReasoningSummaryPayload {
  summary: string;
  message?: string;
  [key: string]: unknown;
}

export interface ReasoningSummaryDeltaPayload {
  delta: string;
  summary?: string;
  message?: string;
  [key: string]: unknown;
}

export interface ToolCallPayload {
  tool_name: string;
  call_id: string;
  args?: Record<string, unknown>;
  status?: string;
  summary?: string;
  [key: string]: unknown;
}

export interface ToolCallDeltaPayload {
  tool_name?: string;
  call_id: string;
  delta?: string;
  args_delta?: Record<string, unknown>;
  status?: string;
  summary?: string;
  [key: string]: unknown;
}

export type ToolResultStatus =
  | "completed"
  | "failed"
  | "timed_out"
  | "abandoned"
  | "cancelled";

export interface ToolResultPayload {
  tool_name: string;
  call_id: string;
  status?: ToolResultStatus | (string & {});
  output?: Record<string, unknown>;
  summary?: string;
  safe_message?: string;
  error_code?: string;
  error_message?: string;
  [key: string]: unknown;
}

export interface SubagentActivityPayload {
  task_id: string;
  subagent_name?: string;
  subagent_id?: string;
  status?: string;
  display_title?: string;
  short_summary?: string;
  summary?: string;
  message?: string;
  duration_ms?: number;
  // Present on subagent_completed when the worker correlated the
  // subagent's LLM calls back to its task_id (B2). Absent when the
  // provider did not return stable message ids for the subagent.
  usage?: AssistantSubagentUsageRollup;
  [key: string]: unknown;
}

export interface ModelCallCompletedPayload {
  message_id: string;
  performance_metrics: AssistantPerformanceMetrics;
  [key: string]: unknown;
}

export interface RuntimeLifecyclePayload {
  status?: string;
  message?: string;
  summary?: string;
  performance_metrics?: AssistantPerformanceMetrics;
  [key: string]: unknown;
}

export interface PresentationUpdatedPayload {
  call_id?: string;
  approval_id?: string;
  patches?: string[];
  [key: string]: unknown;
}

export interface ApprovalResolvedPayload {
  approval_id: string;
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  // Wire-level status. For approval_kind=ask_a_question this is "answered" or
  // "skipped" (not "approved"/"rejected") so the UI does not have to render a
  // permission-flavored badge for a question card.
  status?: "approved" | "rejected" | "answered" | "skipped" | string;
  decision?: ApprovalDecision;
  message?: string;
  [key: string]: unknown;
}

export interface RuntimeEventPayloadByType {
  run_queued: RuntimeLifecyclePayload;
  run_started: RuntimeLifecyclePayload;
  run_cancelling: RuntimeLifecyclePayload;
  run_cancelled: RuntimeLifecyclePayload;
  run_completed: RuntimeLifecyclePayload;
  run_failed: RuntimeLifecyclePayload;
  progress: RuntimeTextPayload;
  reasoning_summary: ReasoningSummaryPayload;
  reasoning_summary_delta: ReasoningSummaryDeltaPayload;
  tool_call: ToolCallPayload;
  tool_call_started: ToolCallPayload;
  tool_call_delta: ToolCallDeltaPayload;
  tool_result: ToolResultPayload;
  tool_call_completed: ToolResultPayload;
  mcp_auth_required: McpAuthRequiredEventPayload;
  subagent_update: SubagentActivityPayload;
  subagent_started: SubagentActivityPayload;
  subagent_progress: SubagentActivityPayload;
  subagent_completed: SubagentActivityPayload;
  approval_requested: ApprovalRequestedPayload;
  approval_resolved: ApprovalResolvedPayload;
  observation: RuntimeTextPayload;
  error: RuntimeTextPayload;
  model_call_started: RuntimeLifecyclePayload;
  model_call_completed: ModelCallCompletedPayload;
  model_delta: RuntimeTextPayload;
  final_response: RuntimeTextPayload;
  heartbeat: RuntimeLifecyclePayload;
  presentation_updated: PresentationUpdatedPayload;
  budget_warning: BudgetWarningPayload;
  run_rejected: RunRejectedPayload;
  draft_updated: DraftUpdatedPayload;
}

// B7 — budget enforcement event payloads.
//
// `BudgetWarningPayload` fires when a soft cap is crossed (the run still
// proceeds). `RunRejectedPayload` fires when a hard cap would be
// exceeded — the run is rejected before the LLM is called and the
// envelope's `event_type` is `run_rejected` rather than `run_failed` so
// the UI can render "budget exceeded" instead of a generic failure.

export type BudgetScope = "org" | "user";
export type BudgetPeriod = "day" | "month";

export interface BudgetWarningPayload {
  budget_id: string;
  scope: BudgetScope;
  period: BudgetPeriod;
  current_micro_usd: number;
  current_tokens: number;
  limit_micro_usd: number | null;
  limit_tokens: number | null;
  severity: "soft_cap";
}

export interface RunRejectedPayload {
  reason: "budget_exceeded";
  budget_id: string;
  scope: BudgetScope;
  period: BudgetPeriod;
  current_micro_usd: number;
  current_tokens: number;
  limit_micro_usd: number | null;
  limit_tokens: number | null;
}

// PR 1.3 — Workspace-pane Draft artifact.
//
// Drafts are the agent-produced (or user-edited) writable artifact rendered
// in the Workspace-pane Draft tab. Versioned and append-only on the server;
// the FE keeps a per-conversation Map<draft_id, Draft> in `eventReducer.ts`
// keyed by the latest version it has seen on the SSE stream or via list/get.

export type DraftStatus =
  | "draft"
  | "send_pending_approval"
  | "sent"
  | "discarded"
  | "send_failed";

export interface DraftSection {
  heading: string;
  body: string;
}

export interface Draft {
  draft_id: string;
  version: number;
  conversation_id: string;
  run_id: string | null;
  user_id: string;
  title: string;
  content_text: string;
  sections: DraftSection[];
  target_connector: string | null;
  target_metadata: Record<string, unknown> | null;
  citation_ids: string[];
  status: DraftStatus;
  created_at: string;
}

export interface DraftListResponse {
  drafts: Draft[];
}

export interface DraftPatchRequest {
  expected_version: number;
  content_text: string;
  title?: string | null;
}

export interface DraftSendRequest {
  expected_version: number;
  target_connector: string;
  target_metadata?: Record<string, unknown>;
}

export interface DraftSendResponse {
  draft: Draft;
  approval_id: string | null;
  run_id: string | null;
}

export interface DraftDiscardRequest {
  expected_version: number;
}

// `DRAFT_UPDATED` event payload — emitted by `DraftBackend` on every agent
// `awrite` / `aedit`. The shape is the FE projection of one persisted draft
// version plus presentation hints. The server projects `activity_kind="draft"`
// onto the envelope.
export interface DraftUpdatedPayload {
  draft_id: string;
  version: number;
  status: DraftStatus;
  title: string;
  sections: DraftSection[];
  target_connector: string | null;
  target_metadata: Record<string, unknown> | null;
  citation_ids: string[];
  summary: string;
  approval_id?: string;
}

export type StructuredRuntimeEventEnvelope<
  TEventType extends RuntimeApiEventType = RuntimeApiEventType,
> = RuntimeEventEnvelope & {
  event_type: TEventType;
  payload: RuntimeEventPayloadByType[TEventType];
};

export type SkillScope = "user" | "org";
export type SkillSourceType = "user" | "preloaded" | "system";

export interface Skill {
  skill_id: string;
  name: string;
  display_name: string;
  description: string;
  markdown: string;
  virtual_path: string;
  enabled: boolean;
  scope: SkillScope;
  source_type: SkillSourceType;
  version: number;
  allowed_tools: string[];
  compatibility: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CreateSkillRequest {
  org_id: string;
  user_id: string;
  markdown: string;
  display_name?: string;
  enabled?: boolean;
  scope?: SkillScope;
}

export interface UpdateSkillRequest {
  markdown?: string;
  display_name?: string;
  enabled?: boolean;
  scope?: SkillScope;
}

export interface SkillListResponse {
  skills: Skill[];
}

export function isRuntimeEventEnvelope(
  value: unknown,
): value is RuntimeEventEnvelope {
  if (!isPlainRecord(value)) {
    return false;
  }
  const candidate = value;
  return (
    typeof candidate.event_id === "string" &&
    typeof candidate.run_id === "string" &&
    typeof candidate.conversation_id === "string" &&
    typeof candidate.sequence_no === "number" &&
    Number.isInteger(candidate.sequence_no) &&
    candidate.sequence_no >= 0 &&
    isRuntimeApiEventType(candidate.event_type) &&
    (candidate.source === undefined ||
      isRuntimeEventSource(candidate.source)) &&
    isRuntimeActivityKind(candidate.activity_kind) &&
    (candidate.presentation === undefined ||
      candidate.presentation === null ||
      isRuntimeEventPresentation(candidate.presentation)) &&
    isPlainRecord(candidate.payload) &&
    (candidate.metadata === undefined || isPlainRecord(candidate.metadata)) &&
    typeof candidate.created_at === "string"
  );
}

export function isRuntimeEventPresentation(
  value: unknown,
): value is RuntimeEventPresentation {
  if (!isPlainRecord(value)) {
    return false;
  }
  return (
    typeof value.title === "string" &&
    isRuntimeEventPresentationStatus(value.status_label) &&
    isRuntimeEventPresentationKind(value.kind) &&
    (value.result_preview === undefined ||
      (Array.isArray(value.result_preview) &&
        value.result_preview.every(isRuntimeEventPresentationPreviewRow)))
  );
}

function isRuntimeEventPresentationKind(
  value: unknown,
): value is RuntimeEventPresentationKind {
  return (
    value === "progress" ||
    value === "result" ||
    value === "approval" ||
    value === "auth" ||
    value === "error"
  );
}

function isRuntimeEventPresentationStatus(
  value: unknown,
): value is RuntimeEventPresentationStatus {
  return (
    value === "Running" ||
    value === "Waiting for permission" ||
    value === "Done" ||
    value === "Failed"
  );
}

function isRuntimeEventPresentationPreviewRow(value: unknown): boolean {
  return isPlainRecord(value) && typeof value.title === "string";
}

export function isRuntimeTextPayload(
  payload: unknown,
): payload is RuntimeTextPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return (
    typeof payload.message === "string" ||
    typeof payload.delta === "string" ||
    typeof payload.summary === "string" ||
    typeof payload.display_title === "string"
  );
}

export function isAssistantPerformanceMetrics(
  value: unknown,
): value is AssistantPerformanceMetrics {
  if (!isPlainRecord(value)) {
    return false;
  }
  return (
    typeof value.started_at === "string" &&
    typeof value.completed_at === "string" &&
    typeof value.duration_ms === "number" &&
    Number.isFinite(value.duration_ms) &&
    typeof value.chunk_count === "number" &&
    Number.isFinite(value.chunk_count)
  );
}

export function isReasoningSummaryPayload(
  payload: unknown,
): payload is ReasoningSummaryPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return typeof payload.summary === "string";
}

export function isReasoningSummaryDeltaPayload(
  payload: unknown,
): payload is ReasoningSummaryDeltaPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return typeof payload.delta === "string";
}

export function isToolCallPayload(
  payload: unknown,
): payload is ToolCallPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return (
    typeof payload.tool_name === "string" && typeof payload.call_id === "string"
  );
}

export function isToolCallDeltaPayload(
  payload: unknown,
): payload is ToolCallDeltaPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return typeof payload.call_id === "string";
}

export function isToolResultPayload(
  payload: unknown,
): payload is ToolResultPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return (
    typeof payload.tool_name === "string" && typeof payload.call_id === "string"
  );
}

export function isSubagentActivityPayload(
  payload: unknown,
): payload is SubagentActivityPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return typeof payload.task_id === "string";
}

export function isApprovalRequestedPayload(
  payload: unknown,
): payload is ApprovalRequestedPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return typeof payload.approval_id === "string";
}

export function isMcpAuthRequiredPayload(
  payload: unknown,
): payload is McpAuthRequiredEventPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return (
    typeof payload.server_id === "string" &&
    typeof payload.display_name === "string" &&
    typeof payload.auth_url === "string" &&
    typeof payload.expires_at === "string"
  );
}

export function isRuntimeApiEventType(
  value: unknown,
): value is RuntimeApiEventType {
  return (
    typeof value === "string" &&
    (RUNTIME_API_EVENT_TYPES as readonly string[]).includes(value)
  );
}

export function isRuntimeEventSource(
  value: unknown,
): value is RuntimeEventSource {
  return (
    typeof value === "string" &&
    (RUNTIME_EVENT_SOURCES as readonly string[]).includes(value)
  );
}

export function isRuntimeActivityKind(
  value: unknown,
): value is RuntimeActivityKind {
  return (
    typeof value === "string" &&
    (RUNTIME_ACTIVITY_KINDS as readonly string[]).includes(value)
  );
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

// ---------------------------------------------------------------------------
// Auth (A2 sessions, A3 OIDC, A4 local password, A6 MFA, A8 lockout)
//
// Mirrors the public ``/v1/auth/*`` surface served by ``backend-facade``.
// All shapes are additive: existing identity payloads (``SessionResponse``)
// remain unchanged.
// ---------------------------------------------------------------------------

export type AuthProviderKind = "local" | "oidc" | "saml" | "scim";

export interface AuthProviderSummary {
  provider_id: string;
  kind: AuthProviderKind;
  display_name: string;
  enabled: boolean;
}

export interface AuthProvidersResponse {
  providers: AuthProviderSummary[];
}

export interface LoginRequest {
  org_id: string;
  email: string;
  password: string;
}

export interface LoginResponse {
  user_id: string;
  session_id: string;
  bearer_token: string;
  expires_at: string;
  requires_password_change?: boolean;
  requires_mfa?: boolean;
}

export interface AccountSession {
  session_id: string;
  org_id: string;
  user_id: string;
  auth_provider_id?: string | null;
  device_label?: string | null;
  client_ip?: string | null;
  user_agent?: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  mfa_satisfied: boolean;
}

export interface AccountSessionListResponse {
  sessions: AccountSession[];
}

// MFA -----------------------------------------------------------------------

export type MfaFactorKind = "totp" | "webauthn";
export type MfaChallengeKind = "totp" | "webauthn" | "recovery";

export interface MfaFactorSummary {
  factor_id: string;
  kind: MfaFactorKind;
  display_name: string;
  enabled: boolean;
  enrolled_at: string;
  last_used_at?: string | null;
}

export interface MfaFactorListResponse {
  factors: MfaFactorSummary[];
}

export interface TotpEnrollResponse {
  factor_id: string;
  otpauth_url: string;
  secret_b32: string;
  recovery_codes: string[];
}

export interface TotpConfirmRequest {
  factor_id: string;
  code: string;
}

export interface MfaChallengeRequest {
  kind: MfaChallengeKind;
  factor_id?: string | null;
}

export interface MfaChallengeResponse {
  challenge_id: string;
  nonce: string;
  kind: MfaChallengeKind;
  expected_factor_id?: string | null;
  expires_at: string;
  // Present when ``kind === 'webauthn'`` — the
  // ``PublicKeyCredentialRequestOptions`` JSON the navigator consumes.
  webauthn_options?: Record<string, unknown> | null;
}

export interface MfaVerifyRequest {
  challenge_id: string;
  code?: string;
  assertion?: Record<string, unknown>;
  expected_origin?: string;
}

export interface MfaVerifyResponse {
  factor_id: string;
  kind: MfaChallengeKind;
  mfa_satisfied_at: string;
}

export interface MfaRecoveryConsumeRequest {
  code: string;
}

// Login attempts (A8) -------------------------------------------------------

export type LoginAttemptKind =
  | "local"
  | "oidc"
  | "saml"
  | "mfa"
  | "scim_token"
  | "api_key";
export type LoginAttemptOutcome =
  | "success"
  | "bad_password"
  | "unknown_user"
  | "locked_out"
  | "mfa_failed"
  | "provider_rejected";

export interface LoginAttempt {
  attempt_id: string;
  org_id?: string | null;
  email_attempted?: string | null;
  user_id?: string | null;
  auth_kind: LoginAttemptKind;
  outcome: LoginAttemptOutcome;
  ip?: string | null;
  user_agent?: string | null;
  failure_reason?: string | null;
  created_at: string;
}

export interface LoginAttemptListResponse {
  attempts: LoginAttempt[];
}
