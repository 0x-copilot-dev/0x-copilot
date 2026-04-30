export type McpTransport = "http" | "sse" | "stdio";
export type McpAuthMode = "none" | "oauth2" | "api_key" | "service_account";
export type McpAuthState =
  | "unauthenticated"
  | "auth_skipped"
  | "auth_pending"
  | "authenticated"
  | "auth_failed"
  | "auth_unsupported";
export type McpServerHealth = "healthy" | "degraded" | "unavailable" | "disabled";

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
  created_at: string;
  updated_at: string;
}

export interface CreateMcpServerRequest {
  org_id: string;
  user_id: string;
  url: string;
  display_name?: string;
  transport?: McpTransport;
  auth_mode?: McpAuthMode;
}

export interface UpdateMcpServerRequest {
  display_name?: string;
  enabled?: boolean;
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
  server_id: string;
  server_name: string;
  display_name: string;
  auth_url: string;
  expires_at: string;
  message: string;
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
export type RuntimeEventVisibility = "user" | "internal" | "audit";
export type RuntimeEventRedactionState = "redacted" | "truncated" | "offloaded";
export type RuntimeEventSource =
  | "runtime"
  | "model"
  | "tool"
  | "mcp"
  | "subagent"
  | "memory"
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
  | "heartbeat";

export type ApprovalDecision = "approved" | "rejected";
export type ApprovalStatus = "pending" | "approved" | "rejected";

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

export interface Message {
  message_id: string;
  conversation_id: string;
  org_id: string;
  run_id: string | null;
  role: MessageRole;
  content_text: string;
  content_format: string;
  parent_message_id: string | null;
  token_count: number | null;
  trace_id: string | null;
  status: MessageStatus;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
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
  source?: RuntimeEventSource | string;
  event_type: RuntimeApiEventType | string;
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
  visibility?: RuntimeEventVisibility;
  redaction_state?: RuntimeEventRedactionState;
  payload: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  created_at: string;
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
}

export interface ApprovalDecisionResponse {
  approval_id: string;
  run_id: string;
  status: ApprovalStatus;
  decided_at: string;
}

export interface ApprovalRequestedPayload {
  approval_id: string;
  message?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface RuntimeTextPayload {
  message?: string;
  delta?: string;
  summary?: string;
  display_title?: string;
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
  summary?: string;
  message?: string;
  [key: string]: unknown;
}

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

export function isRuntimeEventEnvelope(value: unknown): value is RuntimeEventEnvelope {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.event_id === "string" &&
    typeof candidate.run_id === "string" &&
    typeof candidate.conversation_id === "string" &&
    typeof candidate.sequence_no === "number" &&
    typeof candidate.event_type === "string" &&
    typeof candidate.payload === "object" &&
    candidate.payload !== null
  );
}

export function isRuntimeTextPayload(payload: unknown): payload is RuntimeTextPayload {
  return typeof payload === "object" && payload !== null;
}

export function isReasoningSummaryPayload(payload: unknown): payload is ReasoningSummaryPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.summary === "string";
}

export function isReasoningSummaryDeltaPayload(
  payload: unknown
): payload is ReasoningSummaryDeltaPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.delta === "string";
}

export function isToolCallPayload(payload: unknown): payload is ToolCallPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.tool_name === "string" && typeof candidate.call_id === "string";
}

export function isToolCallDeltaPayload(payload: unknown): payload is ToolCallDeltaPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.call_id === "string";
}

export function isToolResultPayload(payload: unknown): payload is ToolResultPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.tool_name === "string" && typeof candidate.call_id === "string";
}

export function isSubagentActivityPayload(payload: unknown): payload is SubagentActivityPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.task_id === "string";
}

export function isApprovalRequestedPayload(payload: unknown): payload is ApprovalRequestedPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.approval_id === "string";
}
