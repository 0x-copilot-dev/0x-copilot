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
} from "@enterprise-search/api-types";
import { isRuntimeEventEnvelope } from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import { assertOk, jsonHeaders } from "./http";

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

export async function createConversation(
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
  const response = await fetch("/v1/agent/conversations", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
  await assertOk(response);
  return (await response.json()) as Conversation;
}

export async function getConversation(
  conversationId: string,
  identity: RequestIdentity,
): Promise<Conversation> {
  const response = await fetch(
    `/v1/agent/conversations/${conversationId}?${identityParams(identity)}`,
  );
  await assertOk(response);
  return (await response.json()) as Conversation;
}

export async function listConversations(
  identity: RequestIdentity,
  options: { limit?: number; includeArchived?: boolean } = {},
): Promise<ConversationListResponse> {
  const params = identityParams(identity);
  params.set("limit", String(options.limit ?? 30));
  if (options.includeArchived) {
    params.set("include_archived", "true");
  }
  const response = await fetch(`/v1/agent/conversations?${params}`);
  await assertOk(response);
  return (await response.json()) as ConversationListResponse;
}

export async function listMessages(
  conversationId: string,
  identity: RequestIdentity,
): Promise<MessageListResponse> {
  const params = identityParams(identity);
  params.set("limit", "100");
  const response = await fetch(
    `/v1/agent/conversations/${conversationId}/messages?${params}`,
  );
  await assertOk(response);
  return (await response.json()) as MessageListResponse;
}

export async function listModels(
  identity: RequestIdentity,
): Promise<ModelCatalogResponse> {
  const response = await fetch(`/v1/agent/models?${identityParams(identity)}`);
  await assertOk(response);
  return (await response.json()) as ModelCatalogResponse;
}

export async function createRun(
  conversationId: string,
  userInput: string,
  identity: RequestIdentity,
  options: {
    model?: ModelSelectionRequest | null;
    content?: Array<Record<string, unknown>>;
    attachments?: Array<Record<string, unknown>>;
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
    attachments: options.attachments as CreateRunRequest["attachments"],
    quote: options.quote,
    parent_message_id: options.parentMessageId,
    source_message_id: options.sourceMessageId,
    regenerate_from_message_id: options.regenerateFromMessageId,
    branch_id: options.branchId,
  };
  const response = await fetch("/v1/agent/runs", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
  await assertOk(response);
  return (await response.json()) as CreateRunResponse;
}

export async function cancelRun(
  runId: string,
  identity: RequestIdentity,
): Promise<CancelRunResponse> {
  const payload: CancelRunRequest = {
    requested_by_user_id: identity.userId,
    reason: "Cancelled from web chat",
  };
  const response = await fetch(
    `/v1/agent/runs/${runId}/cancel?${identityParams(identity)}`,
    {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    },
  );
  await assertOk(response);
  return (await response.json()) as CancelRunResponse;
}

export async function decideApproval(
  approvalId: string,
  decision: ApprovalDecisionRequest["decision"],
  identity: RequestIdentity,
): Promise<ApprovalDecisionResponse> {
  const payload: ApprovalDecisionRequest = {
    decision,
    decided_by_user_id: identity.userId,
  };
  const params = new URLSearchParams({ org_id: identity.orgId });
  const response = await fetch(
    `/v1/agent/approvals/${approvalId}/decision?${params}`,
    {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    },
  );
  await assertOk(response);
  return (await response.json()) as ApprovalDecisionResponse;
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
