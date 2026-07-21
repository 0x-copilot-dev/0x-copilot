export { ADAPTER_ALLOWLIST, type AdapterAllowlist } from "./adapterAllowlist";

// Branded ID types — used in approval payloads + responses (P1-A re-scoped,
// cross-audit §2.1). Imported here so they are in scope for the approval
// types declared in this file; the canonical declaration site is
// `./brands.ts` and the public re-export is the `export type { ... } from
// "./brands"` block near the end of this file.
import type {
  ApprovalId,
  ConversationId,
  ProjectId,
  RunId,
  TenantId,
  UserId,
} from "./brands";

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
  /**
   * PR 3.4.1 — brand metadata. ``logo_url`` is the row favicon (frontend
   * falls through to a letter glyph on 404). ``brand_color`` tints the
   * chip surface. ``scopes_summary`` is the popover row's one-line
   * subtitle. ``default_scopes`` is the resume-from-paused payload PR 1.2's
   * PATCH endpoint round-trips. ``admin_managed`` gates the popover's
   * Enable button for non-admin members.
   *
   * All optional / defaulted: old clients that ignore these still render
   * correctly; rows that lack metadata fall back to the design-system
   * letter glyph and a state-specific subtitle.
   */
  logo_url?: string | null;
  brand_color?: string | null;
  scopes_summary?: string | null;
  default_scopes?: readonly string[];
  admin_managed?: boolean;
  /**
   * PR 4.4.6 — marketing description copied from the catalog entry on
   * install. Empty for custom (non-catalog) servers.
   */
  description?: string;
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

/**
 * PR 4.4.6 — curated catalog entry. Org-agnostic; the wire shape served
 * by ``GET /v1/mcp/catalog``. Frontend cross-references with the user's
 * ``McpServer`` list (matching ``server_id === "seed:" + slug``) to
 * decide between Install / Resume install / Installed in the catalog
 * grid.
 */
export interface McpCatalogEntry {
  slug: string;
  display_name: string;
  url: string;
  transport: McpTransport;
  auth_mode: McpAuthMode;
  description: string;
  logo_url?: string | null;
  brand_color?: string | null;
  scopes_summary?: string | null;
  default_scopes?: readonly string[];
  /**
   * When true, install requires the caller to supply a pre-registered
   * OAuth client (the vendor doesn't expose RFC 8414 metadata or RFC
   * 7591 dynamic client registration). Frontend prompts for credentials
   * before submitting the install request.
   */
  requires_pre_registered_client: boolean;
  verified: boolean;
  /**
   * PR 4.4.7 (Phase 1) — workspace's progressive-discovery default
   * for this catalog entry. When true, the agent may surface this
   * connector as a *suggestion* even before the current user installs
   * or authenticates it, so users learn about capabilities instead of
   * perceiving them as platform limitations.
   *
   * Phase 1 ships only the data plumbing + a per-user override
   * persisted client-side. Phase 2 wires this into a runtime
   * "suggested connectors" surface; until then the field has no
   * runtime effect — agents only see installed + authenticated
   * servers via ``McpPermissionPolicy``.
   *
   * Optional with a default fallback so older server payloads stay
   * compatible.
   */
  discoverable?: boolean;
}

export interface McpCatalogResponse {
  entries: readonly McpCatalogEntry[];
}

export interface InstallMcpServerRequest {
  org_id: string;
  user_id: string;
  slug: string;
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
  /**
   * Pre-baked OAuth URL. Always present on the *blocking* auth gate
   * (PR 3.3) where the user clicks Connect and we redirect immediately.
   * Absent on the *catalog suggestion* variant (PR 4.4.7 Phase 2,
   * Slice C) — for an uninstalled connector, no MCP server row exists
   * yet to issue an auth URL against. The FE branches on
   * ``catalog_slug`` to deep-link the install overlay instead.
   */
  auth_url?: string;
  /** Companion to ``auth_url`` — same lifecycle. */
  expires_at?: string;
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
  /**
   * PR 4.4.7 Phase 2 (Slice C) — present only when the suggestion came
   * from the org catalog (uninstalled connector). The FE's
   * ``ConnectorAuthTool`` branches Connect on this field: present →
   * route through the install flow; absent → run OAuth against the
   * already-installed server.
   */
  catalog_slug?: string | null;
  /**
   * PR 4.4.7 follow-up — only meaningful alongside ``catalog_slug``.
   * When false (default), Connect runs the 1-click chain inline:
   * install + start OAuth + redirect. When true, Connect opens a
   * credentials form first because the vendor doesn't support RFC
   * 8414 metadata or RFC 7591 dynamic client registration (Atlassian,
   * GitHub, Intercom, PayPal, Plaid, Square).
   */
  requires_pre_registered_client?: boolean;
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
  | "draft"
  | "note";
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
  | "approval_undo_requested"
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
  | "sources_ingested"
  | "citation_made"
  | "draft_updated"
  | "compression_note"
  | "subagent_fleet_started"
  | "subagent_fleet_finished"
  | "subagent_paused"
  | "subagent_resumed"
  | "adapter_generated"
  | "surface_spec_generated"
  | "workspace_snapshot_captured";

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
  "approval_undo_requested",
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
  "sources_ingested",
  "citation_made",
  "draft_updated",
  "compression_note",
  "subagent_fleet_started",
  "subagent_fleet_finished",
  "subagent_paused",
  "subagent_resumed",
  "adapter_generated",
  "surface_spec_generated",
  "workspace_snapshot_captured",
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
  "note",
] as const satisfies readonly RuntimeActivityKind[];

// PR 1.4 — two-stage approval forwarding. The "forwarded" decision is an
// API-edge variant: it routes the pending approval to a second workspace
// user and never reaches the LangGraph harness. Status "forwarded" is a
// terminal state for the parent row in a chain; resume hangs off the
// child's eventual approve/reject.
//
// P1-A re-scoped — "suggest_edit" decision/status. Like "forwarded" it is
// API-edge: it resolves the current pending row, creates a new pending
// child row carrying the approver's edited payload, and re-emits
// `approval_requested` for the originator. The LangGraph harness is NOT
// resumed — the run remains in `waiting_for_approval` until the child
// reaches `approved` / `rejected`.
export type ApprovalDecision =
  | "approved"
  | "rejected"
  | "forwarded"
  | "suggest_edit";
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "forwarded"
  | "suggest_edit";

export interface ApprovalForwardTarget {
  kind: "workspace_user";
  user_id: UserId;
}

// PR 4.4.6.2 — structured consent-card payload for `approval_kind == "mcp_tool"`.
// Mirrors `runtime_api/schemas/approvals.py::McpApprovalMetadata`. Optional on
// the wire so old emitters (no structured fields) and new readers stay
// compatible — clients fall back to inferring from `read_only` + `risk_level`.
export type McpApprovalCategory = "read" | "write" | "action";
export type McpApprovalReasonCode =
  | "read_only_first_use"
  | "writes_out_of_workspace"
  | "risk_high"
  | "irreversible"
  | "default";
export type McpApprovalReversible = "yes" | "no" | "n/a";

export interface McpApprovalParam {
  label: string;
  value: string;
  hint?: string | null;
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
  /**
   * Phase 6.5 §4 (Projects extensions, P6.5-C1) — when the user creates
   * a chat while on a `/projects/<id>` route (or any subroute), the
   * frontend populates this field so the new conversation is filed
   * under the project. `null` (or omitted) keeps the chat Unfiled,
   * matching the Phase 1 default.
   *
   * The composer's `[Filed under: ▾]` chip is the user-visible override
   * — the chip's selected value is what the request sends; the route is
   * the default, not a hard binding (compliance: untrusted-input rule).
   *
   * Backend wiring lands with P6.5-A2 (ai-backend conversation hook).
   * Until A2 lands the field is intentionally omitted from the wire
   * payload when the resolved value is `null` (the Phase 1 default
   * shape is preserved bit-for-bit). The frontend only sends
   * `project_id` when a non-null value is resolved — see
   * `apps/frontend/src/api/agentApi.ts::createConversation`.
   */
  project_id?: ProjectId | null;
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
   * PR 2.2.1 — most recent run's status, projected from `runtime_runs`
   * by the conversation list/get endpoints. Drives the sidebar's live
   * indicator on cold reload (no need to open an SSE first to learn
   * which chats are running). Optional for backwards compat with older
   * server builds; `null` for conversations that never ran.
   */
  latest_run_status?: AgentRunStatus | null;
  latest_run_id?: string | null;
  /** PR A3 — id of the message this conversation was self-forked from
   * ("retry from here" / "fork to new chat"). Mutually exclusive with
   * forked_from_share_id (declared below); both nullable for non-fork
   * rows. */
  forked_from_message_id?: string | null;
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
  /**
   * PR 6.2 — fork lineage. Audit pointer to the share row that
   * authorised the conversation's creation; non-FK so revoking the
   * share does not break the conversation. NULL on every non-forked
   * row. Optional for backwards compat with payloads pre-migration
   * 0022.
   */
  forked_from_share_id?: string | null;
}

/**
 * PR 6.2 — body for ``POST /v1/agent/shares/{share_token}/fork``.
 *
 * Both fields optional. ``title`` defaults to ``"Forked from
 * {source_title}"`` server-side when omitted. ``folder`` defaults to
 * NULL.
 */
export interface ForkRequest {
  title?: string | null;
  folder?: string | null;
}

/**
 * PR 6.2 — response shape for the fork endpoint.
 *
 * ``conversation_id`` is the new (recipient-owned) conversation; the
 * FE navigates to it via ``/?conversationId={conversation_id}``.
 * ``fork_message_count`` powers the post-fork toast.
 */
export interface ForkResponse {
  conversation_id: string;
  parent_conversation_id: string;
  forked_from_share_id: string;
  fork_message_count: number;
  title: string | null;
  folder: string | null;
  created_at: string;
  user_id: string;
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
  /**
   * PR 4.3 — workspace-policy knobs that flow into RunService's
   * resolution chain. All fields optional; absent fields fall through
   * to deployment defaults. Adding this field is **non-breaking**: the
   * server tolerates omission via a default-factory.
   */
  behavior_overrides?: WorkspaceBehaviorOverrides;
  /**
   * PR-2C — the model ids/model_names this workspace has enabled in its
   * pickers. `null` or omitted = no explicit curation (the server enables
   * the newest models per configured provider); `[]` = everything disabled.
   */
  enabled_models?: readonly string[] | null;
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
  /**
   * PR 4.3 — server always populates this field (defaults to all-None
   * + ``training_data_opt_out=false`` when no row exists).
   */
  behavior_overrides: WorkspaceBehaviorOverrides;
  /** PR-2C — `null` when the workspace hasn't curated its model list. */
  enabled_models: readonly string[] | null;
  updated_at: string | null;
  updated_by_user_id: string | null;
}

/** PR 4.3 — closed enum for the citation-density preset. */
export type CitationDensity = "minimal" | "standard" | "thorough";

/** PR 4.3 — closed enum for the refusal-behavior preset. */
export type RefusalBehavior = "standard" | "strict" | "permissive";

/** PR 4.3 — closed enum for the default reasoning effort preset. */
export type ReasoningEffort = "low" | "medium" | "high";

/**
 * PR 4.3 — workspace-policy knobs persisted in
 * ``workspace_defaults.behavior_overrides`` (JSONB).
 *
 * Every field is optional; absent fields fall through to deployment
 * defaults at run-create. ``training_data_opt_out`` defaults to
 * ``false``; the FE always sends an explicit boolean for clarity.
 */
export interface WorkspaceBehaviorOverrides {
  system_prompt_override?: string | null;
  /** Clamped to [0, 1] server-side. */
  temperature?: number | null;
  citation_density?: CitationDensity | null;
  refusal_behavior?: RefusalBehavior | null;
  default_reasoning_effort?: ReasoningEffort | null;
  training_data_opt_out?: boolean;
}

/** PR 4.3 — kind of retention TTL (mirrors `RetentionKind` server-side). */
export type RetentionKind =
  | "messages"
  | "events"
  | "context_payloads"
  | "checkpoints"
  | "memory_items";

/** PR 4.3 — provenance scope for an effective TTL. `null` ⇒ deployment default. */
export type RetentionSourceScope =
  | "org"
  | "user"
  | "conversation"
  | "assistant"
  | null;

/** PR 4.3 — one row in the effective-TTL response. */
export interface RetentionEffectivePolicyEntry {
  kind: RetentionKind;
  ttl_seconds: number | null;
  source_scope: RetentionSourceScope;
  source_policy_id: string | null;
}

/** PR 4.3 — response for ``GET /v1/retention/effective``. */
export interface RetentionEffectiveResponse {
  effective: Record<RetentionKind, RetentionEffectivePolicyEntry>;
}

/** PR 4.3 — request body for ``POST /v1/agent/workspace/export``. */
export interface WorkspaceExportRequest {
  /** Only "workspace" is accepted in v1. */
  scope?: "workspace";
}

/** PR 4.3 — response from the export-queue stub. */
export interface WorkspaceExportResponse {
  export_id: string;
  status: "queued";
}

/**
 * PR 4.3 — query parameter shape for ``DELETE /v1/agent/workspace/data``.
 * Sent as a query param (not a body) — DELETE-with-body is not
 * idiomatic and intermediaries occasionally strip the body.
 */
export interface WorkspaceDeleteAllParams {
  /** Caller types the org slug here; correctness is recorded in audit. */
  confirm_slug: string;
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

// PR 4.2 — Settings → "Workspace" group --------------------------------

/** Full workspace branding shape (admin Settings → Workspace). The
 *  per-user-switcher view is `Workspace` (above) with role / member_count
 *  / is_current fields; this is the global record. */
export interface WorkspaceSettings {
  org_id: string;
  display_name: string;
  slug: string;
  deployment_kind: string;
  status: string;
  metadata: { logo_url?: string } & Record<string, unknown>;
  created_at: string;
}

export interface UpdateWorkspaceSettingsRequest {
  display_name?: string | null;
  slug?: string | null;
  metadata?: { logo_url?: string | null } & Record<string, unknown>;
}

export type WorkspaceRoleName = "admin" | "member" | "viewer";

export type WorkspaceMemberSource =
  | "local"
  | "oidc"
  | "saml"
  | "scim"
  | "bootstrap"
  | "invite"
  // Sign-In-With-Ethereum (wallet) — matches the backend OrganizationMemberSource
  // enum, which has carried `siwe` since wallet login shipped.
  | "siwe";

export interface MemberRoleSummary {
  id: string;
  name: WorkspaceRoleName;
  display_name: string;
}

export interface Member {
  user_id: string;
  email: string;
  email_verified_at: string | null;
  display_name: string | null;
  title: string | null;
  role: MemberRoleSummary | null;
  joined_at: string;
  last_seen_at: string | null;
  removed_at: string | null;
  source: WorkspaceMemberSource;
}

export interface MemberListResponse {
  members: Member[];
  next_cursor: string | null;
}

export interface UpdateMemberRequest {
  role: WorkspaceRoleName;
}

export interface InvitationCreator {
  user_id: string;
  display_name: string | null;
}

export interface Invitation {
  invite_id: string;
  email: string;
  role: WorkspaceRoleName;
  token_prefix: string;
  created_by: InvitationCreator;
  created_at: string;
  expires_at: string;
}

export interface CreateInvitationRequest {
  email: string;
  role: WorkspaceRoleName;
  ttl_seconds?: number;
}

export interface CreateInvitationResponse extends Invitation {
  /** Plaintext bearer; surfaced exactly once. */
  token: string;
  /** Built by the facade so the FE doesn't need to know the host. */
  accept_url: string | null;
}

export interface InvitationListResponse {
  invitations: Invitation[];
}

export interface AcceptInvitationResponse {
  invite_id: string;
  org_id: string;
  org_display_name: string;
  user_id: string;
  role: WorkspaceRoleName;
  accept_redirect: string;
}

export interface BillingPlan {
  tier: string;
  display_name: string;
  managed_externally: boolean;
  billing_contact: string | null;
}

export interface BillingSeats {
  used: number;
  limit: number;
  removed_in_period: number;
}

export interface BillingPeriod {
  start: string;
  end: string;
}

export interface BillingBudgetSummary {
  scope: string;
  period: string;
  limit_micro_usd: number | null;
  current_spend_micro_usd: number | null;
}

export interface BillingInvoiceStub {
  invoice_id: string | null;
  period_start: string | null;
  period_end: string | null;
  amount_micro_usd: number | null;
  status: string | null;
}

export interface BillingDigest {
  plan: BillingPlan;
  seats: BillingSeats;
  current_period: BillingPeriod;
  budgets: BillingBudgetSummary[];
  invoices: BillingInvoiceStub[];
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

/**
 * One connector in a by-connector breakdown (PR 7.2).
 *
 * `connector_slug` is the empty string (`""`) for the "(unattributed)"
 * bucket — calls before any tool fired this turn. The frontend renders
 * the empty slug as a localised "Unattributed" label.
 */
export interface UsageConnectorRow {
  connector_slug: string;
  /**
   * Sub-PRD 01d: model split inside a connector. Empty string for
   * pre-01d rows (no model dimension on the connector rollup before
   * the migration) and for aggregated by-slug views that collapse
   * the model dimension. Frontend renders empty as "(no model)".
   */
  model_name?: string;
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

/**
 * Sub-PRD 01d — one row of the org-scoped subagent breakdown
 * (`GET /v1/usage/org/subagents`). `subagent_slug` is the empty
 * string for orchestrator-scope LLM calls (mirrors the connector
 * rollup's "(unattributed)" pattern).
 */
export interface UsageSubagentRow {
  subagent_slug: string;
  model_provider: string;
  model_name: string;
  call_count: number;
  input: number;
  output: number;
  cached_input: number;
  cache_creation_input: number;
  reasoning: number;
  audio_input: number;
  audio_output: number;
  total: number;
  cost_micro_usd: number | null;
}

/**
 * Sub-PRD 01d — one row of the org-scoped purpose breakdown
 * (`GET /v1/usage/org/purpose`). `purpose` is one of the
 * `Purpose` StrEnum string values: `main`, `tool_planning`,
 * `tool_interpretation`, `subagent_work`, `context_compression`.
 */
export interface UsagePurposeRow {
  purpose: string;
  model_provider: string;
  model_name: string;
  call_count: number;
  input: number;
  output: number;
  cached_input: number;
  cache_creation_input: number;
  reasoning: number;
  audio_input: number;
  audio_output: number;
  total: number;
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
  by_connector: UsageConnectorRow[];
  cold_start_fallback: boolean;
}

export interface UsageOrgResponse {
  period: UsagePeriodWindow;
  currency: "USD";
  total: UsageTotals;
  by_day: UsageDailyRow[];
  by_model: UsageModelRow[];
  by_user: UsageConversationRow[];
  by_connector: UsageConnectorRow[];
  cold_start_fallback: boolean;
}

/**
 * Sub-PRD 01d — response for `GET /v1/usage/org/subagents`.
 * Admin-only (same auth scope as `/v1/usage/org`). Rows sorted by
 * cost desc.
 */
export interface UsageOrgSubagentsResponse {
  period: UsagePeriodWindow;
  currency: "USD";
  rows: UsageSubagentRow[];
  cold_start_fallback: boolean;
}

/**
 * Sub-PRD 01d — response for `GET /v1/usage/org/purpose`.
 * Admin-only. Rows sorted by cost desc.
 */
export interface UsageOrgPurposeResponse {
  period: UsagePeriodWindow;
  currency: "USD";
  rows: UsagePurposeRow[];
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
  by_connector: UsageConnectorRow[];
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
  /**
   * PR-2C: whether this model is shown in the workspace's composer picker
   * (Settings → Models toggles curate it). Optional/defaults-true for wire
   * back-compat with consumers that predate the flag.
   */
  enabled?: boolean;
  supports_streaming?: boolean;
  supports_attachments?: boolean;
  supports_reasoning?: boolean;
  reasoning?: ModelReasoningHints | null;
  /**
   * models.dev-sourced metadata (optional additions — entries without
   * live/cached/snapshot coverage, e.g. the runtime default model, omit them).
   */
  context_window?: number | null;
  max_output_tokens?: number | null;
  input_cost_per_mtok?: number | null;
  output_cost_per_mtok?: number | null;
  supports_tools?: boolean | null;
  release_date?: string | null;
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

/**
 * User-selectable reasoning depth — the Fast / Balanced / Deep composer
 * picker maps 1:1 to this literal union. The runtime translates depth into
 * timeout, max_output_tokens, and tool-call-budget multipliers (see
 * `services/ai-backend/src/agent_runtime/execution/depth.py`). When omitted,
 * the runtime applies its default behavior (equivalent to `balanced`).
 */
export type ReasoningDepth = "fast" | "balanced" | "deep";

export interface CreateRunRequest {
  conversation_id: string;
  org_id: string;
  user_id: string;
  user_input: string;
  content_format?: string;
  idempotency_key?: string | null;
  model?: ModelSelectionRequest | null;
  /**
   * Per-turn reasoning depth. Optional — when null/absent the runtime keeps
   * the model's configured defaults (no regression vs. pre-depth behaviour).
   */
  reasoning_depth?: ReasoningDepth | null;
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
  decided_by_user_id: UserId;
  reason?: string | null;
  answer?: string | null;
  // PR 1.4 — required when `decision === "forwarded"`; rejected by the
  // server otherwise. Self-forward is rejected via 422.
  forward_to?: ApprovalForwardTarget | null;
  // P1-A re-scoped — required when `decision === "suggest_edit"`; rejected
  // by the server otherwise. Empty objects are rejected too. The keys are
  // the tool-call argument names the approver edited.
  edited_payload?: Record<string, unknown> | null;
}

export interface ApprovalDecisionResponse {
  approval_id: ApprovalId;
  run_id: RunId;
  status: ApprovalStatus;
  decided_at: string;
  // PR 1.4 — populated only for "forwarded" responses so the FE can
  // render "Waiting on @marcus" without an extra fetch.
  forwarded_to_user_id?: UserId | null;
  // PR 1.4 / P1-A re-scoped — populated for "forwarded" AND "suggest_edit"
  // responses; the child row's id is the newly-created pending approval
  // the originator now sees.
  child_approval_id?: ApprovalId | null;
  // PR 4.4.6.4 — non-null only when status === "approved" AND the
  // original request was tagged reversible="yes". ISO 8601. The FE
  // uses this to drive the 60s undo countdown on the receipt.
  undo_expires_at?: string | null;
}

// PR 4.4.6.4 — result of a successful (or repeat) undo request.
export interface ApprovalUndoResponse {
  approval_id: ApprovalId;
  run_id: RunId;
  undo_requested_at: string;
  undo_expires_at: string;
}

// PR 1.4.1 Gap #6 — recipient inbox row.
export interface AssignedApproval {
  approval_id: ApprovalId;
  conversation_id: ConversationId;
  run_id: RunId;
  approval_kind: string;
  status: ApprovalStatus;
  chain_parent_approval_id?: ApprovalId | null;
  forwarded_by_user_id?: UserId | null;
  forwarded_at?: string | null;
  action_summary: string;
  risk_class?: string | null;
  expires_at?: string | null;
  created_at: string;
  // P1-A re-scoped — non-null only on rows produced by a SUGGEST_EDIT
  // decision so the originator can render a diff vs the original
  // arguments without a second fetch.
  edited_payload?: Record<string, unknown> | null;
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
  approval_id: ApprovalId;
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  // PR #43 — ApprovalBatch projection. The batch_id is 1:1 with a single
  // LangGraph interrupt; batch_index is the typed position within the
  // interrupt's action_requests list. For single-action interrupts the
  // batch has size 1 and batch_index is 0. Optional so existing FE
  // handlers (which don't read batch metadata) keep working; a future
  // PR can group cards by batch_id and add an "approve all" affordance.
  batch_id?: string;
  batch_index?: number;
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
  // P1-A re-scoped — set on the child approval emitted after a
  // SUGGEST_EDIT decision; carries the approver's edited tool-call
  // arguments so the originator's FE can diff vs the original.
  edited_payload?: Record<string, unknown>;
  edited_by_user_id?: UserId;
  chain_parent_approval_id?: ApprovalId;
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

// P7 — batched variant of `source_ingested`. Emitted by
// `CitationLedger.register_many` (the MCP projector after the PR2 switch)
// when multiple sources are ingested in one ledger call. Payload carries
// an ordered list of `CitationSourceRef`; FE reducers iterate it as if N
// `source_ingested` events had arrived. Order matches the ledger's
// allocation order (ascending ordinal). The singular event type is
// retained for per-source emitters (provider grounding, capturing tool).
export interface SourcesIngestedPayload {
  citations: CitationSourceRef[];
  [key: string]: unknown;
}

// PR 1.1-rev2 — model-declared, conversation-scoped citation pointers.
// The model emits opaque tokens of the form `[[N]]` where N is the
// `conversation_ordinal` of a tool invocation. The runtime resolves each
// occurrence to a tool invocation and emits one `citation_made` event per
// resolved marker — no separate per-source registry, no shape parsing of
// tool results. Designed to coexist with PR 1.1's `[c<id>]` chips during
// rollout; the legacy types remain until the cut-over completes.
export interface CitationLink {
  /** `tool_invocations.conversation_ordinal` — monotonic per conversation,
   *  stable across turns. The inline token is `[[<conversation_ordinal>]]`. */
  conversation_ordinal: number;
  /** Assistant message that contains the resolved chip. */
  message_id: string;
  /** 0-based char offset of "[[" in the assembled assistant text. */
  prose_offset: number;
  /** Length of the matched token (e.g. `[[12]]` is length 6). */
  prose_length: number;
  /** Denormalized for FE convenience; same as the cited tool invocation's
   *  `tool_call_id`. Resolves the chip to a tool card without an extra fetch. */
  source_tool_call_id: string;
}

export interface CitationMadePayload {
  link: CitationLink;
  [key: string]: unknown;
}

// `final_response` is `RuntimeTextPayload` + the sealed citation list, so
// archived reads and the share-recipient view can rebuild chips without
// replaying every `source_ingested` event for the run.
export interface RuntimeFinalResponsePayload extends RuntimeTextPayload {
  citations?: CitationSourceRef[];
  /** PR 1.1-rev2 — sealed list of conversation_ordinals referenced in the
   *  assistant's final text, in first-occurrence order. Carries integers,
   *  not payloads — source detail lives in the tool invocation log. */
  cited_ordinals?: number[];
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
  /** Generative-UI (PRD-01) — the surface projection for this tool result, when
   * the backend resolved one. Absent ⇒ the FE renders the raw output (tier-3).
   * The spec inside may itself be absent and arrive later via
   * `surface_spec_generated` (merged by `surface_uri`). */
  surface?: SurfaceEnvelope;
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
  approval_id: ApprovalId;
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  // PR #43 — mirrors ``ApprovalRequestedPayload.batch_id`` / ``batch_index``
  // so a client tracking by batch can correlate a resolve event back to the
  // requesting batch without parsing the approval_id string.
  batch_id?: string;
  batch_index?: number;
  // Wire-level status. For approval_kind=ask_a_question this is "answered" or
  // "skipped" (not "approved"/"rejected") so the UI does not have to render a
  // permission-flavored badge for a question card.
  // PR 1.4 — "forwarded" is the parent's terminal status when it gets
  // forwarded to a second workspace user; the FE pairs this with a
  // following `approval_forwarded` event to render the inline pill.
  // P1-A re-scoped — "suggest_edit" is the parent's terminal status when
  // an approver suggests edits; the FE pairs this with a following
  // `approval_requested` event for the child row carrying `edited_payload`.
  status?:
    | "approved"
    | "rejected"
    | "answered"
    | "skipped"
    | "forwarded"
    | "suggest_edit"
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
  approval_id: ApprovalId; // child approval (the new pending row)
  chain_parent_approval_id: ApprovalId; // original (now resolved with status=forwarded)
  approval_kind?: "mcp_tool" | "ask_a_question" | string;
  forwarded_by_user_id: UserId;
  forwarded_to_user_id: UserId;
  forwarded_at: string;
  action_summary?: string;
  status?: "waiting" | string;
  message?: string;
  [key: string]: unknown;
}

// PR 4.4.6.4 — emitted when a user POSTs /v1/agent/approvals/{id}/undo
// within the 60s reversibility window. Same audit metadata round-trips
// here so run-stream subscribers don't need a follow-up fetch.
export interface ApprovalUndoRequestedPayload {
  approval_id: ApprovalId;
  approval_kind?: "mcp_tool" | string;
  decided_by_user_id: UserId;
  undo_requested_at: string;
  undo_expires_at: string;
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
  /** PR 4.4.6.4 — user requested undo within the 60s reversibility
   * window. Run-stream subscribers learn alongside the audit row. */
  approval_undo_requested: ApprovalUndoRequestedPayload;
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
  /** P7 — batched variant of `source_ingested`. */
  sources_ingested: SourcesIngestedPayload;
  citation_made: CitationMadePayload;
  draft_updated: DraftUpdatedPayload;
  /** PR A1 — context-compression note. Server-emitted when the
   * memory-compression hook redacts older context; FE renders an
   * inline `<NoteCard>`. Payload mirrors `CompressionEventRecord`
   * (before/after tokens, strategy, optional summary). */
  compression_note: CompressionNotePayload;
  /** PR A2 — parallel subagent fleet bookends. Children carry
   * `parent_fleet_id` for grouping. */
  subagent_fleet_started: SubagentFleetStartedPayload;
  subagent_fleet_finished: SubagentFleetFinishedPayload;
  /** PR 3.2.5 Phase 3 — explicit pause/resume signals so the FE marks a
   * fleet row "paused" without inferring from the absence of
   * SUBAGENT_COMPLETED. Both events carry
   * `task_id == parent_task_id == supervisor_call_id`. */
  subagent_paused: SubagentPausedPayload;
  subagent_resumed: SubagentResumedPayload;
  /** Phase 6B — emitted when the tier-2 render-adapter generator produces
   * a complete `SaaSRendererAdapter` source string. The desktop's tier-2
   * lifecycle (6C) subscribes via SSE, persists `adapter_source` to
   * `{userData}/adapters/{scheme}-v{schema_version}.js`, and hands it to
   * the local quality gate (6D). */
  adapter_generated: AdapterGeneratedPayload;
  /** Generative-UI (PRD-01) — emitted when the async spec generator produces a
   * validated `SurfaceSpec` for a `(server, tool, output_shape)`. The projector
   * merges `spec` into `surfaceState[surface_uri]` so the next render upgrades
   * in place from tier-3 to the archetype view (plan D4). */
  surface_spec_generated: SurfaceSpecGeneratedPayload;
  /** AC5 slice 3b — host write-through pre-image snapshot. Emitted by the
   * workspace backend BEFORE an approved overwrite/edit mutates a granted
   * host file: the prior bytes are stored content-addressed and this event
   * carries the reference (op / mount / virtual path / object_sha256 / size —
   * never a host path) so the change is auditable and undoable. */
  workspace_snapshot_captured: WorkspaceSnapshotCapturedPayload;
}

export interface CompressionNotePayload {
  before_tokens: number;
  after_tokens: number;
  strategy: string;
  summary?: string | null;
  payload_refs?: Record<string, unknown>;
}

export interface SubagentFleetStartedPayload {
  fleet_id: string;
  title: string;
  sub?: string | null;
  agent_ids: readonly string[];
}

export interface SubagentFleetFinishedPayload {
  fleet_id: string;
  elapsed?: string | null;
}

/** PR 3.2.5 Phase 3 — `subagent_paused` payload. Emitted by the worker
 *  when an APPROVAL_REQUESTED / MCP_AUTH_REQUIRED / ASK_A_QUESTION
 *  interrupt fires INSIDE a subagent (i.e. when `parent_task_id`
 *  resolves to the subagent's supervisor `task` call_id). The FE
 *  reducer flips the matching `SubagentEntry.status` to `paused` so
 *  fleet rows + pane cards render the amber/paused visual without
 *  inferring from the absence of SUBAGENT_COMPLETED. */
export interface SubagentPausedPayload {
  task_id: string;
  /** What kind of interrupt paused it; the FE picks the right copy /
   *  icon. Mirrors the variants the worker already supports. */
  reason: "approval" | "mcp_auth" | "ask_a_question";
  /** The event_id of the underlying interrupt event (for cross-linking
   *  in the FE: clicking the paused row jumps to the interrupt card). */
  source_event_id?: string | null;
}

/** PR 3.2.5 Phase 3 — `subagent_resumed` payload. Emitted before any
 *  further activity from the resumed subagent so the FE flips state
 *  back to `running` BEFORE the next progress event. */
export interface SubagentResumedPayload {
  task_id: string;
  /** Outcome of the gating interrupt that paused the subagent. Drives
   *  per-row copy (e.g. "Resumed (rejected)" vs "Resumed (approved)").
   *  Optional: the resolution path may have no semantic decision (e.g.
   *  a future cancel-clear path) — the FE flips paused → running on
   *  the event regardless. */
  reason?: "approved" | "rejected";
  /** approval_id of the resolved approval / auth / question; useful for
   *  cross-linking the row back to the original gating card on the
   *  thread. */
  approval_id?: string;
  /** The event_id of the resolution event for cross-linking. */
  source_event_id?: string | null;
}

/** Phase 6B — constrained layout templates the agent code-gen capability
 *  is permitted to emit. Mirrors `LayoutTemplate` in
 *  `services/ai-backend/.../render_adapter_generator/models.py`. */
export type AdapterLayoutTemplate =
  | "form"
  | "table"
  | "kanban"
  | "definition-list";

/** Phase 6B — payload of the `adapter_generated` run event. Carries the
 *  complete TypeScript source for one tier-2 `SaaSRendererAdapter` that
 *  the desktop persists and installs through its tier-2 lifecycle. */
export interface AdapterGeneratedPayload {
  scheme: string;
  layout: AdapterLayoutTemplate;
  schema_version: number;
  adapter_source: string;
  generated_at: string;
  generator_model: string;
}

// ---------------------------------------------------------------------------
// Generative-UI SurfaceSpec contract (PRD-01). Mirrors the JSON Schema SSOT at
// `packages/service-contracts/src/copilot_service_contracts/surface_spec.schema.json`
// and the pydantic model in
// `services/ai-backend/src/agent_runtime/capabilities/surfaces/spec_models.py`.
// A cross-language parity test pins the pydantic model to the schema; keep this
// mirror in step with both. The schema has zero side-effectful members — no
// handlers, no free-form URLs (only `url_path` into payload data, host-sanitised
// at render), no templates — which is the injection blast-radius bound (D9).
// ---------------------------------------------------------------------------

/** The render family a `SurfaceSpec` binds to (v1). A host may implement a
 * subset; an unknown archetype renders the tier-3 generic fallback, never an
 * error. */
export type SurfaceArchetype =
  | "record"
  | "table"
  | "message"
  | "doc"
  | "board"
  | "event"
  | "timeline"
  | "dashboard"
  | "file"
  | "form";

/** Runtime SSOT tuple for the archetype union — used by `isSurfaceSpec` /
 * `isSurfaceEnvelope` and mirrors `SURFACE_ARCHETYPES` in service-contracts.
 * Order matches the schema `$defs.archetype` enum. */
export const SURFACE_ARCHETYPES = [
  "record",
  "table",
  "message",
  "doc",
  "board",
  "event",
  "timeline",
  "dashboard",
  "file",
  "form",
] as const satisfies readonly SurfaceArchetype[];

/** Purely visual presentation hint the renderer applies to a value. */
export type SurfaceFieldFormat =
  | "text"
  | "number"
  | "currency"
  | "datetime"
  | "badge"
  | "user";

/** Horizontal alignment for a table/board column. */
export type SurfaceColumnAlign = "start" | "end";

/** The connector server + tool whose output shape a spec maps. */
export interface SurfaceSource {
  server: string;
  tool: string;
}

/** A label/value pair for record | message | doc archetypes. */
export interface SurfaceField {
  label: string;
  path: string;
  format?: SurfaceFieldFormat;
}

/** A column definition for table | board archetypes. */
export interface SurfaceColumn {
  label: string;
  path: string;
  format?: SurfaceFieldFormat;
  align?: SurfaceColumnAlign;
}

/** A single outbound link. `url_path` resolves into payload data and is
 * host-sanitised at render — there are no free-form URLs. */
export interface SurfaceLink {
  label: string;
  url_path: string;
}

/** A schema-validated JSON document binding a tool's output shape onto an
 * archetype's slots. `spec_version` is frozen at 1. Every `*_path` is a dotted
 * accessor (identifier segments + array indices only, e.g. `a.b.0.c`). */
export interface SurfaceSpec {
  spec_version: 1;
  archetype: SurfaceArchetype;
  source: SurfaceSource;
  title_path: string;
  subtitle_path?: string;
  fields?: readonly SurfaceField[];
  columns?: readonly SurfaceColumn[];
  items_path?: string;
  group_by_path?: string;
  link?: SurfaceLink;
}

/** One proposed field change carried inside a surface diff. Structurally
 * compatible with the chat-surface `GenericFieldChange`. */
export interface SurfaceFieldChange {
  field: string;
  old?: unknown;
  new?: unknown;
}

/** The rendered state of a surface. `spec` absent ⇒ the host renders the tier-3
 * generic view; a spec may arrive later via `surface_spec_generated` and be
 * merged by `surface_uri`. `data` is untrusted tool output. */
export interface SurfaceState {
  spec?: SurfaceSpec;
  data: unknown;
}

/** A proposed change to a surface, ridden by approval flows (PRD-09). */
export interface SurfaceDiff {
  spec?: SurfaceSpec;
  changes: readonly SurfaceFieldChange[];
}

/** What rides inside event payloads under the `surface` key. `surface_uri`
 * grammar: `<archetype>://<server-slug>/<tool-or-resource>/<id>`. */
export interface SurfaceEnvelope {
  surface_uri: string;
  archetype: SurfaceArchetype;
  state: SurfaceState;
  diff?: SurfaceDiff;
}

/** Payload of the `surface_spec_generated` run event. Mirrors the projector
 * allow-list in `runtime_api/schemas/events.py`. */
export interface SurfaceSpecGeneratedPayload {
  surface_uri: string;
  archetype: SurfaceArchetype;
  spec: SurfaceSpec;
  spec_version: number;
  generator_model: string;
  skill_version: string;
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
/** AC5 slice 3b — reference to a host file's pre-image, captured before an
 * approved overwrite/edit. `path` is the route-relative virtual path
 * (`/<mount>/<relative>`); no host-absolute path is ever present. */
export interface WorkspaceSnapshotCapturedPayload {
  op: "overwrite" | "edit";
  mount: string;
  path: string;
  object_sha256: string;
  size: number;
  [key: string]: unknown;
}

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
  /** Generative-UI (PRD-01) — the surface projection for this draft, when the
   * backend resolved one (the draft/email path in plan D3). Absent ⇒ tier-3. */
  surface?: SurfaceEnvelope;
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
export type BudgetEnforcement = "soft" | "hard";
export type BudgetStatus = "active" | "disabled";

/**
 * Mirror of `BudgetMeRow` (`services/ai-backend/src/runtime_api/schemas/budgets.py`).
 * One budget that currently applies to the caller, with computed remaining headroom.
 */
export interface BudgetMeRow {
  id: string;
  scope: BudgetScope;
  period: BudgetPeriod;
  enforcement: BudgetEnforcement;
  status: BudgetStatus;
  limit_micro_usd: number | null;
  limit_tokens: number | null;
  current_micro_usd: number;
  current_tokens: number;
  remaining_micro_usd: number | null;
  remaining_tokens: number | null;
  period_start: string;
  period_end: string;
}

/** Mirror of `BudgetMeResponse`. Response for `GET /v1/budgets/me`. */
export interface BudgetMeResponse {
  currency: "USD";
  budgets: BudgetMeRow[];
}

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

/**
 * Composer Tools popover (`GET /v1/mcp/tools`).
 *
 * The endpoint aggregates two stores into one sectioned listing for the
 * composer: user-installed **skill** bundles and **MCP** server tools. The
 * `kind` discriminator is the single source of truth the UI uses to split
 * the popover into its Skills and MCPs sections — without it, the Skills
 * section is empty and skills and MCPs render in one undifferentiated list.
 *
 * NB: this surface is the popover-only aggregator, NOT the canonical Tools
 * destination (Phase 10). The canonical `Tool` / `ToolKind` /
 * `ToolListResponse` shapes live in `./tools.ts`; this legacy shape was
 * renamed to `ComposerTool*` so the Phase 10 wire shape can claim the
 * unqualified names.
 */
export type ComposerToolKind = "skill" | "mcp";

export interface ComposerToolDescriptor {
  /** Stable id used as the selection key and React list key. */
  name: string;
  /** Human-readable name rendered as the row label. */
  label: string;
  /** Optional one-line subtitle rendered under the label. */
  description?: string;
  /**
   * Whether this descriptor came from the user's installed skill bundles or
   * from a registered MCP server. Drives the Skills / MCPs section split.
   */
  kind: ComposerToolKind;
}

export interface ComposerToolListResponse {
  tools: ComposerToolDescriptor[];
}

// PR 1.5 — Workspace pane data feeds.
// Read-only archive contracts that complement the live SUBAGENT_* and
// `source_ingested` events on the SSE stream. The shape mirrors
// `services/ai-backend/src/runtime_api/schemas/workspace.py`.

export type SubagentLifecycleStatus =
  | "queued"
  | "running"
  // PR 3.2.5 Phase 3 — set by `subagentReducer` when a `subagent_paused`
  // event arrives (the worker emits one whenever an
  // APPROVAL_REQUESTED / MCP_AUTH_REQUIRED / ASK_A_QUESTION interrupt
  // resolves to a non-null `parent_task_id`). Live-stream-only: the
  // archive read at `GET .../subagents` projects from the terminal-or-
  // running `runtime_async_tasks.status` and never emits "paused".
  | "paused"
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
  /** PR 3.2.7 — set live (FE-only projection) when status === "paused"; the
   *  reducer copies these fields from the most recent `subagent_paused`
   *  payload so the row / card can render reason-specific copy without
   *  rescanning the event log. Cleared on resume / terminal. The archive
   *  read never returns them — they're additive optional FE state. */
  pause_reason?: "approval" | "mcp_auth" | "ask_a_question" | null;
  pause_source_event_id?: string | null;
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

// ----- PR 6.1: Conversation sharing -----------------------------------------
//
// Mirrors ``services/ai-backend/src/runtime_api/schemas/shares.py``. The
// server is the source of truth; these shapes are what ``backend-facade``
// returns. The plaintext ``share_token`` rides only on the create response
// — the server stores ``sha256(plaintext)`` and never re-emits it.

export type ShareViewAccess = "workspace" | "specific";

export interface ConversationShare {
  share_id: string;
  share_token_prefix: string | null;
  view_access: ShareViewAccess;
  recipient_user_ids: string[];
  sources_visible_to_viewer: boolean;
  snapshot_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  created_by_user_id: string;
  created_at: string;
  view_count?: number;
}

export interface CreateShareRequest {
  view_access: ShareViewAccess;
  recipient_user_ids?: string[];
  sources_visible_to_viewer: boolean;
  expires_at?: string | null;
  include_link: boolean;
}

export interface CreateShareResponse extends ConversationShare {
  /** Plaintext bearer token — returned exactly ONCE. Never persisted client-side. */
  share_token: string;
  share_url: string;
}

export interface ListSharesResponse {
  shares: ConversationShare[];
}

export interface UpdateShareRequest {
  sources_visible_to_viewer?: boolean | null;
  expires_at?: string | null;
  recipient_user_ids?: string[] | null;
  /** When true and ``expires_at`` is omitted, clears the existing expiry. */
  clear_expires_at?: boolean;
}

export interface SharedByUser {
  user_id: string;
  display_name?: string | null;
}

export interface SharedConversationSummary {
  share_id: string;
  view_access: ShareViewAccess;
  sources_visible_to_viewer: boolean;
  snapshot_at: string;
  shared_by: SharedByUser;
}

export type RecipientPreviewReason =
  | "ok"
  | "revoked"
  | "expired"
  | "foreign_org"
  | "not_recipient"
  | "share_not_found";

export interface RecipientPreview {
  share: SharedConversationSummary;
  can_view: boolean;
  reason: RecipientPreviewReason;
}

export interface SharedConversationView {
  share: SharedConversationSummary;
  conversation: Conversation;
  messages: Message[];
  events_by_run_id: Record<string, RuntimeEventEnvelope[]>;
  sources: SourceEntry[];
  drafts: Draft[];
  subagents: SubagentEntry[];
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
  // PR 4.4.7 Phase 2 (Slice C) — ``auth_url`` / ``expires_at`` are no
  // longer required. The blocking auth gate (PR 3.3) still always
  // sets them, but the catalog-suggestion variant emits an empty
  // string (which the runtime payload projector strips before the
  // wire) because no ``mcp_servers`` row exists yet to issue an auth
  // URL against. Accepting them as optional means the chat reducer
  // projects the catalog-only event into a discovery card instead of
  // silently dropping it.
  return (
    typeof payload.server_id === "string" &&
    typeof payload.display_name === "string"
  );
}

// Generative-UI (PRD-01) — structural guards following the
// `isRuntimeEventEnvelope` pattern. They check the required shape only; the
// authoritative validator is the ai-backend `validate_surface_spec`.
export function isSurfaceArchetype(value: unknown): value is SurfaceArchetype {
  return (
    typeof value === "string" &&
    (SURFACE_ARCHETYPES as readonly string[]).includes(value)
  );
}

export function isSurfaceSpec(value: unknown): value is SurfaceSpec {
  if (!isPlainRecord(value)) {
    return false;
  }
  const source = value.source;
  return (
    value.spec_version === 1 &&
    isSurfaceArchetype(value.archetype) &&
    isPlainRecord(source) &&
    typeof source.server === "string" &&
    typeof source.tool === "string" &&
    typeof value.title_path === "string"
  );
}

export function isSurfaceEnvelope(value: unknown): value is SurfaceEnvelope {
  if (!isPlainRecord(value)) {
    return false;
  }
  const state = value.state;
  if (!isPlainRecord(state) || !("data" in state)) {
    return false;
  }
  if (state.spec !== undefined && !isSurfaceSpec(state.spec)) {
    return false;
  }
  return (
    typeof value.surface_uri === "string" && isSurfaceArchetype(value.archetype)
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

// P7 — type guard for the batched variant. The empty-list case is
// considered well-formed (the projector emits `{ citations: [] }` when
// the upstream payload is malformed) so reducers can no-op safely.
export function isSourcesIngestedPayload(
  payload: unknown,
): payload is SourcesIngestedPayload {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return false;
  }
  const candidate = (payload as Record<string, unknown>).citations;
  if (!Array.isArray(candidate)) {
    return false;
  }
  return candidate.every(isCitationSourceRef);
}

export function isCitationLink(value: unknown): value is CitationLink {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  // ``source_tool_call_id`` is intentionally lenient. Two cases need
  // it absent: events persisted before the projector was taught to
  // default the field to ``""``, and resolver emissions for a
  // hallucinated ordinal that the allocator never bound to a tool
  // call. Treat any of (missing | null | string) as valid; the chip
  // renders muted-but-numbered when the call_id is empty.
  const callIdField = candidate.source_tool_call_id;
  const callIdValid =
    callIdField === undefined ||
    callIdField === null ||
    typeof callIdField === "string";
  return (
    typeof candidate.conversation_ordinal === "number" &&
    Number.isInteger(candidate.conversation_ordinal) &&
    candidate.conversation_ordinal > 0 &&
    typeof candidate.message_id === "string" &&
    candidate.message_id.length > 0 &&
    typeof candidate.prose_offset === "number" &&
    Number.isInteger(candidate.prose_offset) &&
    candidate.prose_offset >= 0 &&
    typeof candidate.prose_length === "number" &&
    Number.isInteger(candidate.prose_length) &&
    candidate.prose_length > 0 &&
    callIdValid
  );
}

export function isCitationMadePayload(
  payload: unknown,
): payload is CitationMadePayload {
  if (
    payload === null ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return false;
  }
  return isCitationLink((payload as Record<string, unknown>).link);
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
  /**
   * Either a remote URL or an inline `data:image/<png|jpeg|webp>;base64,…`
   * (PR 8.2 — inline avatar upload via the existing column; ≤ 200 KB
   * after FE resize). The FE renders both the same way (`<img src=…>`).
   */
  avatar_url: string | null;
  /**
   * PR 8.2 — short free-text bio surfaced in the profile card and the
   * member directory. Server-capped at 600 chars; whitespace-only inputs
   * are normalised to `null`.
   */
  bio: string | null;
  updated_at: string;
  /**
   * Honest identity (Issues 3 + 4). A SIWE (wallet) account has no real email —
   * `email` is the undeliverable `<address>@wallet.invalid` placeholder, which
   * `email_is_placeholder` flags so surfaces render the wallet anchor instead.
   * `wallet_address` is the EIP-55 checksummed address; `chain_id`/`chain_name`
   * the chain it linked on. Absent/`null`/`false` for an email account.
   * Optional so older servers stay compatible (non-breaking additions).
   */
  email_is_placeholder?: boolean;
  wallet_address?: string | null;
  chain_id?: number | null;
  chain_name?: string | null;
  /** Durable auth origin — drives the "Signed in with" indicator. */
  auth_method?: WorkspaceMemberSource | string | null;
  /**
   * Account-linking (PRD FR-L4): every sign-in identity linked to this
   * account — the "Linked accounts" panel's data. The singular
   * `wallet_address` fields above remain "the" profile wallet (first-linked)
   * for existing consumers. Optional so older servers stay compatible.
   */
  linked_identities?: readonly LinkedIdentity[];
}

/**
 * One linked sign-in identity on the account. `kind` discriminates: `wallet`
 * rows carry `address`/`chain_*`; `oidc` rows carry `provider` (e.g.
 * `google`) + the email seen at link time. `id` is the row id a future
 * unlink targets.
 */
export interface LinkedIdentity {
  kind: "wallet" | "oidc" | string;
  id: string;
  provider?: string | null;
  email?: string | null;
  /** Per-identity IdP `email_verified` assertion at link time (null = unasserted). */
  email_verified?: boolean | null;
  address?: string | null;
  chain_id?: number | null;
  chain_name?: string | null;
  linked_at: string;
}

/**
 * Request body for `POST /v1/me/identities/wallet` (account-linking FR-L1).
 * The SIWE `message` + `signature` prove control of the wallet being
 * linked; identity of the survivor account comes from the bearer, never
 * the body. `confirm_merge` is the FR-U2 explicit consent that a wallet
 * already owned by ANOTHER account should merge that account into this one
 * — never defaulted to true; the client sets it only after the user
 * confirms the merge dialog.
 */
export interface LinkWalletRequest {
  message: string;
  signature: string;
  confirm_merge?: boolean;
}

/**
 * Result of `POST /v1/me/identities/wallet`. `status` is `linked` for a
 * fresh bind, `already_linked` for the idempotent no-op (FR-L6), and
 * `merged` when a confirmed merge absorbed the wallet's prior account
 * (FR-M1). Never mints a session — the caller already holds a bearer.
 */
export interface LinkWalletResult {
  status: "linked" | "already_linked" | "merged" | string;
  wallet_id: string;
  address: string;
  chain_id: number;
  chain_name: string;
}

/**
 * Result of `POST /v1/me/identities/google/link/start` (FR-L2). `auth_url`
 * is the IdP consent URL to send the browser to; the flow completes on the
 * public `/v1/auth/oidc/callback`, whose link intent is recovered
 * server-side from the consumed `state` row.
 */
export interface LinkGoogleStartResult {
  auth_url: string;
  state: string;
}

/**
 * Result of a Google LINK flow returned by `/v1/auth/oidc/callback` when
 * the flow was link-bound (FR-L2). Distinguished from the sign-in handoff
 * (`OidcCallbackResult`) by `linked: true` — a link never mints a session.
 * `email_upgraded` is true when a wallet account's `@wallet.invalid`
 * placeholder was replaced by the verified Google address.
 */
export interface OidcLinkCallbackResult {
  linked: true;
  status: "linked" | "already_linked" | string;
  user_id: string;
  provider_id: string;
  email?: string | null;
  email_upgraded?: boolean;
  return_to?: string | null;
}

/**
 * Structured `detail` object carried by account-linking error responses
 * (409 for a merge trigger or the last-sign-in-method guard; 502 for a
 * merge that must be retried). `code` is the stable discriminator the
 * client branches on; `safe_message` is a user-safe explanation that never
 * leaks the other account's identifiers.
 */
export interface LinkErrorDetail {
  code:
    | "merge_required"
    | "last_sign_in_method"
    | "merge_runtime_failed"
    | "merge_not_allowed"
    | string;
  safe_message: string;
}

/** Machine-readable `code` values on a {@link LinkErrorDetail}. */
export const LINK_ERROR_CODES = {
  mergeRequired: "merge_required",
  lastSignInMethod: "last_sign_in_method",
  mergeRuntimeFailed: "merge_runtime_failed",
  mergeNotAllowed: "merge_not_allowed",
} as const;

export type UserProfileTheme = "system" | "light" | "dark" | "slate";

/** Mirrors `ACCENT_SCHEMES` in `@0x-copilot/design-system`. */
export type UserProfileAccent =
  | "sky"
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

/**
 * PR 4.4.7 Phase 2 (Slice A) — per-user override for the catalog's
 * ``discoverable`` defaults. ``overrides[slug] === true`` forces the
 * catalog entry to be suggestible for this user, ``false`` mutes it,
 * absent slug = inherit the catalog entry's ``discoverable`` flag.
 *
 * Phase 1 stored this in ``localStorage`` per-device. Slice A migrates
 * the hook to read/write here so the toggle survives across browsers.
 * Slice B reads it server-side at run-create to drive agent
 * suggestions; until then the field has no runtime effect.
 */
export interface DiscoverableConnectorsPreferences {
  overrides: Record<string, boolean>;
}

export interface UserPreferences {
  appearance: AppearancePreferences;
  shortcuts: ShortcutsPreferences;
  notifications: NotificationsPreferences;
  discoverable_connectors: DiscoverableConnectorsPreferences;
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
  /** PR 8.2 — `null` (or whitespace-only) clears. Server-capped at 600 chars. */
  bio?: string | null;
};

/**
 * PR 8.2 — request body for the Settings UI's TOTP enrollment route.
 * The internal MFA contract carries org_id / user_id in the body; the
 * caller-scoped wrapper at `/v1/me/mfa/*` derives those from the
 * verified session, so the public body only carries the device label.
 */
export interface TotpEnrollRequestBody {
  display_name: string;
}

/**
 * PR 8.3 — admin editor for the workspace's `identity_policies` MFA
 * fields. The same `mfa_required` row already gates sign-in via the
 * OIDC mint, so a flip takes effect on the next login.
 */
export interface WorkspaceMfaPolicy {
  mfa_required: boolean;
  step_up_window_seconds: number;
  /** Empty when the row hasn't been written yet (defaults are surfaced). */
  updated_at: string;
}

export interface UpdateWorkspaceMfaPolicyRequest {
  mfa_required?: boolean;
  step_up_window_seconds?: number;
}

/**
 * PR 8.3 — WebAuthn enrollment ceremony.
 *
 * `options` is a `PublicKeyCredentialCreationOptionsJSON` — every binary
 * field (`challenge`, `user.id`, `excludeCredentials[].id`) is base64-
 * url. The FE decodes these into `Uint8Array` before passing to the
 * navigator, then re-encodes the attestation result the same way.
 */
export interface MfaWebAuthnStartRequestBody {
  display_name?: string;
  rp_id: string;
  rp_name: string;
  user_name: string;
  user_display_name?: string | null;
}

export interface MfaWebAuthnStartResponse {
  factor_id: string;
  challenge_id: string;
  options: Record<string, unknown>;
}

export interface MfaWebAuthnFinishRequestBody {
  factor_id: string;
  challenge_id: string;
  rp_id: string;
  expected_origin: string;
  attestation: Record<string, unknown>;
}

export interface MfaWebAuthnFinishResponse {
  credential_id: string;
}

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
  /** PR 4.4.7 Phase 2 (Slice A) — per-user catalog discoverable
   *  overrides. Sending an empty ``overrides`` object leaves prior
   *  state untouched (deep-merge). To clear a single slug, send
   *  ``{ overrides: { <slug>: <catalog default> } }`` — the merge
   *  semantics here treat ``true``/``false`` as wholesale replace
   *  per slug (no ``null`` clearing in this slice). */
  discoverable_connectors?: { overrides?: Record<string, boolean> };
}

// ---------------------------------------------------------------------------
// Login email-first / magic-link / workspace picker (PR 5.1)
// ---------------------------------------------------------------------------

/** UI branch the discovery response tells the frontend to render.
 *
 * `personal` and `magic_link` produce identical UI (the magic-link CTA);
 * the distinction is forensic. `unknown` means the deploy disables the
 * magic-link path (bank-profile) and the user is shown an SSO-required
 * message. */
export type AuthDiscoverKind = "sso" | "personal" | "magic_link" | "unknown";

export type AuthDiscoverProviderKind = AuthProviderKind | "magic_link" | null;

export interface AuthDiscoverRequest {
  email: string;
}

export interface AuthDiscoverResponse {
  kind: AuthDiscoverKind;
  domain: string | null;
  org_id: string | null;
  org_display_name: string | null;
  org_logo_url: string | null;
  member_count: number | null;
  provider_id: string | null;
  provider_kind: AuthDiscoverProviderKind;
  provider_display_name: string | null;
  sso_enforced: boolean;
  magic_link_supported: boolean;
  message: string | null;
}

export interface MagicLinkStartRequest {
  email: string;
  return_to?: string;
}

export interface MagicLinkStartResponse {
  status: "queued";
  expires_in_seconds: number;
}

export type MagicLinkCallbackOutcome =
  | "session_minted"
  | "workspace_pick_required";

export interface WorkspaceCandidate {
  org_id: string;
  display_name: string;
  logo_url: string | null;
  role: string;
  member_count: number;
  last_active_at: string | null;
}

export interface MagicLinkCallbackResponse {
  outcome: MagicLinkCallbackOutcome;
  user_id: string;
  // session_minted branch:
  bearer_token?: string;
  session_id?: string;
  org_id?: string;
  requires_mfa?: boolean;
  return_to?: string | null;
  expires_at?: string;
  // workspace_pick_required branch:
  pick_token?: string;
  expires_in_seconds?: number;
  workspaces?: WorkspaceCandidate[];
}

export interface SessionSelectRequest {
  pick_token: string;
  org_id: string;
}

export interface SessionSelectResponse {
  bearer_token: string;
  session_id: string;
  user_id: string;
  org_id: string;
  requires_mfa: boolean;
  expires_at: string;
}

// ---------------------------------------------------------------------------
// PR 7.1 — admin audit log query (Settings → Members → Audit log)
// ---------------------------------------------------------------------------

export type AuditStream =
  | "mcp_audit_events"
  | "skill_audit_events"
  | "identity_audit_events"
  | "deploy_audit_events";

export type AuditOutcome = "success" | "failure" | "denied";
export type AuditActorKind = "user" | "ci" | "system";

export interface AuditChainView {
  /** Per-stream monotonic sequence; null for streams that don't carry one. */
  seq: number | null;
  prev_hash: string | null;
  signature: string | null;
  key_version: number | null;
}

export interface AuditEvent {
  stream: AuditStream;
  seq: number | null;
  audit_id: string;
  org_id: string;
  actor_user_id: string | null;
  actor_kind: AuditActorKind;
  subject_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: AuditOutcome;
  metadata: Record<string, unknown>;
  chain: AuditChainView;
  /** RFC 3339 timestamp (UTC). */
  created_at: string;
}

export interface ListAuditEventsRequest {
  /** Action substring filter (server compares as a prefix match). */
  action?: string;
  actor_user_id?: string;
  resource_type?: string;
  /** RFC 3339 inclusive lower bound. */
  since?: string;
  /** RFC 3339 inclusive upper bound. */
  until?: string;
  /** Opaque cursor returned by the previous page; absent on first page. */
  cursor?: string;
  /** 1..200, server clamps. */
  limit?: number;
}

export interface ListAuditEventsResponse {
  rows: AuditEvent[];
  /** Cursor to pass back as ``cursor=`` for the next page; null at end. */
  next_cursor: string | null;
  has_more: boolean;
  /** Streams whose underlying store was unavailable on this read. */
  degraded_streams: string[];
}

// =====================================================================
// PR B1 — tool-use policy (workspace default + per-user override).
// =====================================================================

export type ToolPolicyKind = "read" | "write" | "destructive";
export type ToolPolicyMode = "auto" | "ask" | "require" | "block";

export interface ToolUsePolicyEntry {
  kind: ToolPolicyKind;
  mode: ToolPolicyMode;
  updated_at: string;
  updated_by_user_id: string | null;
}

/** Workspace-level (admin) and per-user policy share the same shape;
 * the route discriminates the scope. */
export interface ToolUsePolicyResponse {
  scope: "workspace" | "user";
  org_id: string;
  user_id: string | null;
  policies: ToolUsePolicyEntry[];
}

export interface UpdateToolUsePolicyRequest {
  policies: Array<{ kind: ToolPolicyKind; mode: ToolPolicyMode }>;
}

// =====================================================================
// PR B2 — privacy & data settings (workspace default + per-user override).
// =====================================================================

export type DataResidencyRegion = "us-east-1" | "eu-west-1" | "ap-northeast-1";

export interface PrivacySettingsResponse {
  scope: "workspace" | "user";
  org_id: string;
  user_id: string | null;
  training_opt_out: boolean;
  region: DataResidencyRegion | null;
  retention_days: number | null;
  share_metadata: boolean;
  memory_enabled: boolean;
  updated_at: string;
}

export interface UpdatePrivacySettingsRequest {
  training_opt_out?: boolean;
  region?: DataResidencyRegion | null;
  retention_days?: number | null;
  share_metadata?: boolean;
  memory_enabled?: boolean;
}

// =====================================================================
// PR B3 — personal API keys (atlas_pk_… bearer for CI / scripts).
// =====================================================================

export type ApiKeyKind = "personal" | "workspace";

export interface ApiKeySummary {
  id: string;
  label: string;
  key_prefix: string;
  scopes: readonly string[];
  last_used_at: string | null;
  created_at: string;
  rotated_from_id: string | null;
  /** PR 8.3 — drives the FE tab strip; legacy rows default to `personal`. */
  kind: ApiKeyKind;
}

export interface ApiKeyListResponse {
  keys: ApiKeySummary[];
}

export interface CreateApiKeyRequest {
  label: string;
  scopes?: readonly string[];
}

export interface CreateApiKeyResponse {
  key: ApiKeySummary;
  /** Plaintext secret. Returned ONCE — the server stores only the hash. */
  plaintext: string;
}

// =====================================================================
// PR B4 — notification preferences v2 + quiet hours.
// =====================================================================

/** PR B4 channel set. Distinct from the legacy `NotificationChannel`
 * (which is "email" | "slack" | "desktop"); the v2 dispatcher targets
 * in-app / email / push instead. */
export type NotificationChannelV2 = "in_app" | "email" | "push";
export type NotificationEventKind =
  | "long_task_finished"
  | "approval_requested"
  | "mention"
  | "connector_error"
  | "weekly_digest"
  | "product_updates";

export interface NotificationPreferenceEntry {
  event_kind: NotificationEventKind;
  channel: NotificationChannelV2;
  enabled: boolean;
}

export interface NotificationQuietHours {
  enabled: boolean;
  from_local: string; // HH:MM 24h
  to_local: string; // HH:MM 24h
  tz: string; // IANA tz id
}

export interface NotificationPreferencesResponse {
  user_id: string;
  preferences: NotificationPreferenceEntry[];
  quiet_hours: NotificationQuietHours;
}

export interface UpdateNotificationPreferencesRequest {
  preferences?: NotificationPreferenceEntry[];
  quiet_hours?: NotificationQuietHours;
}

// =====================================================================
// PR B5 — unified audit-log read-model (workspace admin).
// =====================================================================

export type AuditEventKind =
  | "identity"
  | "mcp"
  | "skill"
  | "deploy"
  | "approval"
  | "connector"
  | "tool_policy"
  | "api_key"
  | "privacy"
  | "share";

export interface AuditEventRow {
  event_id: string;
  event_kind: AuditEventKind;
  action: string;
  actor_user_id: string | null;
  subject: string | null;
  metadata: Record<string, unknown>;
  occurred_at: string;
  chain_seq: number;
}

export interface AuditEventListResponse {
  events: AuditEventRow[];
  next_cursor: string | null;
  has_more: boolean;
}

// =====================================================================
// Phase 7A — tier-2 adapter registry (community-shared SaaSRendererAdapters).
//
// Apps submit locally-generated adapters (`harvest`) that meet the
// §9.5.3 success criteria; admins review the queue and approve/reject.
// Approved adapters propagate to every tenant that has not opted out.
// =====================================================================

export type AdapterRegistryLayout =
  | "form"
  | "table"
  | "kanban"
  | "definition-list";

export type AdapterCandidateStatus =
  | "submitted"
  | "in-review"
  | "changes-requested"
  | "approved"
  | "rejected";

export type AdapterReviewAction = "approve" | "reject" | "request-changes";

export interface AdapterHarvestMetrics {
  zero_error_sessions: number;
  total_sessions: number;
  user_reported_issues?: number;
  generator_model?: string | null;
}

export interface AdapterCandidateSubmission {
  scheme: string;
  version: number;
  layout: AdapterRegistryLayout;
  source: string;
  harvest_metrics: AdapterHarvestMetrics;
}

export interface AdapterCandidate {
  candidate_id: string;
  tenant_id: string;
  submitter_user_id: string;
  scheme: string;
  version: number;
  layout: AdapterRegistryLayout;
  source: string;
  source_digest: string;
  harvest_metrics: AdapterHarvestMetrics;
  status: AdapterCandidateStatus;
  created_at: string;
  updated_at: string;
}

export interface AdapterCandidateListResponse {
  candidates: AdapterCandidate[];
}

export interface AdapterReviewDecision {
  action: AdapterReviewAction;
  notes?: string | null;
}

export interface PromotedAdapter {
  promoted_id: string;
  scheme: string;
  version: number;
  schema_version: number;
  layout: AdapterRegistryLayout;
  source: string;
  source_digest: string;
  /** Always `"community"` in Phase 7A; reserved for future provenance values. */
  origin: "community";
  promoted_at: string;
}

export interface PromotedAdaptersResponse {
  adapters: PromotedAdapter[];
}

export interface AdapterRegistryOptOutRequest {
  opted_out: boolean;
}

export interface AdapterRegistryOptOutResponse {
  tenant_id: string;
  opted_out: boolean;
  updated_at: string;
}

// === Phase 0.5 shared primitives ===
// Branded IDs (single declaration site — see ./brands.ts).
export type {
  AgentId,
  ApprovalId,
  ConnectorId,
  ConversationId,
  InboxItemId,
  LibraryDatasetId,
  LibraryEntityId,
  LibraryFileId,
  LibraryItemId,
  LibraryPageId,
  MeetingExternalId,
  MemoryItemId,
  ProjectId,
  ProjectTemplateId,
  RoutineId,
  RunId,
  SkillId,
  SubagentId,
  TenantId,
  TodoExtractionId,
  TodoId,
  TodoSeriesId,
  ToolId,
  ToolResultId,
  TriggerId,
  UserId,
} from "./brands";

// Cross-destination references and partial-failure section wrapper
// (single declaration site — see ./refs.ts).
export type { ItemKind, ItemRef, ItemRefSnapshot, SectionResult } from "./refs";
// === end Phase 0.5 ===

// === Phase 9 Home destination ===
// Morning-briefing aggregator response. Phase 9 redesign supersedes the
// Phase 2 7-section model: HomePinnedChat, HomeRecentRun, HomeFavoriteTool,
// HomeFocusItem, HomeUpcomingMeeting, HomeRunStatus, HomeResponse are
// retired. Section composers may ship as stubs until their upstream
// destinations land; the wire shape is stable from day one.
export type {
  HomeActivityKind,
  HomeActivityRow,
  HomeGreeting,
  HomePayload,
  InFlightProject,
  MeetingTimelineEntry,
  QuickAction,
  QuickActionTarget,
  QuickActionTargetKind,
  RoutineFireTimelineEntry,
  RunScheduledTimelineEntry,
  TimelineEntry,
  TimelineEntryBase,
  TimelineEntryKind,
  TimelineEntryStatus,
  TimeSegment,
  TodoDueTimelineEntry,
  TriageCounts,
  WhatsNewSection,
} from "./home";

// === Phase 9 Home SSE event envelope ===
// Live-updated activity feed for the LiveActivityRail. Mirrors the
// existing run-event SSE pattern:
// monotonic ``sequence_no`` per ``(org_id, user_id)`` channel; ``id:`` SSE
// field carries the sequence so browsers replay via ``Last-Event-ID`` header.
// Server also accepts ``?after_sequence=N`` query fallback.
//
// Wire framing (matches services/backend/src/backend_app/home/sse.py):
//   event: home_activity
//   id: 42
//   data: { "event_id": "...", "sequence_no": 42, ... }
export type HomeActivityEventType =
  | "activity_added"
  | "activity_updated"
  | "heartbeat";

import type { HomeActivityRow as _HomeActivityRow } from "./home";
export interface HomeActivityEvent {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: HomeActivityEventType;
  // ``row`` is present for ``activity_added``/``activity_updated``; absent for ``heartbeat``.
  readonly row?: _HomeActivityRow;
  readonly created_at: string;
}
// === end Phase 9 Home ===

// === Phase 3 Todos destination ===
// Canonical CRUD + extraction provenance + recurrence + one-level
// subtasks wire shape. Cross-audit §1.1 (ItemRef link payloads), §1.3
// (project-scoped ACL), §1.5 (multi-value OR filter axes), §9.6
// (Phase 3 deviations). Single declaration site: ./todos.ts.
export type {
  BulkTodoAction,
  BulkUpdateTodosRequest,
  BulkUpdateTodosResponse,
  CreateTodoRequest,
  Todo,
  TodoListResponse,
  TodoPriority,
  TodoRecurrence,
  TodoRecurrenceRule,
  TodoSource,
  TodoSourceKind,
  TodoStatus,
  UpdateTodoRequest,
} from "./todos";
// === end Phase 3 Todos ===

// === Phase 4 Inbox destination ===
// Canonical CRUD + state-machine + multi-link wire shape. Cross-audit
// §1.1 (links: ReadonlyArray<ItemRef> replaces ad-hoc string refs),
// §1.3 (project-scoped ACL — recipient writes, project-member reads,
// admin compliance reads, 404-not-403), §1.5 (multi-value OR filter
// axes), §9.1 (Inbox Q6 revised — inline by default; durable item
// only when user has not viewed thread within tenant-configurable
// window; priority filter dropped), §9.3 (reply-to-error routing).
// Single declaration site: ./inbox.ts.
export type {
  BulkInboxAction,
  BulkUpdateInboxItemsRequest,
  BulkUpdateInboxItemsResponse,
  InboxItem,
  InboxItemKind,
  InboxItemSender,
  InboxItemState,
  InboxListResponse,
  InboxUnreadCountResponse,
  UpdateInboxItemRequest,
} from "./inbox";

// SSE event envelope (P4-A3). Mirrors home SSE pattern. The ``item``
// field carries the canonical InboxItem from ./inbox.ts. Naming
// distinct from PR-1.4.1's ``InboxEventType``/``InboxEventEnvelope``
// (approval-pulse stream); the two converge in phase 4.5.
import type { InboxItem as _InboxItem } from "./inbox";
export type InboxStreamEventType = "item_added" | "item_updated" | "heartbeat";
export interface InboxStreamEnvelope {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: InboxStreamEventType;
  readonly item?: _InboxItem;
  readonly created_at: string;
}
// === end Phase 4 Inbox ===

// === Phase 5 Routines destination ===
// Canonical CRUD + state-machine + trigger array wire shape. Cross-audit
// §1.1 (ItemRef-typed code-routine refs), §1.3 (project-scoped ACL —
// owner writes, project-member reads, admin compliance reads,
// 404-not-403), §1.5 (multi-value OR filter axes), §2.1 (branded ids:
// RoutineId / AgentId), §2.4 (webhook trigger_id; rotating secret +
// IP allowlist live in the internal contract), §9.7 (14 binding
// decisions — manual_fire ACL override, no auto-resume, fire_once
// missed-fire default, 100 active routines per USER, agent_version_pin
// optional, code-routines wire shape now / executor in Wave 6). Single
// declaration site: ./routines.ts.
export type {
  CreateRoutineRequest,
  Routine,
  RoutineCodeRef,
  RoutineCronTrigger,
  RoutineEventTrigger,
  RoutineListResponse,
  RoutineManualFireScope,
  RoutineMissedFirePolicy,
  RoutinePauseReason,
  RoutinePermissions,
  RoutineStatus,
  RoutineTrigger,
  RoutineTriggerKind,
  RoutineWebhookTrigger,
  RunRoutineResponse,
  UpdateRoutineRequest,
} from "./routines";
// === end Phase 5 Routines ===

// === Phase 6 Projects destination ===
// Canonical CRUD + member management + ownership transfer + activity-stream
// wire shape. Cross-audit §1.1 (ItemRef incl. kind="project"), §1.3 (project-
// scoped ACL master rule — Projects ships the canonical resolver and every
// destination with project_id consumes it), §1.4 (audit context carries
// project_id), §1.5 (multi-value OR filter axes), §2.1 (branded ProjectId).
// Single declaration site: ./projects.ts.
export type {
  AddMemberRequest,
  ChangeRoleRequest,
  ConnectorSlug,
  CreateProjectRequest,
  ForkProjectTemplateRequest,
  LivenessDetail,
  LivenessDetailSource,
  LivenessReport,
  Project,
  ProjectActivity,
  ProjectActivityCounts,
  ProjectActivityListResponse,
  ProjectArchiveBlockedResponse,
  ProjectColorHue,
  ProjectIconEmoji,
  ProjectListResponse,
  ProjectMembership,
  ProjectMembershipListResponse,
  ProjectRole,
  ProjectStatus,
  ProjectStreamEnvelope,
  ProjectStreamEventType,
  ProjectSummary,
  ProjectTemplate,
  ProjectTemplateListResponse,
  ProjectTemplateSeededRoutine,
  ProjectTemplateSeededTodo,
  ProjectTemplateSnapshot,
  RemoveMemberRequest,
  SaveAsTemplateRequest,
  TransferOwnershipRequest,
  UpdateProjectRequest,
  UpdateProjectTemplateRequest,
} from "./projects";
// === end Phase 6 Projects ===

// === Phase 7 Library destination ===
// Canonical CRUD + kind-agnostic LibraryItem wire shape (file / page /
// dataset). Cross-audit §1.1 (ItemRef-resolvable source refs), §1.3
// (project-scoped ACL — owner writes, project-member reads, admin
// compliance reads, 404-not-403), §1.5 (multi-value OR filter axes),
// §2.1 (branded ids: LibraryFileId / LibraryPageId / LibraryDatasetId).
// Single declaration site: ./library.ts. P7-A1 ships the CRUD wire; the
// signed-URL upload + dataset ingest + search + preview/download
// payloads land additively in P7-A2 / P7-A3.
export type {
  LibraryDataset,
  LibraryDatasetColumnSpec,
  LibraryDatasetColumnType,
  LibraryDatasetFormat,
  LibraryFile,
  LibraryFileKind,
  LibraryIndexStatus,
  LibraryItem,
  LibraryItemKind,
  LibraryItemPatchRequest,
  LibraryListResponse,
  LibraryPage,
  LibraryPageCreateRequest,
  LibrarySearchCompleteEnvelope,
  LibrarySearchErrorEnvelope,
  LibrarySearchHit,
  LibrarySearchLegEnvelope,
  LibrarySearchMatchedIn,
  LibrarySearchRerankedEnvelope,
  LibrarySearchResponse,
  LibrarySearchStrategy,
  LibrarySearchStreamEnvelope,
  LibrarySource,
  LibrarySourceKind,
} from "./library";
// === end Phase 7 Library ===

// === Phase 10 Tools destination ===
// Canonical catalog + onboarding + audit-lens wire shape (mcp / openapi /
// builtin / code / skill kinds in one row). Cross-audit §1.1 (ItemRef
// branch "tool" already in the union), §1.3 (project-scoped ACL —
// owner-or-admin writes, project-member or owner reads, 404-not-403),
// §1.4 (audit context), §1.5 (multi-value OR filter axes), §2.1 (branded
// ToolId), §5.2 (SSE convention), §5.5 (TU-1 single-tracker invariant —
// usage projection over runtime_tool_invocations, never a parallel
// tracker). P10-A2 ships the backend catalog module + routes; P10-A3
// lands the code-routine sandbox executor; P10-A4 wires the facade.
// Single declaration site: ./tools.ts.
export type {
  CreateToolRequest,
  TestToolCallRequest,
  TestToolCallResponse,
  Tool,
  ToolCodeRef,
  ToolDetailResponse,
  ToolInvocation,
  ToolInvocationCallerKind,
  ToolInvocationErrorKind,
  ToolInvocationListResponse,
  ToolKind,
  ToolListResponse,
  ToolScope,
  ToolSkillPageRef,
  ToolStatus,
  ToolStreamEnvelope,
  ToolStreamEventType,
  ToolTransport,
  ToolTransportKind,
  ToolUsageProjection,
  ToolUsageResponse,
  UpdateToolRequest,
} from "./tools";
// === end Phase 10 Tools ===

// === Phase 11 Connectors destination ===
// Canonical wire shape for the Connectors destination: list / detail /
// scope / disconnect / refresh / audit / SSE + the Webhook management
// surface (Routines §9.7 Q6 HMAC-of-payload UX). Cross-audit §1.1 (ItemRef
// kind="connector"), §1.6 (status taxonomy), §5.2 (SSE convention). The
// storage is a denormalized read model over the existing MCP registration
// + token vault path — see connectors-prd §3.2 (no parallel registry).
// `ConnectorSlug` is re-exported from ./projects.ts (canonical site).
// Single declaration site: ./connectors.ts.
export type {
  Connector,
  ConnectorAccessMode,
  ConnectorAuditEntry,
  ConnectorAuditResponse,
  ConnectorAvailability,
  ConnectorCapabilitySummary,
  ConnectorCatalogEntry,
  ConnectorConsumers,
  ConnectorDetailResponse,
  ConnectorListResponse,
  ConnectorOAuthCallbackRequest,
  ConnectorScopeEntry,
  ConnectorStatus,
  ConnectorStreamEnvelope,
  ConnectorStreamEventType,
  CreateWebhookRequest,
  DisconnectConnectorResponse,
  PatchConnectorScopesRequest,
  PatchConnectorScopesResponse,
  PatchWebhookRequest,
  RefreshConnectorResponse,
  SetConnectorAccessModeRequest,
  SetConnectorAccessModeResponse,
  StartConnectorOAuthResponse,
  TestFireWebhookRequest,
  Webhook,
  WebhookCreateResponse,
  WebhookHmacAlgo,
  WebhookListResponse,
  WebhookRotateResponse,
  WebhookSecretStrategy,
  WebhookStatus,
  WebhookTestFireResponse,
} from "./connectors";
// Runtime SSOT tuple for the per-connector access mode union (desktop
// redesign, Phase 4). Value export so the 3-way segment + tests can
// enumerate the modes without redeclaring them.
export { CONNECTOR_ACCESS_MODES } from "./connectors";
// AC9 — Desktop MCP connector OAuth transport (desktop-only variant types).
// These do NOT touch the shared web OAuth shapes above; they live in a
// separate module so the shipped web redirect flow stays byte-identical while
// the desktop facade routes speak the richer loopback/deep-link transport.
export type {
  DesktopConnectorCallback,
  DesktopConnectorCatalogEntry,
  DesktopConnectorCatalogResponse,
  DesktopConnectorConnectionResult,
  DesktopConnectorOAuthCallbackRequest,
  DesktopDeepLinkCallback,
  DesktopLoopbackCallback,
  DesktopRequestedProductScope,
  DesktopStartConnectorOAuthRequest,
  DesktopStartConnectorOAuthResponse,
} from "./connectors-desktop";
export {
  DESKTOP_CONNECTOR_DEEP_LINK_URI,
  DESKTOP_CONNECTOR_LOOPBACK_PATH,
} from "./connectors-desktop";
// === end Phase 11 Connectors ===

// === Phase 12 Team destination ===
// Wire shape for the Team destination: list / detail / invite / role /
// offboarding + presence SSE. Built on the existing `users` +
// `tenant_memberships` tables (no new identity). Cross-audit §1.1
// (ItemRef kind="person" already in the canonical union), §1.3
// (`is_project_member` ACL for admin recent-activity filter), §1.5
// (multi-value OR filter axes), §5.2 (SSE convention). Single
// declaration site: ./team.ts.
export type {
  InviteRequest,
  OffboardingReassignment,
  OffboardingRequest,
  Person,
  PersonActivityEntry,
  PersonActivityFilterAxis,
  PersonDetailResponse,
  Presence,
  TeamListFilterAxis,
  TeamListResponse,
  TeamListSort,
  TeamRole,
  TeamStreamEnvelope,
  TeamStreamEventType,
  UpdateTeamRoleRequest,
} from "./team";
// === end Phase 12 Team ===

// === Phase 12 Memory destination ===
// Wire shape for the Memory destination: CRUD + proposals (auto-extraction
// accept/reject) + hybrid search + SSE. Embeddings reuse
// `library_embeddings` with `target_kind = "memory"` (no parallel vector
// table — sub-PRD §5.1). Cross-audit §1.1 (ItemRef kind="memory" already
// in the canonical union), §1.3 (project-scoped ACL via optional
// `project_id`), §1.5 (multi-value OR filter axes), §5.2 (SSE
// convention). Single declaration site: ./memory.ts.
export type {
  AcceptMemoryProposalRequest,
  CreateMemoryRequest,
  MemoryCreator,
  MemoryCreatorKind,
  MemoryItem,
  MemoryKind,
  MemoryListFilterAxis,
  MemoryListResponse,
  MemoryListSort,
  MemoryProposal,
  MemoryProposalDecisionStatus,
  MemoryProposalListResponse,
  MemoryScope,
  MemorySearchHit,
  MemorySearchResponse,
  MemoryStreamEnvelope,
  MemoryStreamEventType,
  UpdateMemoryRequest,
} from "./memory";
// === end Phase 12 Memory ===

// === Phase 12 ⌘K Palette ===
// Wire shape for the global command palette: single search endpoint +
// flat hit list with `kind` discriminator (navigation / entity / action
// / command). The palette is substrate-shared — same payload drives
// web, Mac, Windows; the host wires a `PaletteSearchPort` per substrate.
// Cross-audit §1.1 (ItemRef as the `entity` target). Single declaration
// site: ./palette.ts.
export type {
  PaletteHit,
  PaletteHitKind,
  PaletteSearchContext,
  PaletteSearchRequest,
  PaletteSearchResponse,
} from "./palette";
// === end Phase 12 ⌘K Palette ===

// === Phase 12 Settings (notifications + webhook security) ===
// Settings JSONB-blob wire shapes. Settings is NOT a destination per
// master PRD §3.5 — it lives off the profile menu. The three namespaces
// Phase 12 lands: per-user notification defaults, admin workspace
// notification defaults, admin workspace webhook security defaults
// (Routines §9.7 Q6 HMAC-of-payload UX + max-secret-age policy).
// Settings storage reuses the existing `tenant_settings` / `user_settings`
// JSONB pattern (sub-PRD §5.2 — no parallel table). Single declaration
// site: ./settings.ts.
export type {
  NotificationDefaults,
  NotificationQuietHoursBlob,
  PerDestinationToggle,
  UpdateNotificationDefaultsRequest,
  UpdateWebhookSecurityDefaultsRequest,
  UpdateWorkspaceNotificationDefaultsRequest,
  WebhookSecurityDefaults,
  WorkspaceNotificationDefaults,
} from "./settings";
// === end Phase 12 Settings ===

// === BYOK provider keys (Settings → AI & data) ===
// Bring-your-own-key wire shapes for the per-user model provider keys
// under `/v1/settings/provider-keys`. Reads carry only a masked
// `key_hint` (last 4 chars); the plaintext travels exactly once in the
// PUT body and is encrypted at rest server-side. Single declaration
// site: ./providerKeys.ts.
export type {
  ListProviderKeysResponse,
  ProviderKeyProvider,
  ProviderKeySummary,
  PutProviderKeyRequest,
  PutProviderKeyResponse,
  ValidateProviderKeyRequest,
  ValidateProviderKeyResponse,
} from "./providerKeys";
// === end BYOK provider keys ===

// === Local models (Round 2 — HF GGUF + local Ollama) ===
// Desktop / self-host only; the management routes 404 unless the deployment
// enabled the feature. Single declaration site: ./localModels.ts.
export type {
  LocalModelPullEvent,
  LocalModelRunPlacement,
  LocalModelSize,
  LocalModelSummary,
  LocalModelsListResponse,
  LocalModelsStatus,
  PullLocalModelRequest,
} from "./localModels";
// === end Local models ===

// === SIWE wallet sign-in (EIP-4361) ===
// `/v1/auth/siwe/nonce` + `/v1/auth/siwe/verify` wire shapes. The verify
// response mirrors the OIDC callback session handoff so the SPA adopts
// wallet sessions through the same path as SSO. Single declaration site:
// ./siwe.ts.
export type {
  SiweNonceRequest,
  SiweNonceResponse,
  SiweSessionResponse,
  SiweVerifyErrorDetail,
  SiweVerifyRequest,
} from "./siwe";
// === end SIWE wallet sign-in ===

// === Phase 4 (desktop redesign) Chats destination ===
// Conversation ARCHIVE read model (pinned / recent / archived). A row
// reopens the thread in the Run cockpit — the destination is not a live
// thread canvas. `ChatsArchive` is the bucketed shape the destination
// consumes; the host binder composes it from `/v1/agent/conversations`
// (incl. archived) until a dedicated bucketed endpoint lands (PRD §11).
// Single declaration site: ./chats.ts.
export type { ChatArchiveRow, ChatArchiveStatus, ChatsArchive } from "./chats";
// Runtime SSOT tuple for the chat archive status union (state-chip map).
export { CHAT_ARCHIVE_STATUSES } from "./chats";
// === end Phase 4 Chats ===

// === Phase 4 (desktop redesign) Activity destination ===
// Single run-history feed that absorbs the former Agents / Inbox /
// audit-log surfaces. `ActivityRunRow` is the projected day-groupable row;
// day grouping is derived in the shell (no `DayGroup` on the wire). The
// host binder composes `/v1/agent/conversations` + `/v1/audit` into rows
// until a dedicated `GET /v1/activity` lands (PRD §11). Single declaration
// site: ./activity.ts.
export type { ActivityRunRow, ActivityRunStatus } from "./activity";
// Runtime SSOT tuple for the activity run status union (status→tone map).
export { ACTIVITY_RUN_STATUSES } from "./activity";
// === end Phase 4 Activity ===

// === Phase 4 (desktop redesign) Skills destination ===
// Card-grid summary of saved multi-step workflows (name, sub, N runs;
// Run / Edit / New). `SkillSummary` is the lightweight row projection,
// distinct from the richer authoring `Skill` declared above; the binder
// projects `/v1/skills` → `SkillSummary` (PRD §11). Single declaration
// site: ./skills.ts.
export type { SkillSummary } from "./skills";
// === end Phase 4 Skills ===
