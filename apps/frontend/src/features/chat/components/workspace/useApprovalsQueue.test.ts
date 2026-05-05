// PR 3.2 — useApprovalsQueue projection.

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChatItem, ThreadToolCallPart } from "../../chatModel/types";
import { useApprovalsQueue } from "./useApprovalsQueue";

function approvalPart(
  approvalId: string,
  result?: Record<string, unknown>,
): ThreadToolCallPart {
  return {
    type: "tool-call",
    toolCallId: approvalId,
    toolName: "approval_request",
    args: {
      approval_id: approvalId,
      title: "Send draft to #launch-aurora",
      summary: "Atlas wants to post the draft to Slack.",
      target_connector: "slack",
    },
    argsText: "approval",
    ...(result !== undefined ? { result } : {}),
  };
}

function assistantMessage(
  id: string,
  parts: ThreadToolCallPart[],
  runId = `run-${id}`,
): ChatItem {
  return {
    id,
    kind: "message",
    role: "assistant",
    runId,
    content: parts,
  };
}

describe("useApprovalsQueue", () => {
  it("returns empty pending + recent for an empty thread", () => {
    const { result } = renderHook(() => useApprovalsQueue([]));
    expect(result.current.pending).toEqual([]);
    expect(result.current.recent).toEqual([]);
  });

  it("classifies unresolved approval_request parts as pending", () => {
    const items: ChatItem[] = [assistantMessage("m1", [approvalPart("ap-1")])];
    const { result } = renderHook(() => useApprovalsQueue(items));
    expect(result.current.pending).toHaveLength(1);
    expect(result.current.pending[0]).toMatchObject({
      approvalId: "ap-1",
      target: "slack",
      runId: "run-m1",
      messageId: "m1",
    });
    expect(result.current.recent).toEqual([]);
  });

  it("filters resolved approvals into recent within the window", () => {
    const recentResolved = approvalPart("ap-old", {
      decided_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      status: "approved",
    });
    const items: ChatItem[] = [assistantMessage("m1", [recentResolved])];
    const { result } = renderHook(() =>
      useApprovalsQueue(items, { recentWindowMs: 60 * 60_000 }),
    );
    expect(result.current.pending).toEqual([]);
    expect(result.current.recent).toHaveLength(1);
  });

  it("drops resolved approvals older than the recent window", () => {
    const stale = approvalPart("ap-stale", {
      decided_at: new Date(Date.now() - 4 * 60 * 60_000).toISOString(),
      status: "approved",
    });
    const items: ChatItem[] = [assistantMessage("m1", [stale])];
    const { result } = renderHook(() =>
      useApprovalsQueue(items, { recentWindowMs: 60 * 60_000 }),
    );
    expect(result.current.recent).toHaveLength(0);
  });

  it("handles mcp_auth_required as the mcp_auth kind", () => {
    const part: ThreadToolCallPart = {
      type: "tool-call",
      toolCallId: "ap-2",
      toolName: "mcp_auth_required",
      args: {
        approval_id: "ap-2",
        server_name: "Linear",
      },
      argsText: "mcp",
    };
    const items: ChatItem[] = [assistantMessage("m1", [part])];
    const { result } = renderHook(() => useApprovalsQueue(items));
    expect(result.current.pending).toHaveLength(1);
    expect(result.current.pending[0].approvalKind).toBe("mcp_auth");
    expect(result.current.pending[0].target).toBe("Linear");
  });

  it("ignores user messages and status items", () => {
    const items: ChatItem[] = [
      {
        id: "u1",
        kind: "message",
        role: "user",
        content: [{ type: "text", text: "hi" }],
      },
      { id: "s1", kind: "status", title: "Note" },
    ];
    const { result } = renderHook(() => useApprovalsQueue(items));
    expect(result.current.pending).toEqual([]);
    expect(result.current.recent).toEqual([]);
  });
});
