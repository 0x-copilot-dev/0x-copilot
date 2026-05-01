export interface PendingMcpAuthAction {
  approvalId: string;
  serverId: string;
  createdAt: string;
}

const pendingMcpAuthActionKey = "enterprise-search.pending-mcp-auth-action";

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
      createdAt: record.createdAt,
    };
  } catch {
    return null;
  }
}
