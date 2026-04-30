import type {
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  CancelRunRequest,
  CancelRunResponse,
  Conversation,
  CreateConversationRequest,
  CreateRunRequest,
  CreateRunResponse,
  MessageListResponse,
  RunStatus,
  RuntimeEventEnvelope
} from "@enterprise-search/api-types";
import { isRuntimeEventEnvelope } from "@enterprise-search/api-types";
import { DEFAULT_IDENTITY, type RequestIdentity, identityParams } from "./config";
import { assertOk, jsonHeaders } from "./http";

const SSE_EVENT_NAME = "runtime_event";

export async function createConversation(
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<Conversation> {
  const payload: CreateConversationRequest = {
    org_id: identity.orgId,
    user_id: identity.userId,
    title: "Current task review",
    idempotency_key: `web-${identity.orgId}-${identity.userId}`
  };
  const response = await fetch("/v1/agent/conversations", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as Conversation;
}

export async function listMessages(
  conversationId: string,
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<MessageListResponse> {
  const params = identityParams(identity);
  params.set("limit", "100");
  const response = await fetch(`/v1/agent/conversations/${conversationId}/messages?${params}`);
  await assertOk(response);
  return (await response.json()) as MessageListResponse;
}

export async function createRun(
  conversationId: string,
  userInput: string,
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<CreateRunResponse> {
  const payload: CreateRunRequest = {
    conversation_id: conversationId,
    org_id: identity.orgId,
    user_id: identity.userId,
    user_input: userInput
  };
  const response = await fetch("/v1/agent/runs", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as CreateRunResponse;
}

export async function getRun(
  runId: string,
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<RunStatus> {
  const response = await fetch(`/v1/agent/runs/${runId}?${identityParams(identity)}`);
  await assertOk(response);
  return (await response.json()) as RunStatus;
}

export async function cancelRun(
  runId: string,
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<CancelRunResponse> {
  const payload: CancelRunRequest = {
    requested_by_user_id: identity.userId,
    reason: "Cancelled from web chat"
  };
  const response = await fetch(`/v1/agent/runs/${runId}/cancel?${identityParams(identity)}`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as CancelRunResponse;
}

export async function decideApproval(
  approvalId: string,
  decision: ApprovalDecisionRequest["decision"],
  identity: RequestIdentity = DEFAULT_IDENTITY
): Promise<ApprovalDecisionResponse> {
  const payload: ApprovalDecisionRequest = {
    decision,
    decided_by_user_id: identity.userId
  };
  const params = new URLSearchParams({ org_id: identity.orgId });
  const response = await fetch(`/v1/agent/approvals/${approvalId}/decision?${params}`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as ApprovalDecisionResponse;
}

export function streamRunEvents({
  runId,
  afterSequence = 0,
  identity = DEFAULT_IDENTITY,
  onEvent,
  onError,
  onOpen
}: {
  runId: string;
  afterSequence?: number;
  identity?: RequestIdentity;
  onEvent: (event: RuntimeEventEnvelope) => void;
  onError: (error: Event) => void;
  onOpen?: () => void;
}): EventSource {
  const params = identityParams(identity);
  params.set("after_sequence", String(afterSequence));
  const eventSource = new EventSource(`/v1/agent/runs/${runId}/stream?${params}`);
  eventSource.addEventListener("open", () => onOpen?.());
  eventSource.addEventListener(SSE_EVENT_NAME, (message) => {
    const parsed = JSON.parse((message as MessageEvent).data) as unknown;
    if (isRuntimeEventEnvelope(parsed)) {
      onEvent(parsed);
    }
  });
  eventSource.addEventListener("error", onError);
  return eventSource;
}
