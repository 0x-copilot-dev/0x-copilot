// PR 3.3 — chain-final reducer tagging.
//
// When the leaf approval resolves we tag the *parent* card's result with
// the leaf decision + decider so ApprovalTool can render the chain-final
// inline record ("Approved by @marcus at 10:45 · Forwarded by @sarah at
// 10:41") without a wire change. The parent's wire-level status remains
// "forwarded" — the leaf annotations live alongside the existing fields.
//
// We stage the canonical PR 1.4 sequence and assert the parent's result
// shape after the leaf event lands.

import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import { describe, expect, it } from "vitest";
import { applyRuntimeEvent } from "./chatModel/eventReducer";
import type { ChatItem } from "./chatModel/types";
import { isToolCallPart } from "./chatModel/recordHelpers";
import { asRecord, stringValue } from "./utils/jsonUtils";

function event(overrides: Partial<RuntimeEventEnvelope>): RuntimeEventEnvelope {
  return {
    event_id: "event_123",
    run_id: "run_123",
    conversation_id: "conversation_123",
    sequence_no: 1,
    event_type: "progress",
    activity_kind: "event",
    status: "running",
    payload: {},
    created_at: "2026-05-05T00:00:00Z",
    ...overrides,
  };
}

function findApprovalPart(
  items: ChatItem[],
  approvalId: string,
): { args: Record<string, unknown>; result: Record<string, unknown> } | null {
  for (const item of items) {
    if (item.kind !== "message") continue;
    for (const part of item.content) {
      if (
        isToolCallPart(part) &&
        part.toolName === "approval_request" &&
        part.toolCallId === approvalId
      ) {
        return {
          args: asRecord(part.args),
          result: asRecord(part.result),
        };
      }
    }
  }
  return null;
}

describe("chain-final transform (PR 3.3)", () => {
  it("tags the parent's result with the leaf decision + decider", () => {
    let items: ChatItem[] = [];

    // 1. Parent approval requested (Sarah's "Post to #announcements?").
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_req_parent",
        sequence_no: 1,
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_parent",
          approval_kind: "mcp_tool",
          tool_name: "post_to_slack",
          server_name: "slack",
          display_name: "Slack",
          message: "Send draft to #announcements?",
        },
      }),
    );

    // 2. Sarah forwards: parent approval_resolved with status=forwarded.
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_resolved_parent_forward",
        sequence_no: 2,
        event_type: "approval_resolved",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_parent",
          status: "forwarded",
        },
      }),
    );

    // 3. approval_forwarded annotates the parent with recipient + child.
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_forwarded_1",
        sequence_no: 3,
        event_type: "approval_forwarded",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          chain_parent_approval_id: "appr_parent",
          forwarded_by_user_id: "sarah",
          forwarded_to_user_id: "marcus",
          forwarded_at: "2026-05-05T10:41:00Z",
          action_summary: "Send draft to #announcements",
        },
      }),
    );

    // Sanity: the parent now reads "Waiting on @marcus" — the existing
    // PR 1.4 forwarded behavior. No leaf decision annotated yet.
    let parent = findApprovalPart(items, "appr_parent");
    expect(parent).not.toBeNull();
    expect(stringValue(parent?.result.status)).toBe("forwarded");
    expect(parent?.result.chain_leaf_decision).toBeUndefined();

    // 4. Child approval requested (the new pending row Marcus sees).
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_req_child",
        sequence_no: 4,
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          approval_kind: "mcp_tool",
          tool_name: "post_to_slack",
          server_name: "slack",
          display_name: "Slack",
          message: "Send draft to #announcements? (Forwarded by @sarah)",
        },
      }),
    );

    // 5. Marcus approves the leaf — chain_parent_approval_id is set.
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_resolved_child_approve",
        sequence_no: 5,
        event_type: "approval_resolved",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          status: "approved",
          decided_by_user_id: "marcus",
          decided_at: "2026-05-05T10:45:00Z",
          chain_parent_approval_id: "appr_parent",
        },
      }),
    );

    // 6. Parent now carries the chain-final annotations alongside the
    // existing forwarded-pill data — ApprovalTool picks them up to
    // render "Approved by @marcus at 10:45 · forwarded by @sarah at
    // 10:41" without any modal.
    parent = findApprovalPart(items, "appr_parent");
    expect(parent).not.toBeNull();
    expect(stringValue(parent?.result.status)).toBe("forwarded");
    expect(stringValue(parent?.result.forwarded_to_user_id)).toBe("marcus");
    expect(stringValue(parent?.result.forwarded_by_user_id)).toBe("sarah");
    expect(stringValue(parent?.result.chain_leaf_decision)).toBe("approved");
    expect(stringValue(parent?.result.chain_leaf_decided_by_user_id)).toBe(
      "marcus",
    );
    expect(stringValue(parent?.result.chain_leaf_decided_at)).toBe(
      "2026-05-05T10:45:00Z",
    );

    // The leaf's own card resolved too — the existing single-actor
    // path keeps working byte-identical to today.
    const child = findApprovalPart(items, "appr_child");
    expect(child).not.toBeNull();
    expect(stringValue(child?.result.decision)).toBe("approved");
  });

  it("propagates the leaf reject decision to the parent", () => {
    let items: ChatItem[] = [];

    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_req_parent",
        sequence_no: 1,
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_parent",
          approval_kind: "mcp_tool",
          tool_name: "post_to_slack",
          message: "Send draft?",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_resolved_parent_forward",
        sequence_no: 2,
        event_type: "approval_resolved",
        activity_kind: "approval",
        payload: { approval_id: "appr_parent", status: "forwarded" },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_forwarded_1",
        sequence_no: 3,
        event_type: "approval_forwarded",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          chain_parent_approval_id: "appr_parent",
          forwarded_by_user_id: "sarah",
          forwarded_to_user_id: "marcus",
          forwarded_at: "2026-05-05T10:41:00Z",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_req_child",
        sequence_no: 4,
        event_type: "approval_requested",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          approval_kind: "mcp_tool",
          tool_name: "post_to_slack",
        },
      }),
    );
    items = applyRuntimeEvent(
      items,
      event({
        event_id: "approval_resolved_child_reject",
        sequence_no: 5,
        event_type: "approval_resolved",
        activity_kind: "approval",
        payload: {
          approval_id: "appr_child",
          status: "rejected",
          decided_by_user_id: "marcus",
          chain_parent_approval_id: "appr_parent",
        },
      }),
    );

    const parent = findApprovalPart(items, "appr_parent");
    expect(stringValue(parent?.result.chain_leaf_decision)).toBe("rejected");
    expect(stringValue(parent?.result.chain_leaf_decided_by_user_id)).toBe(
      "marcus",
    );
  });
});
