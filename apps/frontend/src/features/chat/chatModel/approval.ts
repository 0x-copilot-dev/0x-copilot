import type {
  ApprovalDecision,
  ApprovalForwardedPayload,
} from "@enterprise-search/api-types";
import type { MessageStatus as AssistantMessageStatus } from "@assistant-ui/react";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { resolveMcpAuthDecision } from "./mcpAuth";
import { isToolCallPart, jsonArgs } from "./recordHelpers";
import { hasPendingAction } from "./status";
import type { ChatItem } from "./types";

const APPROVAL_DECISION_STATUSES = new Set<string>(["approved", "rejected"]);
const QUESTION_RESOLUTION_STATUSES = new Set<string>(["answered", "skipped"]);
// PR 1.4 — terminal status for the parent row of a forwarded chain. It's
// not part of APPROVAL_DECISION_STATUSES because it does not resolve the
// underlying tool call (the child does); it only repaints the inline card.
const FORWARDED_STATUS = "forwarded";

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
      //
      // `presentation` is cleared on resolution: the snapshot taken when the
      // approval was *requested* pinned status_label="Waiting for permission",
      // and the card UI prefers presentation.status_label over the computed
      // resolved status. Clearing it lets ApprovalTool's fallback render
      // "Permission approved"/"Done" once result is set.
      if (stringValue(args.approval_kind) === "ask_a_question") {
        const status = decision === "approved" ? "answered" : "skipped";
        return {
          ...part,
          args: jsonArgs({
            ...args,
            presentation: null,
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
          presentation: null,
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
          presentation: null,
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
  // PR 1.4 — forwarded parents repaint the inline card with a "Waiting on
  // …" pill instead of resolving the action. The recipient's id + caption
  // arrive next via the trailing approval_forwarded event.
  if (status === FORWARDED_STATUS) {
    return forwardApprovalDecision(items, approvalId);
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

// PR 1.4 — flip the parent card to a "Waiting on someone" pill on the
// status=forwarded approval_resolved event. We don't have the recipient
// id yet (it arrives in the trailing approval_forwarded event), so we
// just clear the pending action and stash the forwarded status.
function forwardApprovalDecision(
  items: ChatItem[],
  approvalId: string,
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
          presentation: null,
          approval_id: approvalId,
          status: FORWARDED_STATUS,
        }),
        // `result !== undefined` clears the requires-action gate on the
        // assistant message so the run state machine can advance, but the
        // actual tool call is still pending the leaf approver's decision.
        result: { approval_id: approvalId, status: FORWARDED_STATUS },
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

// PR 1.4 — second pass once the trailing approval_forwarded event lands.
// Annotates the inline card with the forward target (so the FE can show
// "Waiting on @marcus · forwarded by you · 10:41" without an extra fetch).
export function forwardActionFromPayload(
  items: ChatItem[],
  payload: ApprovalForwardedPayload,
): ChatItem[] {
  const parentId = payload.chain_parent_approval_id;
  if (typeof parentId !== "string" || !parentId) {
    return items;
  }
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    const content = item.content.map((part) => {
      if (!isToolCallPart(part) || part.toolCallId !== parentId) {
        return part;
      }
      return {
        ...part,
        args: jsonArgs({
          ...asRecord(part.args),
          presentation: null,
          status: FORWARDED_STATUS,
          forwarded_to_user_id: payload.forwarded_to_user_id,
          forwarded_by_user_id: payload.forwarded_by_user_id,
          forwarded_at: payload.forwarded_at,
          child_approval_id: payload.approval_id,
        }),
        result: {
          approval_id: parentId,
          status: FORWARDED_STATUS,
          forwarded_to_user_id: payload.forwarded_to_user_id,
          forwarded_by_user_id: payload.forwarded_by_user_id,
          forwarded_at: payload.forwarded_at,
          child_approval_id: payload.approval_id,
        },
      };
    });
    if (content === item.content) {
      return item;
    }
    return { ...item, content };
  });
}

function isApprovalDecision(value: string): value is ApprovalDecision {
  return APPROVAL_DECISION_STATUSES.has(value);
}
