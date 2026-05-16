import type {
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  ApprovalUndoResponse,
  AssignedApprovalsResponse,
  CancelRunRequest,
  CancelRunResponse,
  Conversation,
  ConversationContextResponse,
  ConversationListResponse,
  ConversationShare,
  CreateConversationRequest,
  CreateRunRequest,
  CreateRunResponse,
  CreateShareRequest,
  CreateShareResponse,
  ConversationConnectorScopesResponse,
  Draft,
  ForkRequest,
  ForkResponse,
  DraftDiscardRequest,
  DraftListResponse,
  DraftPatchRequest,
  DraftSendRequest,
  DraftSendResponse,
  InboxEventEnvelope,
  ListSharesResponse,
  MessageListResponse,
  ModelCatalogResponse,
  ModelSelectionRequest,
  RecipientPreview,
  RetentionEffectiveResponse,
  RuntimeEventEnvelope,
  RuntimeEventReplayResponse,
  SharedConversationView,
  SourceListResponse,
  SubagentListResponse,
  SubagentStatusFilter,
  UpdateConversationConnectorScopesRequest,
  UpdateConversationRequest,
  UpdateShareRequest,
  UpdateWorkspaceDefaultsRequest,
  BudgetMeResponse,
  UsageConversationRow,
  UsageMeResponse,
  UsageOrgResponse,
  UsagePeriod,
  WorkspaceDefaultsResponse,
  WorkspaceExportResponse,
} from "@enterprise-search/api-types";
import { isRuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import {
  httpDelete,
  httpGet,
  httpPatchQuery,
  httpPost,
  httpPostQuery,
  httpPutQuery,
} from "./http";
import { getAppTransport } from "./transport";

const SSE_EVENT_NAME = "runtime_event";

export type RuntimeStreamProtocolErrorReason =
  | "malformed_json"
  | "invalid_envelope";

export class RuntimeStreamProtocolError extends Error {
  readonly reason: RuntimeStreamProtocolErrorReason;
  readonly dataLength: number;

  constructor(reason: RuntimeStreamProtocolErrorReason, data: string) {
    super(
      reason === "malformed_json"
        ? "Runtime stream emitted malformed JSON."
        : "Runtime stream emitted an invalid event envelope.",
    );
    this.name = "RuntimeStreamProtocolError";
    this.reason = reason;
    // Length only — never the raw payload. The original data may contain
    // user-typed prompts or model output, and this error object can flow
    // through React error boundaries / OTEL spans where any string field
    // could be serialized. Length preserves the one useful debugging signal
    // (was the payload truncated?) without the leak.
    this.dataLength = data.length;
  }
}

export function createConversation(
  identity: RequestIdentity,
  options: {
    title?: string | null;
    idempotencyKey?: string | null;
    metadata?: Record<string, unknown>;
  } = {},
): Promise<Conversation> {
  const payload: CreateConversationRequest = {
    org_id: identity.orgId,
    user_id: identity.userId,
    title: options.title ?? "New chat",
    metadata: options.metadata ?? {},
  };
  if (options.idempotencyKey !== undefined) {
    payload.idempotency_key = options.idempotencyKey;
  }
  return httpPost<Conversation>("/v1/agent/conversations", payload);
}

export function getConversation(
  conversationId: string,
  identity: RequestIdentity,
): Promise<Conversation> {
  return httpGet<Conversation>(
    `/v1/agent/conversations/${conversationId}`,
    identity,
  );
}

export function listConversations(
  identity: RequestIdentity,
  options: { limit?: number; includeArchived?: boolean } = {},
): Promise<ConversationListResponse> {
  return httpGet<ConversationListResponse>(
    "/v1/agent/conversations",
    identity,
    {
      limit: String(options.limit ?? 30),
      include_archived: options.includeArchived ? "true" : undefined,
    },
  );
}

export function listMessages(
  conversationId: string,
  identity: RequestIdentity,
): Promise<MessageListResponse> {
  return httpGet<MessageListResponse>(
    `/v1/agent/conversations/${conversationId}/messages`,
    identity,
    { limit: "100" },
  );
}

/**
 * PR 6.2 — recipient forks a shared conversation into their own
 * workspace. The share token is the access grant; the facade verifies
 * the recipient identity from the session token before forwarding.
 * Returns the new conversation id; the caller navigates to
 * ``/?conversationId=``.
 */
export function forkShare(
  shareToken: string,
  request: ForkRequest,
): Promise<ForkResponse> {
  return httpPost<ForkResponse>(
    `/v1/agent/shares/${encodeURIComponent(shareToken)}/fork`,
    request,
  );
}

/**
 * PR A3 — self-fork. The user picks "retry from here" or "fork to new
 * chat" on a message in their own conversation. Server creates a new
 * conversation seeded with the messages up to (and including)
 * `from_message_id`, optionally overriding model + connector scopes
 * for the next run. Returns the new conversation id.
 */
export interface SelfForkRequest {
  from_message_id: string;
  model?: string | null;
  enabled_connectors?: Record<string, readonly string[] | null> | null;
}
export function forkConversationFromMessage(
  conversationId: string,
  request: SelfForkRequest,
): Promise<ForkResponse> {
  return httpPost<ForkResponse>(
    `/v1/agent/conversations/${encodeURIComponent(conversationId)}/fork`,
    request,
  );
}

/** B5: per-conversation `/context` slash-command panel data. */
export function getConversationContext(
  conversationId: string,
  identity: RequestIdentity,
): Promise<ConversationContextResponse> {
  return httpGet<ConversationContextResponse>(
    `/v1/agent/conversations/${conversationId}/context`,
    identity,
  );
}

/** PR 1.5: archive read of subagents dispatched in this conversation. */
export function listSubagents(
  conversationId: string,
  identity: RequestIdentity,
  options: { status?: SubagentStatusFilter; limit?: number } = {},
): Promise<SubagentListResponse> {
  return httpGet<SubagentListResponse>(
    `/v1/agent/conversations/${conversationId}/subagents`,
    identity,
    {
      limit: String(options.limit ?? 50),
      status: options.status,
    },
  );
}

/**
 * PR 1.5: archive read of unique sources cited in this conversation.
 *
 * Returns one row per ``(source_connector, source_doc_id)`` ranked by
 * citation count then recency. ``run_id`` scopes to a single run.
 */
export function listSources(
  conversationId: string,
  identity: RequestIdentity,
  options: { runId?: string | null; limit?: number } = {},
): Promise<SourceListResponse> {
  return httpGet<SourceListResponse>(
    `/v1/agent/conversations/${conversationId}/sources`,
    identity,
    {
      limit: String(options.limit ?? 200),
      run_id: options.runId ?? undefined,
    },
  );
}

/**
 * PR 1.2: merge-patch the per-chat connector scope override.
 *
 * `scopes` follows RFC 7396 merge-patch — keys present overwrite the
 * stored value (including `null` to pause); keys absent leave the stored
 * value untouched. The response carries the full effective scope map
 * after the merge.
 */
export function updateConversationConnectorScopes(
  conversationId: string,
  request: UpdateConversationConnectorScopesRequest,
  identity: RequestIdentity,
): Promise<ConversationConnectorScopesResponse> {
  return httpPatchQuery<ConversationConnectorScopesResponse>(
    `/v1/agent/conversations/${conversationId}/connectors`,
    request,
    identity,
  );
}

/**
 * PR 1.6: merge-patch the conversation row — title, folder, archived.
 * RFC 7396 merge-patch semantics: omit a field to leave it untouched,
 * send `null` to clear (folder/title) or un-archive (`archived: false`).
 */
export function updateConversation(
  conversationId: string,
  request: UpdateConversationRequest,
  identity: RequestIdentity,
): Promise<Conversation> {
  return httpPatchQuery<Conversation>(
    `/v1/agent/conversations/${conversationId}`,
    request,
    identity,
  );
}

/**
 * PR 1.6 / 3.5 — workspace-level defaults (model + connectors + retention).
 * `GET` returns the effective view (deployment fallback when no row exists);
 * `PUT` is admin-only and writes the defaults row + 3 retention policies + 1
 * audit row in a single ai-backend transaction. Full-document replace, not
 * merge-patch — the Settings panel sends the resolved shape it just rendered.
 */
export function getWorkspaceDefaults(
  identity: RequestIdentity,
): Promise<WorkspaceDefaultsResponse> {
  return httpGet<WorkspaceDefaultsResponse>(
    "/v1/agent/workspace/defaults",
    identity,
  );
}

export function putWorkspaceDefaults(
  request: UpdateWorkspaceDefaultsRequest,
  identity: RequestIdentity,
): Promise<WorkspaceDefaultsResponse> {
  return httpPutQuery<WorkspaceDefaultsResponse>(
    "/v1/agent/workspace/defaults",
    request,
    identity,
  );
}

/**
 * PR 4.3 — read-only effective retention TTL view for the
 * Privacy & data Settings panel. Re-uses the same resolver the
 * sweeper does so the displayed numbers are the applied numbers.
 */
export function getRetentionEffective(
  identity: RequestIdentity,
): Promise<RetentionEffectiveResponse> {
  return httpGet<RetentionEffectiveResponse>(
    "/v1/retention/effective",
    identity,
  );
}

/**
 * PR 4.3 — queue a workspace export. v1 is a stub that returns 202 +
 * ``{export_id, status: "queued"}`` and writes one audit row; the
 * actual NDJSON dump pipeline lives in a follow-up PR.
 */
export function requestWorkspaceExport(
  identity: RequestIdentity,
): Promise<WorkspaceExportResponse> {
  return httpPostQuery<WorkspaceExportResponse>(
    "/v1/agent/workspace/export",
    { scope: "workspace" },
    identity,
  );
}

/**
 * PR 4.3 — request a workspace-wide data delete. v1 always 501s and
 * audits the typed-confirmation correctness; the cascade-delete job
 * is a separate, gated follow-up PR. The FE renders the 501 message
 * verbatim (``Workspace deletion is gated. Contact support.``).
 */
export function deleteWorkspaceData(
  confirmSlug: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete("/v1/agent/workspace/data", identity, {
    confirm_slug: confirmSlug,
  });
}

/** B6: caller's own usage rollup for the period (`today` / `7d` / `30d` / `month`). */
export function getMyUsage(
  period: UsagePeriod,
  identity: RequestIdentity,
): Promise<UsageMeResponse> {
  return httpGet<UsageMeResponse>("/v1/usage/me", identity, { period });
}

/** B6: caller's top conversations by total tokens for the period. */
export function getMyTopConversations(
  period: UsagePeriod,
  identity: RequestIdentity,
  limit = 10,
): Promise<UsageConversationRow[]> {
  return httpGet<UsageConversationRow[]>(
    "/v1/usage/me/conversations",
    identity,
    { period, limit: String(limit) },
  );
}

/**
 * PR 4.5: org-wide usage rollup for the period (admin / auditor only).
 * Returns 403 to non-admin callers; UI surfaces an admin-only empty state.
 */
export function getOrgUsage(
  period: UsagePeriod,
  identity: RequestIdentity,
): Promise<UsageOrgResponse> {
  return httpGet<UsageOrgResponse>("/v1/usage/org", identity, { period });
}

/**
 * PR 4.5: caller's currently-applicable budgets with remaining headroom.
 * Drives the plan-limit overlay on the workspace usage chart.
 */
export function getMyBudgets(
  identity: RequestIdentity,
): Promise<BudgetMeResponse> {
  return httpGet<BudgetMeResponse>("/v1/budgets/me", identity);
}

export function listModels(
  identity: RequestIdentity,
): Promise<ModelCatalogResponse> {
  return httpGet<ModelCatalogResponse>("/v1/agent/models", identity);
}

export function createRun(
  conversationId: string,
  userInput: string,
  identity: RequestIdentity,
  options: {
    model?: ModelSelectionRequest | null;
    content?: CreateRunRequest["content"];
    attachments?: CreateRunRequest["attachments"];
    quote?: Record<string, unknown>;
    parentMessageId?: string | null;
    sourceMessageId?: string | null;
    regenerateFromMessageId?: string | null;
    branchId?: string | null;
  } = {},
): Promise<CreateRunResponse> {
  const payload: CreateRunRequest = {
    conversation_id: conversationId,
    org_id: identity.orgId,
    user_id: identity.userId,
    user_input: userInput,
    model: options.model,
    content: options.content,
    attachments: options.attachments,
    quote: options.quote,
    parent_message_id: options.parentMessageId,
    source_message_id: options.sourceMessageId,
    regenerate_from_message_id: options.regenerateFromMessageId,
    branch_id: options.branchId,
  };
  return httpPost<CreateRunResponse>("/v1/agent/runs", payload);
}

export function cancelRun(
  runId: string,
  identity: RequestIdentity,
): Promise<CancelRunResponse> {
  const payload: CancelRunRequest = {
    requested_by_user_id: identity.userId,
    reason: "Cancelled from web chat",
  };
  return httpPostQuery<CancelRunResponse>(
    `/v1/agent/runs/${runId}/cancel`,
    payload,
    identity,
  );
}

export function decideApproval(
  approvalId: string,
  decision: ApprovalDecisionRequest["decision"],
  identity: RequestIdentity,
  reason?: string,
  answer?: string,
  forwardTo?: ApprovalDecisionRequest["forward_to"],
): Promise<ApprovalDecisionResponse> {
  const payload: ApprovalDecisionRequest = {
    decision,
    decided_by_user_id: identity.userId,
  };
  if (reason !== undefined) {
    payload.reason = reason;
  }
  if (answer !== undefined) {
    payload.answer = answer;
  }
  // PR 1.4 — two-stage approval forwarding. Optional; omitted means a
  // direct approve/reject decision against the run.
  if (forwardTo !== undefined && forwardTo !== null) {
    payload.forward_to = forwardTo;
  }
  // Only `org_id` rides the query — not the full identity that
  // httpPostQuery would inject — because the decision endpoint authorises
  // by approval row plus org context, with `decided_by_user_id` carried in
  // the body. Bypasses the helper and goes straight through Transport.
  return getAppTransport().request<ApprovalDecisionResponse>({
    method: "POST",
    path: `/v1/agent/approvals/${encodeURIComponent(approvalId)}/decision`,
    query: { org_id: identity.orgId },
    body: payload,
  });
}

// PR 4.4.6.4 — record an undo request inside the 60s reversibility
// window. Server returns 200 with the audited timestamps, 410 when the
// window has expired, 422 if the approval was never reversible.
export function requestApprovalUndo(
  approvalId: string,
  identity: RequestIdentity,
): Promise<ApprovalUndoResponse> {
  // POST with no body. httpPostQuery would always JSON.stringify a body
  // slot + add a content-type header; Transport.request honours omitting
  // both when `body` is undefined, preserving the prior wire shape.
  return getAppTransport().request<ApprovalUndoResponse>({
    method: "POST",
    path: `/v1/agent/approvals/${encodeURIComponent(approvalId)}/undo`,
    query: { org_id: identity.orgId, user_id: identity.userId },
  });
}

// PR 1.3 — Workspace-pane drafts. All paths proxied via backend-facade.

export function listDrafts(
  conversationId: string,
  identity: RequestIdentity,
): Promise<DraftListResponse> {
  return httpGet<DraftListResponse>(
    `/v1/agent/conversations/${conversationId}/drafts`,
    identity,
  );
}

export function getDraft(
  draftId: string,
  identity: RequestIdentity,
  options: { version?: number } = {},
): Promise<Draft> {
  return httpGet<Draft>(
    `/v1/agent/drafts/${draftId}`,
    identity,
    options.version !== undefined
      ? { version: String(options.version) }
      : undefined,
  );
}

export function patchDraft(
  draftId: string,
  request: DraftPatchRequest,
  identity: RequestIdentity,
): Promise<Draft> {
  return httpPatchQuery<Draft>(
    `/v1/agent/drafts/${draftId}`,
    request,
    identity,
  );
}

export function sendDraft(
  draftId: string,
  request: DraftSendRequest,
  identity: RequestIdentity,
): Promise<DraftSendResponse> {
  return httpPostQuery<DraftSendResponse>(
    `/v1/agent/drafts/${draftId}/send`,
    request,
    identity,
  );
}

export function discardDraft(
  draftId: string,
  request: DraftDiscardRequest,
  identity: RequestIdentity,
): Promise<Draft> {
  return httpPostQuery<Draft>(
    `/v1/agent/drafts/${draftId}/discard`,
    request,
    identity,
  );
}

export function replayRunEvents(
  runId: string,
  identity: RequestIdentity,
  afterSequence = 0,
): Promise<RuntimeEventReplayResponse> {
  return httpGet<RuntimeEventReplayResponse>(
    `/v1/agent/runs/${runId}/events`,
    identity,
    { after_sequence: String(afterSequence) },
  );
}

/**
 * Closeable handle for a running SSE subscription. Replaces the bare
 * EventSource the FE used pre-W0.1: the browser's EventSource cannot
 * carry an Authorization header, so the bearer never reached the
 * facade and every stream 401'd. The fetch-based implementation in
 * `_streamSseEvents` ships the bearer like any other API call.
 */
export interface AgentEventStream {
  close(): void;
}

export function streamRunEvents({
  runId,
  afterSequence = 0,
  identity,
  onEvent,
  onError,
  onProtocolError,
  onOpen,
}: {
  runId: string;
  afterSequence?: number;
  identity: RequestIdentity;
  onEvent: (event: RuntimeEventEnvelope) => void;
  onError: (error: Event) => void;
  onProtocolError?: (error: RuntimeStreamProtocolError) => void;
  onOpen?: () => void;
}): AgentEventStream {
  return getAppTransport().subscribeServerSentEvents({
    path: `/v1/agent/runs/${runId}/stream`,
    query: sseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        onProtocolError?.(
          new RuntimeStreamProtocolError("malformed_json", data),
        );
        return;
      }
      if (isRuntimeEventEnvelope(parsed)) {
        onEvent(parsed);
        return;
      }
      onProtocolError?.(
        new RuntimeStreamProtocolError("invalid_envelope", data),
      );
    },
  });
}

// PR 1.4.1 Gap #6 — recipient inbox endpoint. Filters approvals where the
// caller is the requested_by user (i.e. assignments forwarded to them).
export function listAssignedApprovals(
  identity: RequestIdentity,
  options: {
    status?: "pending" | "approved" | "rejected" | "forwarded";
    limit?: number;
    cursor?: string | null;
  } = {},
): Promise<AssignedApprovalsResponse> {
  const params: Record<string, string> = {
    assigned_to_me: "true",
    status: options.status ?? "pending",
    limit: String(options.limit ?? 50),
  };
  if (options.cursor) {
    params.cursor = options.cursor;
  }
  return httpGet<AssignedApprovalsResponse>(
    "/v1/agent/approvals",
    identity,
    params,
  );
}

// PR 1.4.1 Gap #6 — per-user inbox SSE channel. Mirrors
// streamRunEvents' EventSource shape so the FE only knows one parser.
// Reconnect with the highest received sequence_no.
export function streamInboxEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  identity: RequestIdentity;
  afterSequence: number;
  onEvent: (event: InboxEventEnvelope) => void;
  onError: (event: Event) => void;
  onOpen?: () => void;
}): AgentEventStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/agent/me/inbox/stream",
    query: sseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      try {
        const parsed = JSON.parse(data) as InboxEventEnvelope;
        if (
          typeof parsed.sequence_no === "number" &&
          typeof parsed.event_type === "string" &&
          typeof parsed.approval_id === "string"
        ) {
          onEvent(parsed);
        }
      } catch {
        // Malformed payload; FE caller can wire onError if needed.
      }
    },
  });
}

// Identity + after_sequence in the shape Transport.subscribeServerSentEvents
// wants (a query object, not a URLSearchParams string). Both SSE callers
// share this so they can't drift in how the cursor is named.
function sseQueryFor(
  identity: RequestIdentity,
  afterSequence: number,
): Record<string, string> {
  const out: Record<string, string> = { after_sequence: String(afterSequence) };
  for (const [k, v] of identityParams(identity)) {
    out[k] = v;
  }
  return out;
}

// The legacy onError signature was modelled after EventSource's bare Event
// — chat callers only react to "stream broken" and reconnect. Preserve
// that contract here so we don't have to fan out a wider refactor across
// every SSE consumer in this PR.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}

// SSE plumbing (frame parsing, abortable fetch, reconnect-cursor-free
// reader loop) used to live here as `_streamSseEvents`. It moved to
// packages/chat-transport/src/web/sse.ts so the desktop webview's
// transport bridge can stream events through the extension host without
// re-implementing the parser. Domain envelope validation
// (`isRuntimeEventEnvelope`, `RuntimeStreamProtocolError`) stays here —
// it's frontend-specific and runtime-event-specific.

// PR 6.1 — Conversation sharing. Six endpoints; the bearer ``share_token``
// rides in the URL on the recipient endpoints, but the caller must still
// be a valid session — the token grants access to the *share row*, not
// the *user identity*.

export function createShare(
  conversationId: string,
  request: CreateShareRequest,
  identity: RequestIdentity,
): Promise<CreateShareResponse> {
  return httpPostQuery<CreateShareResponse>(
    `/v1/agent/conversations/${encodeURIComponent(conversationId)}/share`,
    request,
    identity,
  );
}

export function listShares(
  conversationId: string,
  identity: RequestIdentity,
): Promise<ListSharesResponse> {
  return httpGet<ListSharesResponse>(
    `/v1/agent/conversations/${encodeURIComponent(conversationId)}/shares`,
    identity,
  );
}

export function updateShare(
  shareId: string,
  request: UpdateShareRequest,
  identity: RequestIdentity,
): Promise<ConversationShare> {
  return httpPatchQuery<ConversationShare>(
    `/v1/agent/shares/${encodeURIComponent(shareId)}`,
    request,
    identity,
  );
}

export function revokeShare(
  shareId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(
    `/v1/agent/shares/${encodeURIComponent(shareId)}`,
    identity,
  );
}

export function getSharedConversation(
  shareToken: string,
  identity: RequestIdentity,
): Promise<SharedConversationView> {
  return httpGet<SharedConversationView>(
    `/v1/agent/shares/${encodeURIComponent(shareToken)}`,
    identity,
  );
}

export function previewSharedConversation(
  shareToken: string,
  identity: RequestIdentity,
): Promise<RecipientPreview> {
  return httpGet<RecipientPreview>(
    `/v1/agent/shares/${encodeURIComponent(shareToken)}/preview`,
    identity,
  );
}
