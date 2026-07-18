// PR 3.2 — pure projection over the existing ChatItem[] for the
// Approvals tab. No fetch, no new state — every approval is already a
// `tool-call` part on an assistant message. We just classify them.
//
// Pending  → unresolved approval_request / mcp_auth_required parts.
// Recent   → resolved within the last `recentWindowMs` (default 60min).
// Older resolutions are dropped from the tab; the inline thread record
// remains the durable artifact (per Atlas's "approvals as content" rule).

import { useMemo } from "react";

import type { ChatItem, ThreadToolCallPart } from "../../chatModel/types";
import { stringValue } from "../../utils/jsonUtils";

export interface ApprovalsQueueItem {
  approvalId: string;
  /** Tool action's "what does this do" copy. */
  title: string;
  /** Optional sub-line; pulled from `args.summary` / `args.message`. */
  summary: string | null;
  approvalKind:
    | "tool_action"
    | "mcp_tool"
    | "mcp_auth"
    | "ask_a_question"
    | "unknown";
  /** Run id the approval belongs to (so click-to-jump can locate the message). */
  runId: string | null;
  /** Message id the approval part lives on. */
  messageId: string;
  /** Resolved? When true the row is in `recent`. */
  resolved: boolean;
  /** ISO timestamp of resolution; null when pending. */
  resolvedAt: string | null;
  /** Connector / target preview ("#launch-aurora", "Notion / Drafts"). */
  target: string | null;
}

export interface ApprovalsQueueProjection {
  pending: readonly ApprovalsQueueItem[];
  recent: readonly ApprovalsQueueItem[];
}

const DEFAULT_RECENT_WINDOW_MS = 60 * 60 * 1000;

export function useApprovalsQueue(
  items: readonly ChatItem[],
  options: { recentWindowMs?: number; nowMs?: number } = {},
): ApprovalsQueueProjection {
  const recentWindowMs = options.recentWindowMs ?? DEFAULT_RECENT_WINDOW_MS;
  const nowMs = options.nowMs;

  return useMemo(() => {
    const pending: ApprovalsQueueItem[] = [];
    const recent: ApprovalsQueueItem[] = [];
    const cutoffMs = (nowMs ?? Date.now()) - recentWindowMs;

    for (const item of items) {
      if (item.kind !== "message" || item.role !== "assistant") {
        continue;
      }
      const runId = item.runId ?? null;
      const messageId = item.id;
      for (const part of item.content) {
        if (
          part.type !== "tool-call" ||
          (part.toolName !== "approval_request" &&
            part.toolName !== "mcp_auth_required")
        ) {
          continue;
        }
        const projected = projectPart(part, runId, messageId);
        if (projected === null) {
          continue;
        }
        if (!projected.resolved) {
          pending.push(projected);
        } else {
          const ts = projected.resolvedAt
            ? Date.parse(projected.resolvedAt)
            : NaN;
          if (Number.isFinite(ts) && ts >= cutoffMs) {
            recent.push(projected);
          }
        }
      }
    }

    // Newest first within each list.
    recent.sort((a, b) => parseTime(b.resolvedAt) - parseTime(a.resolvedAt));
    return { pending, recent };
  }, [items, recentWindowMs, nowMs]);
}

function projectPart(
  part: ThreadToolCallPart,
  runId: string | null,
  messageId: string,
): ApprovalsQueueItem | null {
  const args = (part.args ?? {}) as Record<string, unknown>;
  const approvalId =
    stringValue(args.approval_id) ?? stringValue(args.action_id) ?? null;
  if (approvalId === null) {
    return null;
  }
  const resolved = part.result !== undefined;
  const result =
    resolved && part.result && typeof part.result === "object"
      ? (part.result as Record<string, unknown>)
      : null;
  const resolvedAt =
    stringValue(result?.decided_at) ??
    stringValue(result?.resolved_at) ??
    (resolved ? new Date().toISOString() : null);
  const approvalKind = normalizeKind(
    stringValue(args.approval_kind) ?? stringValue(args.kind) ?? null,
    part.toolName,
  );
  const title =
    stringValue(args.title) ??
    stringValue(args.display_title) ??
    stringValue(args.tool_name) ??
    fallbackTitle(approvalKind);
  const summary =
    stringValue(args.summary) ??
    stringValue(args.message) ??
    stringValue(args.reason) ??
    null;
  const target =
    stringValue(args.target) ??
    stringValue(args.target_connector) ??
    stringValue(args.server_name) ??
    null;
  return {
    approvalId,
    title,
    summary,
    approvalKind,
    runId,
    messageId,
    resolved,
    resolvedAt,
    target,
  };
}

function normalizeKind(
  raw: string | null,
  toolName: string,
): ApprovalsQueueItem["approvalKind"] {
  if (toolName === "mcp_auth_required") {
    return "mcp_auth";
  }
  if (raw === "mcp_tool") {
    return "mcp_tool";
  }
  if (raw === "ask_a_question") {
    return "ask_a_question";
  }
  if (raw === "tool_action" || raw === "action") {
    return "tool_action";
  }
  return "unknown";
}

function fallbackTitle(kind: ApprovalsQueueItem["approvalKind"]): string {
  switch (kind) {
    case "mcp_auth":
      return "Connect a connector";
    case "mcp_tool":
      return "Allow a connector action";
    case "ask_a_question":
      return "Copilot needs an answer";
    case "tool_action":
      return "Approve action";
    default:
      return "Pending approval";
  }
}

function parseTime(iso: string | null): number {
  if (iso === null) {
    return -Infinity;
  }
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : -Infinity;
}
