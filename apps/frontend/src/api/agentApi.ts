import type {
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  CancelRunRequest,
  CancelRunResponse,
  Conversation,
  ConversationListResponse,
  CreateConversationRequest,
  CreateRunRequest,
  CreateRunResponse,
  MessageListResponse,
  ModelCatalogResponse,
  ModelSelectionRequest,
  RuntimeEventEnvelope,
  RuntimeEventReplayResponse,
} from "@enterprise-search/api-types";
import { isRuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import { httpGet, httpPost, httpPostQuery } from "./http";

const SSE_EVENT_NAME = "runtime_event";

export type RuntimeStreamProtocolErrorReason =
  | "malformed_json"
  | "invalid_envelope";

export class RuntimeStreamProtocolError extends Error {
  readonly reason: RuntimeStreamProtocolErrorReason;
  readonly data: string;

  constructor(reason: RuntimeStreamProtocolErrorReason, data: string) {
    super(
      reason === "malformed_json"
        ? "Runtime stream emitted malformed JSON."
        : "Runtime stream emitted an invalid event envelope.",
    );
    this.name = "RuntimeStreamProtocolError";
    this.reason = reason;
    this.data = data;
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
): Promise<ApprovalDecisionResponse> {
  const payload: ApprovalDecisionRequest = {
    decision,
    decided_by_user_id: identity.userId,
  };
  if (reason !== undefined) {
    payload.reason = reason;
  }
  const params = new URLSearchParams({ org_id: identity.orgId });
  return fetch(
    `/v1/agent/approvals/${encodeURIComponent(approvalId)}/decision?${params}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
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
