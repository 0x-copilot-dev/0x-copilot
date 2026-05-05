import type {
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  CancelRunRequest,
  CancelRunResponse,
  Conversation,
  ConversationContextResponse,
  ConversationListResponse,
  CreateConversationRequest,
  CreateRunRequest,
  CreateRunResponse,
  ConversationConnectorScopesResponse,
  Draft,
  DraftDiscardRequest,
  DraftListResponse,
  DraftPatchRequest,
  DraftSendRequest,
  DraftSendResponse,
  MessageListResponse,
  ModelCatalogResponse,
  ModelSelectionRequest,
  RuntimeEventEnvelope,
  RuntimeEventReplayResponse,
  SourceListResponse,
  SubagentListResponse,
  SubagentStatusFilter,
  UpdateConversationConnectorScopesRequest,
  UsageConversationRow,
  UsageMeResponse,
  UsagePeriod,
} from "@enterprise-search/api-types";
import { isRuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import {
  correlationHeaders,
  httpGet,
  httpPatchQuery,
  httpPost,
  httpPostQuery,
} from "./http";

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
  const params = new URLSearchParams({ org_id: identity.orgId });
  return fetch(
    `/v1/agent/approvals/${encodeURIComponent(approvalId)}/decision?${params}`,
    {
      method: "POST",
      headers: { "content-type": "application/json", ...correlationHeaders() },
      body: JSON.stringify(payload),
    },
  ).then(async (response) => {
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Request failed with ${response.status}`);
    }
    return (await response.json()) as ApprovalDecisionResponse;
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
}): EventSource {
  const params = identityParams(identity);
  params.set("after_sequence", String(afterSequence));
  const eventSource = new EventSource(
    `/v1/agent/runs/${runId}/stream?${params}`,
  );
  eventSource.addEventListener("open", () => onOpen?.());
  eventSource.addEventListener(SSE_EVENT_NAME, (message) => {
    const data = String((message as MessageEvent).data);
    let parsed: unknown;
    try {
      parsed = JSON.parse(data) as unknown;
    } catch {
      onProtocolError?.(new RuntimeStreamProtocolError("malformed_json", data));
      return;
    }
    if (isRuntimeEventEnvelope(parsed)) {
      onEvent(parsed);
      return;
    }
    onProtocolError?.(new RuntimeStreamProtocolError("invalid_envelope", data));
  });
  eventSource.addEventListener("error", onError);
  return eventSource;
}
