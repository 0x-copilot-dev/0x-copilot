import type { WorkspaceListResponse } from "@enterprise-search/api-types";
import { assertOk, correlationHeaders } from "./http";

/**
 * Caller-scoped reads under `/v1/me/*`.
 *
 * Today only `listMyWorkspaces` is wired (PR 2.2 sidebar UserCard). The
 * facade derives identity from the bearer header, so this client passes
 * no body / params — auth is the cookie / `Authorization` flowing through
 * `apps/frontend/src/api/http.ts`.
 */
export async function listMyWorkspaces(): Promise<WorkspaceListResponse> {
  const response = await fetch("/v1/me/workspaces", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as WorkspaceListResponse;
}
