export interface PendingMcpAuthAction {
  approvalId: string;
  serverId: string;
  runId: string | null;
  createdAt: string;
}

export interface CompletedMcpAuthAction extends PendingMcpAuthAction {
  completedAt: string;
}

const pendingMcpAuthActionKey = "0x-copilot.pending-mcp-auth-action";

export function rememberPendingMcpAuthAction(action: {
  approvalId: string;
  serverId: string;
}): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(
    pendingMcpAuthActionKey,
    JSON.stringify({
      ...action,
      runId: runIdFromMcpAuthApprovalId(action.approvalId),
      createdAt: new Date().toISOString(),
    } satisfies PendingMcpAuthAction),
  );
}

export function readPendingMcpAuthAction(
  serverId: string,
): PendingMcpAuthAction | null {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.sessionStorage.getItem(pendingMcpAuthActionKey);
  if (!stored) {
    return null;
  }
  const action = parsePendingMcpAuthAction(stored);
  if (action === null || action.serverId !== serverId) {
    return null;
  }
  return action;
}

export function clearPendingMcpAuthAction(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(pendingMcpAuthActionKey);
}

export function runIdFromMcpAuthApprovalId(approvalId: string): string | null {
  // Two prefixes round-trip through this helper:
  //   ``mcp_auth:<run_id>:<server_id>``       — blocking auth gate
  //   ``mcp_discovery:<run_id>:<server_id>``  — Phase 2 catalog suggestion
  // Both share the ``<prefix>:<run_id>:<rest>`` shape, so we accept
  // either prefix rather than special-casing one. Returning null for
  // an unfamiliar prefix lets callers degrade safely (the App.tsx
  // OAuth callback only uses runId to scope the chat route).
  const parts = approvalId.split(":");
  if (parts.length < 3 || !parts[1]) {
    return null;
  }
  return parts[0] === "mcp_auth" || parts[0] === "mcp_discovery"
    ? parts[1]
    : null;
}

function parsePendingMcpAuthAction(value: string): PendingMcpAuthAction | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const record = parsed as Record<string, unknown>;
    if (
      typeof record.approvalId !== "string" ||
      typeof record.serverId !== "string" ||
      typeof record.createdAt !== "string"
    ) {
      return null;
    }
    return {
      approvalId: record.approvalId,
      serverId: record.serverId,
      runId:
        typeof record.runId === "string"
          ? record.runId
          : runIdFromMcpAuthApprovalId(record.approvalId),
      createdAt: record.createdAt,
    };
  } catch {
    return null;
  }
}
