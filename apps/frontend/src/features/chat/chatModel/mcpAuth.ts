import type { ApprovalDecision, McpServer } from "@enterprise-search/api-types";
import type { MessageStatus as AssistantMessageStatus } from "../runtime/types";
import { asRecord, stringValue } from "../utils/jsonUtils";
import { isToolCallPart, jsonArgs, sameText } from "./recordHelpers";
import { hasPendingAction } from "./status";
import type {
  ChatItem,
  ThreadMessageContent,
  ThreadToolCallPart,
} from "./types";

export function resolveMcpAuthSkip(
  items: ChatItem[],
  actionId: string,
): ChatItem[] {
  return resolveMcpAuthDecision(items, actionId, "rejected", "skipped");
}

export function resolveAuthenticatedMcpServers(
  items: ChatItem[],
  servers: readonly McpServer[],
): ChatItem[] {
  const authenticated = servers.filter(
    (server) => server.auth_state === "authenticated",
  );
  if (authenticated.length === 0) {
    return items;
  }
  // Reference-stable: when nothing resolves, return the original
  // `items` ref so callers (the ChatScreen effect) can safely include
  // `items` in their deps without an infinite render loop. Without
  // this, `items.map(...)` always allocates a new array even when
  // every entry is the same ref, which fails React's `Object.is` bail.
  let anyChanged = false;
  const next = items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    let changed = false;
    const resolvedContent = item.content.map((part) => {
      if (!isToolCallPart(part)) {
        return part;
      }
      const resolvedPart = resolveAuthenticatedMcpPart(part, authenticated);
      if (resolvedPart !== part) {
        changed = true;
      }
      return resolvedPart;
    });
    const content = removeRedundantMcpAuthWrappers(resolvedContent);
    if (content !== resolvedContent) {
      changed = true;
    }
    if (!changed) {
      return item;
    }
    anyChanged = true;
    const status =
      item.status?.type === "requires-action" && !hasPendingAction(content)
        ? ({ type: "running" } satisfies AssistantMessageStatus)
        : item.status;
    return { ...item, content, status };
  });
  return anyChanged ? next : items;
}

export function removeRedundantMcpAuthWrappers(
  content: ThreadMessageContent,
): ThreadMessageContent {
  const authCards = content.filter(
    (part): part is ThreadToolCallPart =>
      isToolCallPart(part) && part.toolName === "mcp_auth_required",
  );
  if (authCards.length === 0) {
    return content;
  }
  const filtered = content.filter((part) => {
    if (!isToolCallPart(part) || part.toolName !== "auth_mcp") {
      return true;
    }
    const args = asRecord(part.args);
    return !authCards.some((authCard) =>
      mcpAuthPayloadMatchesArgs(asRecord(authCard.args), args),
    );
  });
  return filtered.length === content.length ? content : filtered;
}

export function resolveAuthenticatedMcpPart(
  part: ThreadToolCallPart,
  authenticated: readonly McpServer[],
): ThreadToolCallPart {
  if (part.toolName === "mcp_auth_required") {
    if (part.result !== undefined) {
      return part;
    }
    const args = asRecord(part.args);
    const server = authenticated.find((candidate) =>
      mcpAuthPartMatchesServer(args, candidate),
    );
    if (server === undefined) {
      return part;
    }
    const approvalId = stringValue(args.approval_id) ?? part.toolCallId;
    return {
      ...part,
      args: jsonArgs({
        ...args,
        presentation: null,
        approval_id: approvalId,
        server_id: server.server_id,
        status: "approved",
      }),
      result: {
        approval_id: approvalId,
        server_id: server.server_id,
        decision: "approved",
      },
    };
  }
  if (part.toolName !== "auth_mcp") {
    return part;
  }
  const args = asRecord(part.args);
  const server = authenticated.find((candidate) =>
    mcpAuthPartMatchesServer(args, candidate),
  );
  if (server === undefined) {
    return part;
  }
  const status = stringValue(args.status);
  if (status === "completed" && part.result !== undefined) {
    return part;
  }
  return {
    ...part,
    args: jsonArgs({
      ...args,
      server_id: server.server_id,
      server_name: server.name,
      display_name: server.display_name,
      status: "completed",
    }),
    result:
      part.result ??
      ({
        ok: true,
        server_id: server.server_id,
        server_name: server.name,
        display_name: server.display_name,
        status: "connected",
        message: `${server.display_name} is connected.`,
      } satisfies Record<string, unknown>),
    isError: false,
  };
}

export function resolveMcpAuthDecision(
  items: ChatItem[],
  actionId: string,
  decision: ApprovalDecision,
  resultDecision: ApprovalDecision | "skipped" = decision,
): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "message") {
      return item;
    }
    let changed = false;
    const content = item.content.map((part) => {
      if (
        !isToolCallPart(part) ||
        part.toolCallId !== actionId ||
        part.toolName !== "mcp_auth_required"
      ) {
        return part;
      }
      const args = asRecord(part.args);
      const serverId = stringValue(args.server_id);
      changed = true;
      return {
        ...part,
        args: jsonArgs({
          ...args,
          presentation: null,
          approval_id: actionId,
          server_id: serverId,
          status: resultDecision,
        }),
        result: {
          approval_id: actionId,
          server_id: serverId,
          decision: resultDecision,
        },
      };
    });
    if (!changed) {
      return item;
    }
    const status =
      item.status?.type === "requires-action" && !hasPendingAction(content)
        ? ({ type: "running" } satisfies AssistantMessageStatus)
        : item.status;
    return { ...item, content, status };
  });
}

export function mcpAuthPartMatchesServer(
  args: Record<string, unknown>,
  server: McpServer,
): boolean {
  const serverId = stringValue(args.server_id);
  if (serverId !== null && serverId === server.server_id) {
    return true;
  }
  const serverName = stringValue(args.server_name);
  if (serverName !== null && serverName === server.name) {
    return true;
  }
  const displayName = stringValue(args.display_name);
  return displayName !== null && displayName === server.display_name;
}

export function mcpApprovalMatchesWrapper(
  part: ThreadToolCallPart,
  payload: Record<string, unknown>,
): boolean {
  if (part.toolName !== "call_mcp_tool") {
    return false;
  }
  const args = asRecord(part.args);
  return (
    sameText(args.server_name, payload.server_name) &&
    sameText(args.tool_name, payload.tool_name)
  );
}

export function mcpAuthMatchesWrapper(
  part: ThreadToolCallPart,
  payload: Record<string, unknown>,
): boolean {
  if (part.toolName !== "auth_mcp") {
    return false;
  }
  return mcpAuthPayloadMatchesArgs(payload, asRecord(part.args));
}

export function mcpAuthPayloadMatchesArgs(
  payload: Record<string, unknown>,
  args: Record<string, unknown>,
): boolean {
  return (
    sameText(args.server_id, payload.server_id) ||
    sameText(args.server_name, payload.server_name) ||
    sameText(args.display_name, payload.display_name)
  );
}
