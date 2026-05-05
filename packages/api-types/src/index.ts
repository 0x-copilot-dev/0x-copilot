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
  // PR 3.3 — non-blocking MCP discovery. When set, the agent surfaced
  // the connector as a *suggestion* (Connect / Skip card) rather than
  // a blocking auth gate. The run is NOT paused; subsequent events
  // continue to stream while the user decides.
  discovery_reason?: string | null;
  // PR 3.3 — agent's one-line statement of why the user might benefit
  // from connecting (e.g. "could ground claims about ticket progress").
  expected_value?: string | null;
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
  | "approval_forwarded"
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
  | "source_ingested"
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
  "approval_forwarded",
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
  "source_ingested",
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

// PR 1.4 — two-stage approval forwarding. The "forwarded" decision is an
// API-edge variant: it routes the pending approval to a second workspace
// user and never reaches the LangGraph harness. Status "forwarded" is a
// terminal state for the parent row in a chain; resume hangs off the
// child's eventual approve/reject.
export type ApprovalDecision = "approved" | "rejected" | "forwarded";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "forwarded";

export interface ApprovalForwardTarget {
  kind: "workspace_user";
  user_id: string;
}

export interface SessionIdentity {
  org_id: string;
  user_id: string;
  roles: string[];
  permission_scopes: string[];
}

export interface SessionResponse {
  identity: SessionIdentity;
}

/**
 * PR 2.2 — one row in the UserCard workspace switcher (sidebar). Mirrors
 * `services/backend/src/backend_app/routes/me.py::Workspace` shape; keep
 * in lockstep on rename / removal.
 *
 * `role` is the human-readable display name of the caller's primary role
 * in this workspace (Admin / Member / etc.); `null` when no role is
 * assigned. `last_active_at` is `users.last_seen_at` for that org. The
 * v1 endpoint returns only the caller's current workspace; multi-
 * workspace listing widens later (no FE change required).
 */
export interface Workspace {
  org_id: string;
  display_name: string;
  slug: string;
  role: string | null;
  member_count: number;
  last_active_at: string | null;
  is_current: boolean;
}

export interface WorkspaceListResponse {
  workspaces: Workspace[];
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
  /**
   * PR 1.2 — per-chat connector scope override. Map of connector id ->
   * array of scope strings (active for this chat) or null (paused for
   * this chat). Empty object means "no override; defer to inbound
   * header or workspace defaults". Optional for backwards compat with
   * older server payloads.
   */
  enabled_connectors?: ConversationConnectorScopes;
  connectors_updated_at?: string | null;
  /**
   * PR 1.6 — conversation lifecycle additions. ``deleted_at`` is the
   * soft-delete tombstone (the C8 retention sweeper reaps the row on
   * TTL); ``folder`` is a flat sidebar grouping label;
   * ``parent_conversation_id`` is forward-declared for Wave 6 fork
   * lineage. All three are optional for backwards compat with older
   * server payloads pre-migration 0020.
   */
  deleted_at?: string | null;
  folder?: string | null;
  parent_conversation_id?: string | null;
}

/**
 * PR 1.6 — body for ``PATCH /v1/agent/conversations/{id}``.
 *
 * RFC 7396 merge-patch semantics: omit a field to leave it untouched,
 * send `null` to clear (folder/title) or un-archive (`archived: false`).
 * Empty-string folders are normalised to `null` server-side.
 */
export interface UpdateConversationRequest {
  title?: string | null;
  folder?: string | null;
  archived?: boolean;
}

/**
 * PR 1.6 — body for ``PUT /v1/agent/workspace/defaults``.
 *
 * Full-document replace (not merge-patch) — the admin Settings panel
 * always submits the full state. ``retention_days`` composes a
 * ``scope='org'`` row across the relevant kinds in the existing C8
 * retention pipeline; no separate retention storage is introduced.
 */
export interface UpdateWorkspaceDefaultsRequest {
  default_model: WorkspaceDefaultModel;
  default_connectors: ConversationConnectorScopes;
  retention_days: number;
}

export interface WorkspaceDefaultModel {
  provider: string;
  model_name: string;
  reasoning?: ModelReasoningHints | null;
}

export interface WorkspaceDefaultsResponse {
  default_model: WorkspaceDefaultModel;
  default_connectors: ConversationConnectorScopes;
  retention_days: number;
  updated_at: string | null;
  updated_by_user_id: string | null;
}

/**
 * PR 1.2 — per-chat connector scope shape, mirrored from the runtime API.
 * `null` value pauses the connector for this chat; an array activates it
 * with the given scope strings. RFC 7396 merge-patch semantics on writes.
 */
export type ConversationConnectorScopes = Record<
  string,
  readonly string[] | null
>;

export interface UpdateConversationConnectorScopesRequest {
  scopes: ConversationConnectorScopes;
}

export interface ConversationConnectorScopesResponse {
  conversation_id: string;
  scopes: ConversationConnectorScopes;
  updated_at: string | null;
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

/**
 * Model reasoning hints. Mirrors the shape ai-backend's `ModelConfig.reasoning`
 * accepts (`agent_runtime/execution/models.py`). The runtime tolerates extra
 * keys, so the type stays open via the `[key: string]` index signature; the
 * named fields are the ones the UI reads.
 *
 * `depth_label` (PR 3.5 / G3): an optional human-friendly label for the
 * Topbar's `ThinkingDepthControl` announcement when a catalog row wants to
 * override the default Fast/Balanced/Deep wording. Falls back to the FE's
 * built-in label table when absent.
 */
export interface ModelReasoningHints {
  enabled?: boolean;
  effort?: "low" | "medium" | "high";
  summary?: "auto" | "off";
  depth_label?: string;
  [key: string]: unknown;
}

export interface ModelSelectionRequest {
  provider?: string | null;
  model_name?: string | null;
  temperature?: number | null;
  timeout_seconds?: number | null;
  max_input_tokens?: number | null;
  supports_streaming?: boolean | null;
  reasoning?: ModelReasoningHints | null;
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
  reasoning?: ModelReasoningHints | null;
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
  // PR 1.4 — required when `decision === "forwarded"`; rejected by the
  // server otherwise. Self-forward is rejected via 422.
  forward_to?: ApprovalForwardTarget | null;
}

export interface ApprovalDecisionResponse {
  approval_id: string;
  run_id: string;
  status: ApprovalStatus;
  decided_at: string;
  // PR 1.4 — populated only for "forwarded" responses so the FE can
  // render "Waiting on @marcus" without an extra fetch.
  forwarded_to_user_id?: string | null;
  child_approval_id?: string | null;
}

// PR 1.4.1 Gap #6 — recipient inbox row.
export interface AssignedApproval {
  approval_id: string;
  conversation_id: string;
  run_id: string;
  approval_kind: string;
  status: ApprovalStatus;
  chain_parent_approval_id?: string | null;
  forwarded_by_user_id?: string | null;
  forwarded_at?: string | null;
  action_summary: string;
  risk_class?: string | null;
  expires_at?: string | null;
  created_at: string;
}

export interface AssignedApprovalsResponse {
  approvals: AssignedApproval[];
  next_cursor: string | null;
}

// PR 1.4.1 Gap #6 — per-user inbox SSE envelope. Mirrors the
// run-stream's monotonic-sequence reconnect contract; the FE consumes
// this on `/v1/agent/me/inbox/stream`.
export type InboxEventType = "approval_assigned" | "approval_resolved";

export interface InboxEventEnvelope {
  sequence_no: number;
  event_type: InboxEventType;
  approval_id: string;
  status: string;
  org_id: string;
  conversation_id: string;
  actor_user_id: string;
  emitted_at: string;
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

// Citations (PR 1.1). `citation_id` is short ("c<base36>" of the per-run
// ordinal) and is the token the assistant text embeds inline as
// `[c<id>]`. The frontend's markdown plugin resolves these tokens by
// looking up the registry built from `source_ingested` events.
export interface CitationSourceRef {
  citation_id: string;
  source_connector: string;
  source_doc_id: string;
  source_url: string | null;
  title: string;
  snippet: string | null;
  freshness_at: string | null;
  source_tool_call_id: string | null;
  ordinal: number;
}

export interface SourceIngestedPayload {
  citation: CitationSourceRef;
  [key: string]: unknown;
}

// `final_response` is `RuntimeTextPayload` + the sealed citation list, so
// archived reads and the share-recipient view can rebuild chips without
// replaying every `source_ingested` event for the run.
export interface RuntimeFinalResponsePayload extends RuntimeTextPayload {
  citations?: CitationSourceRef[];
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
  // PR 1.4 — "forwarded" is the parent's terminal status when it gets
  // forwarded to a second workspace user; the FE pairs this with a
  // following `approval_forwarded` event to render the inline pill.
  status?:
    | "approved"
    | "rejected"
    | "answered"
    | "skipped"
    | "forwarded"
    | string;
  decision?: ApprovalDecision;
  message?: string;
  [key: string]: unknown;
}

// PR 1.4 — emitted between APPROVAL_RESOLVED (status=forwarded) on the
// parent and APPROVAL_REQUESTED on the child so the reducer can transform
// the original in-thread approval card into a "Waiting on @marcus" pill
// in one step.
export interface ApprovalForwardedPayload {
  approval_id: string; // child approval (the new pending row)
  chain_parent_approval_id: string; // original (now resolved with status=forwarded)
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  forwarded_by_user_id: string;
  forwarded_to_user_id: string;
  forwarded_at: string;
  action_summary?: string;
  status?: "waiting" | string;
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
  approval_forwarded: ApprovalForwardedPayload;
  observation: RuntimeTextPayload;
  error: RuntimeTextPayload;
  model_call_started: RuntimeLifecyclePayload;
  model_call_completed: ModelCallCompletedPayload;
  model_delta: RuntimeTextPayload;
  final_response: RuntimeFinalResponsePayload;
  heartbeat: RuntimeLifecyclePayload;
  presentation_updated: PresentationUpdatedPayload;
  budget_warning: BudgetWarningPayload;
  run_rejected: RunRejectedPayload;
  source_ingested: SourceIngestedPayload;
  draft_updated: DraftUpdatedPayload;
}

// PR 1.3 — Workspace-pane Draft artifact contracts. Mirrors
// services/ai-backend/src/runtime_api/schemas/drafts.py (DraftStatus
// enum + Draft / DraftSection / list / patch / send / discard requests).
export type DraftStatus =
  | "draft"
  | "send_pending_approval"
  | "sent"
  | "discarded";

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
  sections: readonly DraftSection[];
  target_connector: string | null;
  target_metadata: Record<string, unknown> | null;
  citation_ids: readonly string[];
  status: DraftStatus;
  created_at: string;
}

export interface DraftListResponse {
  drafts: readonly Draft[];
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

// Runtime stream payload for the DRAFT_UPDATED event — the runtime
// emits one per draft version created. The FE routes the payload into
// its drafts registry and renders the latest version in the Workspace
// pane Draft tab. Carries the same fields as ``Draft`` plus an
// optional ``summary`` string the projection layer adds for activity
// rows. Required fields mirror what every emit guarantees; the rest
// are optional because some emits (compact updates) omit them.
export interface DraftUpdatedPayload {
  draft_id: string;
  version: number;
  status: DraftStatus;
  title: string;
  sections: readonly DraftSection[];
  target_connector: string | null;
  target_metadata: Record<string, unknown> | null;
  citation_ids: readonly string[];
  summary?: string;
  conversation_id?: string;
  run_id?: string | null;
  user_id?: string;
  content_text?: string;
  created_at?: string;
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

// PR 1.5 — Workspace pane data feeds.
// Read-only archive contracts that complement the live SUBAGENT_* and
// `source_ingested` events on the SSE stream. The shape mirrors
// `services/ai-backend/src/runtime_api/schemas/workspace.py`.

export type SubagentLifecycleStatus =
  | "queued"
  | "running"
  | "completed"
  | "cancelled"
  | "failed"
  | "timed_out";

export type SubagentStatusFilter = "all" | "running" | "recent";

/**
 * PR 1.5 AC-2 — per-subagent token rollup over `runtime_model_call_usage`.
 * `null` on the entry when no model call has been logged for the subagent
 * (rare but possible for sub-second cancellations).
 */
export interface SubagentTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  total_tokens: number;
}

export interface SubagentEntry {
  task_id: string;
  parent_run_id: string;
  subagent_name: string;
  status: SubagentLifecycleStatus;
  display_title: string | null;
  objective_summary: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  result_summary: string | null;
  safe_error_code: string | null;
  safe_error_message: string | null;
  token_usage: SubagentTokenUsage | null;
}

export interface SubagentListResponse {
  conversation_id: string;
  subagents: SubagentEntry[];
  truncated: boolean;
}

export interface SourceEntry {
  citation_id: string;
  source_connector: string;
  source_doc_id: string;
  source_url: string | null;
  title: string | null;
  snippet: string | null;
  freshness_at: string | null;
  citation_count: number;
  last_cited_at: string;
}

export interface SourceListResponse {
  conversation_id: string;
  run_id: string | null;
  sources: SourceEntry[];
  truncated: boolean;
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

// PR 1.4 — type guard for the two-stage approval forwarding payload.
export function isApprovalForwardedPayload(
  payload: unknown,
): payload is ApprovalForwardedPayload {
  if (!isPlainRecord(payload)) {
    return false;
  }
  return (
    typeof payload.approval_id === "string" &&
    typeof payload.chain_parent_approval_id === "string" &&
    typeof payload.forwarded_to_user_id === "string"
  );
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

export function isCitationSourceRef(
  value: unknown,
): value is CitationSourceRef {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.citation_id === "string" &&
    candidate.citation_id.length > 0 &&
    typeof candidate.source_connector === "string" &&
    typeof candidate.source_doc_id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.ordinal === "number" &&
    Number.isInteger(candidate.ordinal) &&
    candidate.ordinal > 0
  );
}

export function isSourceIngestedPayload(
  payload: unknown,
): payload is SourceIngestedPayload {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return false;
  }
  return isCitationSourceRef((payload as Record<string, unknown>).citation);
}
// -----------------------------------------------------------------------------
// PR 4.1 — Settings → "You" group: profile + preferences sidecars.
// -----------------------------------------------------------------------------

/** Working-hours band the user keeps. UI converts ``HH:MM`` + ``tz`` to local
 *  time at render. Server stores wall-clock strings (no DST drift logic). */
export interface WorkingHours {
  tz: string;
  start: string; // 'HH:MM' 24-hour
  end: string;
  days: number[]; // 0=Sun .. 6=Sat
}

export interface UserProfile {
  user_id: string;
  email: string;
  email_verified_at: string | null;
  display_name: string | null;
  title: string | null;
  timezone: string | null; // IANA tz id, e.g. 'America/Los_Angeles'
  locale: string | null; // BCP-47 tag, e.g. 'en-US'
  working_hours: WorkingHours | null;
  avatar_url: string | null;
  updated_at: string;
}

export type UserProfileTheme = "system" | "light" | "dark" | "slate";

/** Mirrors `ACCENT_SCHEMES` in `@enterprise-search/design-system`. */
export type UserProfileAccent =
  | "atlas-orange"
  | "gold"
  | "amber"
  | "red"
  | "lime"
  | "teal"
  | "blue"
  | "violet";

export type UserProfileDensity = "comfortable" | "compact";
export type UserProfileReduceMotion = "auto" | "always" | "off";

export interface AppearancePreferences {
  theme: UserProfileTheme;
  accent: UserProfileAccent;
  density: UserProfileDensity;
  reduce_motion: UserProfileReduceMotion;
}

export interface ShortcutsPreferences {
  /** Map of registry id → tinykeys chord (e.g. `'chat.search': '$mod+P'`). */
  overrides: Record<string, string>;
}

export type NotificationEvent =
  | "mention"
  | "approval_needed"
  | "run_finished"
  | "weekly_digest";

export type NotificationChannel = "email" | "slack" | "desktop";

export interface NotificationsPreferences {
  matrix: Record<NotificationEvent, Record<NotificationChannel, boolean>>;
}

export interface UserPreferences {
  appearance: AppearancePreferences;
  shortcuts: ShortcutsPreferences;
  notifications: NotificationsPreferences;
  /** ISO timestamp of the last write; '' when no row exists yet (fresh user). */
  updated_at: string;
}

/** Merge-patch shape — every field optional, `null` clears (RFC 7396).
 *  Note: `working_hours: null` clears the band; omit to leave untouched. */
export type UpdateUserProfileRequest = {
  display_name?: string | null;
  title?: string | null;
  timezone?: string | null;
  locale?: string | null;
  working_hours?: WorkingHours | null;
  avatar_url?: string | null;
};

/** Deep-partial merge-patch: send only the keys you want to change.
 *  `notifications.matrix.mention.email = false` updates only that cell. */
export interface UpdateUserPreferencesRequest {
  appearance?: Partial<AppearancePreferences>;
  shortcuts?: { overrides?: Record<string, string> };
  notifications?: {
    matrix?: Partial<
      Record<NotificationEvent, Partial<Record<NotificationChannel, boolean>>>
    >;
  };
}
