import type { SessionResponse } from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { assertOk } from "./http";

export async function getSessionIdentity(): Promise<RequestIdentity> {
  const response = await fetch("/v1/session");
  await assertOk(response);
  const payload = (await response.json()) as SessionResponse;
  return {
    orgId: payload.identity.org_id,
    userId: payload.identity.user_id,
  };
}
