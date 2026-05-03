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
  | "event";
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
  | "model_delta"
  | "final_response"
  | "heartbeat"
  | "presentation_updated";

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
  "model_delta",
  "final_response",
  "heartbeat",
  "presentation_updated",
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
export type RuntimeEventPresentationConfidence = "low" | "medium" | "high";

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
  confidence?: RuntimeEventPresentationConfidence | null;
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
  question?: string;
  hint?: string | null;
  options?: string[];
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

export interface ToolResultPayload {
  tool_name: string;
  call_id: string;
  status?: string;
  output?: Record<string, unknown>;
  summary?: string;
  safe_message?: string;
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
  approval_resolved: RuntimeLifecyclePayload;
  observation: RuntimeTextPayload;
  error: RuntimeTextPayload;
  model_delta: RuntimeTextPayload;
  final_response: RuntimeTextPayload;
  heartbeat: RuntimeLifecyclePayload;
  presentation_updated: PresentationUpdatedPayload;
}

export type StructuredRuntimeEventEnvelope<
  TEventType extends RuntimeApiEventType = RuntimeApiEventType,
> = RuntimeEventEnvelope & {
  event_type: TEventType;
  payload: RuntimeEventPayloadByType[TEventType];
};

export type SkillScope = "user" | "org";
export type SkillSourceType = "user" | "preloaded";

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
