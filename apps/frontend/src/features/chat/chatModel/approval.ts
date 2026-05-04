import type { ApprovalDecision } from "@enterprise-search/api-types";
import type { MessageStatus as AssistantMessageStatus } from "@assistant-ui/react";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { resolveMcpAuthDecision } from "./mcpAuth";
import { isToolCallPart, jsonArgs } from "./recordHelpers";
import { hasPendingAction } from "./status";
import type { ChatItem } from "./types";

const APPROVAL_DECISION_STATUSES = new Set<string>(["approved", "rejected"]);
const QUESTION_RESOLUTION_STATUSES = new Set<string>(["answered", "skipped"]);

export function resolveApprovalDecision(
  items: ChatItem[],
  approvalId: string,
  decision: ApprovalDecision,
  answer?: string,
): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    const content = item.content.map((part) => {
      if (!isToolCallPart(part) || part.toolCallId !== approvalId) {
        return part;
      }
      const args = asRecord(part.args);
      // ask_a_question parts use the question vocabulary (answered/skipped),
      // not the permission-gate vocabulary. Branch here so the optimistic
      // local update matches the wire-level status the server will emit.
      if (stringValue(args.approval_kind) === "ask_a_question") {
        const status = decision === "approved" ? "answered" : "skipped";
        return {
          ...part,
          args: jsonArgs({
            ...args,
            approval_id: approvalId,
            status,
          }),
          result: {
            approval_id: approvalId,
            status,
            decision,
            answer: answer ?? null,
          },
        };
      }
      return {
        ...part,
        args: jsonArgs({
          ...args,
          approval_id: approvalId,
          status: decision,
        }),
        result: { approval_id: approvalId, decision },
      };
    });
    if (content === item.content) {
      return item;
    }
    const status =
      item.status?.type === "requires-action" && !hasPendingAction(content)
        ? ({ type: "running" } satisfies AssistantMessageStatus)
        : item.status;
    return { ...item, content, status };
  });
}

export function resolveQuestionFromPayload(
  items: ChatItem[],
  approvalId: string,
  status: string,
  payload: Record<string, unknown>,
): ChatItem[] {
  const decision = stringValue(payload.decision);
  const answer = stringValue(payload.answer);
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    const content = item.content.map((part) => {
      if (!isToolCallPart(part) || part.toolCallId !== approvalId) {
        return part;
      }
      return {
        ...part,
        args: jsonArgs({
          ...asRecord(part.args),
          approval_id: approvalId,
          status,
        }),
        result: {
          approval_id: approvalId,
          status,
          decision: decision ?? status,
          answer,
        },
      };
    });
    if (content === item.content) {
      return item;
    }
    const messageStatus =
      item.status?.type === "requires-action" && !hasPendingAction(content)
        ? ({ type: "running" } satisfies AssistantMessageStatus)
        : item.status;
    return { ...item, content, status: messageStatus };
  });
}

export function resolveActionFromPayload(
  items: ChatItem[],
  payload: Record<string, unknown>,
): ChatItem[] {
  const approvalId = stringValue(payload.approval_id);
  const status = stringValue(payload.status);
  if (approvalId === null || status === null) {
    return items;
  }
  // ask_a_question is a question-to-user, not a permission gate. The backend
  // emits status="answered"/"skipped" for that kind so we route those payloads
  // through the question-aware resolver instead of the approve/reject path.
  if (
    QUESTION_RESOLUTION_STATUSES.has(status) ||
    stringValue(payload.approval_kind) === "ask_a_question"
  ) {
    return resolveQuestionFromPayload(items, approvalId, status, payload);
  }
  if (!isApprovalDecision(status)) {
    return items;
  }
  const hasMcpAuthAction = items.some(
    (item) =>
      item.kind === "message" &&
      item.content.some(
        (part) =>
          isToolCallPart(part) &&
          part.toolName === "mcp_auth_required" &&
          part.toolCallId === approvalId,
      ),
  );
  if (hasMcpAuthAction) {
    return resolveMcpAuthDecision(items, approvalId, status);
  }
  return resolveApprovalDecision(items, approvalId, status);
}

function isApprovalDecision(value: string): value is ApprovalDecision {
  return APPROVAL_DECISION_STATUSES.has(value);
}
