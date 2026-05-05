// PR 3.3 — workspace member lookup.
//
// Round-trips ``GET /v1/workspace/members/{user_id}`` through
// backend-facade. The facade route is a tightly-scoped follow-up;
// until it ships the endpoint returns 404 and the consuming hook
// (``useWorkspaceMember``) falls back to the raw user_id. Adding the
// route later turns the chip into a real name with no FE change.

import { httpGet } from "./http";
import type { RequestIdentity } from "./config";

export interface WorkspaceMemberResponse {
  user_id: string;
  display_name: string;
  email?: string | null;
  handle?: string | null;
}

export function getWorkspaceMember(
  userId: string,
  identity: RequestIdentity,
): Promise<WorkspaceMemberResponse> {
  return httpGet<WorkspaceMemberResponse>(
    `/v1/workspace/members/${encodeURIComponent(userId)}`,
    identity,
  );
}
