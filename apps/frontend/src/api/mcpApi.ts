import type {
  CreateMcpServerRequest,
  InstallMcpServerRequest,
  McpAuthStartResponse,
  McpCatalogResponse,
  McpOAuthClientConfigRequest,
  McpServer,
  McpServerListResponse,
  UpdateMcpServerRequest,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import {
  httpDelete,
  httpGet,
  httpJson,
  httpPatchQuery,
  httpPost,
  httpPostQuery,
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

// PR 4.4.6 — catalog endpoint is org-agnostic; we bypass ``httpGet`` so
// no ``org_id`` / ``user_id`` query params are appended.
export function listMcpCatalog(): Promise<McpCatalogResponse> {
  return httpJson<McpCatalogResponse>("GET", "/v1/mcp/catalog");
}

export function installMcpServer(
  slug: string,
  identity: RequestIdentity,
  oauthClient?: McpOAuthClientConfigRequest,
): Promise<McpServer> {
  const payload: InstallMcpServerRequest = {
    org_id: identity.orgId,
    user_id: identity.userId,
    slug,
  };
  if (oauthClient !== undefined) {
    payload.oauth_client = oauthClient;
  }
  return httpPost<McpServer>("/v1/mcp/servers/install", payload);
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

export function skipMcpAuth(
  serverId: string,
  identity: RequestIdentity,
): Promise<McpServer> {
  return httpPostQuery<McpServer>(
    `/v1/mcp/servers/${serverId}/auth/skip`,
    undefined,
    identity,
  );
}

export function completeMcpOAuth(
  state: string,
  code?: string | null,
  error?: string | null,
  errorDescription?: string | null,
): Promise<McpServer> {
  return httpJson<McpServer>("GET", "/v1/mcp/oauth/callback", undefined, {
    state,
    code: code ?? undefined,
    error: error ?? undefined,
    error_description: errorDescription ?? undefined,
  });
}
