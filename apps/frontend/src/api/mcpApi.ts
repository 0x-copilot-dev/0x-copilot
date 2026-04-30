import type {
  CreateMcpServerRequest,
  McpAuthStartResponse,
  McpServer,
  McpServerListResponse,
  UpdateMcpServerRequest
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { identityParams } from "./config";
import { assertOk, jsonHeaders } from "./http";

export async function listMcpServers(identity: RequestIdentity): Promise<McpServer[]> {
  const response = await fetch(`/v1/mcp/servers?${identityParams(identity)}`);
  await assertOk(response);
  const payload = (await response.json()) as McpServerListResponse;
  return payload.servers;
}

export async function createMcpServer(
  url: string,
  identity: RequestIdentity
): Promise<McpServer> {
  const payload: CreateMcpServerRequest = {
    org_id: identity.orgId,
    user_id: identity.userId,
    url
  };
  const response = await fetch("/v1/mcp/servers", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as McpServer;
}

export async function updateMcpServer(
  serverId: string,
  payload: UpdateMcpServerRequest,
  identity: RequestIdentity
): Promise<McpServer> {
  const response = await fetch(`/v1/mcp/servers/${serverId}?${identityParams(identity)}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(payload)
  });
  await assertOk(response);
  return (await response.json()) as McpServer;
}

export async function deleteMcpServer(
  serverId: string,
  identity: RequestIdentity
): Promise<void> {
  const response = await fetch(`/v1/mcp/servers/${serverId}?${identityParams(identity)}`, {
    method: "DELETE"
  });
  await assertOk(response);
}

export async function startMcpAuth(
  serverId: string,
  identity: RequestIdentity
): Promise<McpAuthStartResponse> {
  const response = await fetch(`/v1/mcp/servers/${serverId}/auth/start`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({
      org_id: identity.orgId,
      user_id: identity.userId,
      redirect_uri: `${window.location.origin}/mcp/oauth/callback`
    })
  });
  await assertOk(response);
  return (await response.json()) as McpAuthStartResponse;
}

export async function skipMcpAuth(
  serverId: string,
  identity: RequestIdentity
): Promise<McpServer> {
  const response = await fetch(`/v1/mcp/servers/${serverId}/auth/skip?${identityParams(identity)}`, {
    method: "POST"
  });
  await assertOk(response);
  return (await response.json()) as McpServer;
}

export async function completeMcpOAuth(state: string, code: string): Promise<McpServer> {
  const params = new URLSearchParams({ state, code });
  const response = await fetch(`/v1/mcp/oauth/callback?${params}`);
  await assertOk(response);
  return (await response.json()) as McpServer;
}
