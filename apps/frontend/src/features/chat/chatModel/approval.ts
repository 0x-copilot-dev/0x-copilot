import type { ApprovalDecision } from "@enterprise-search/api-types";
import type { MessageStatus as AssistantMessageStatus } from "@assistant-ui/react";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { resolveMcpAuthDecision } from "./mcpAuth";
import { isToolCallPart, jsonArgs } from "./recordHelpers";
import { hasPendingAction } from "./status";
import type { ChatItem } from "./types";

export function resolveApprovalDecision(
  items: ChatItem[],
  approvalId: string,
  decision: ApprovalDecision,
): ChatItem[] {
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

export function resolveActionFromPayload(
  items: ChatItem[],
  payload: Record<string, unknown>,
): ChatItem[] {
  const approvalId = stringValue(payload.approval_id);
  const status = stringValue(payload.status);
  if (approvalId === null || (status !== "approved" && status !== "rejected")) {
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
