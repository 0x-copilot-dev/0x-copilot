import type {
  CreateMcpServerRequest,
  McpAuthRequiredEventPayload,
  McpAuthStartResponse,
  McpServer,
  McpServerListResponse
} from "@enterprise-search/api-types";

const DEFAULT_ORG_ID = "org_123";
const DEFAULT_USER_ID = "user_123";

export async function listMcpServers(): Promise<McpServer[]> {
  const params = new URLSearchParams({ org_id: DEFAULT_ORG_ID, user_id: DEFAULT_USER_ID });
  const response = await fetch(`/v1/mcp/servers?${params}`);
  assertOk(response);
  const payload = (await response.json()) as McpServerListResponse;
  return payload.servers;
}

export async function createMcpServer(url: string): Promise<McpServer> {
  const payload: CreateMcpServerRequest = {
    org_id: DEFAULT_ORG_ID,
    user_id: DEFAULT_USER_ID,
    url
  };
  const response = await fetch("/v1/mcp/servers", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload)
  });
  assertOk(response);
  return (await response.json()) as McpServer;
}

export async function startMcpAuth(serverId: string): Promise<McpAuthStartResponse> {
  const response = await fetch(`/v1/mcp/servers/${serverId}/auth/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      org_id: DEFAULT_ORG_ID,
      user_id: DEFAULT_USER_ID,
      redirect_uri: `${window.location.origin}/mcp/oauth/callback`
    })
  });
  assertOk(response);
  return (await response.json()) as McpAuthStartResponse;
}

export async function skipMcpAuth(serverId: string): Promise<McpServer> {
  const params = new URLSearchParams({ org_id: DEFAULT_ORG_ID, user_id: DEFAULT_USER_ID });
  const response = await fetch(`/v1/mcp/servers/${serverId}/auth/skip?${params}`, {
    method: "POST"
  });
  assertOk(response);
  return (await response.json()) as McpServer;
}

export function isMcpAuthRequiredPayload(
  payload: unknown
): payload is McpAuthRequiredEventPayload {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return (
    typeof candidate.server_id === "string" &&
    typeof candidate.display_name === "string" &&
    typeof candidate.auth_url === "string" &&
    typeof candidate.expires_at === "string"
  );
}

function assertOk(response: Response): void {
  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`);
  }
}
