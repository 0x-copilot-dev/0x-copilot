import type {
  CreateMcpServerRequest,
  McpAuthStartResponse,
  McpOAuthClientConfigRequest,
  McpServer,
  McpServerListResponse,
  UpdateMcpServerRequest,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import {
  assertOk,
  correlationHeaders,
  httpDelete,
  httpGet,
  httpPatchQuery,
  httpPost,
} from "./http";

export async function listMcpServers(
  identity: RequestIdentity,
): Promise<McpServer[]> {
  const payload = await httpGet<McpServerListResponse>(
    "/v1/mcp/servers",
    identity,
  );
  return payload.servers;
}

export function createMcpServer(
  url: string,
  identity: RequestIdentity,
  oauthClient?: McpOAuthClientConfigRequest,
): Promise<McpServer> {
  const payload: CreateMcpServerRequest = {
    org_id: identity.orgId,
    user_id: identity.userId,
    url,
  };
  if (oauthClient !== undefined) {
    payload.oauth_client = oauthClient;
  }
  return httpPost<McpServer>("/v1/mcp/servers", payload);
}

export function updateMcpServer(
  serverId: string,
  payload: UpdateMcpServerRequest,
  identity: RequestIdentity,
): Promise<McpServer> {
  return httpPatchQuery<McpServer>(
    `/v1/mcp/servers/${serverId}`,
    payload,
    identity,
  );
}

export function deleteMcpServer(
  serverId: string,
  identity: RequestIdentity,
): Promise<void> {
  return httpDelete(`/v1/mcp/servers/${serverId}`, identity);
}

export function startMcpAuth(
  serverId: string,
  identity: RequestIdentity,
): Promise<McpAuthStartResponse> {
  return httpPost<McpAuthStartResponse>(
    `/v1/mcp/servers/${serverId}/auth/start`,
    {
      org_id: identity.orgId,
      user_id: identity.userId,
      redirect_uri: `${window.location.origin}/mcp/oauth/callback`,
    },
  );
}

export async function skipMcpAuth(
  serverId: string,
  identity: RequestIdentity,
): Promise<McpServer> {
  const params = new URLSearchParams({
    org_id: identity.orgId,
    user_id: identity.userId,
  });
  const response = await fetch(
    `/v1/mcp/servers/${serverId}/auth/skip?${params}`,
    { method: "POST", headers: correlationHeaders() },
  );
  await assertOk(response);
  return (await response.json()) as McpServer;
}

export async function completeMcpOAuth(
  state: string,
  code?: string | null,
  error?: string | null,
  errorDescription?: string | null,
): Promise<McpServer> {
  const params = new URLSearchParams({ state });
  if (code) {
    params.set("code", code);
  }
  if (error) {
    params.set("error", error);
  }
  if (errorDescription) {
    params.set("error_description", errorDescription);
  }
  const response = await fetch(`/v1/mcp/oauth/callback?${params}`);
  await assertOk(response);
  return (await response.json()) as McpServer;
}
