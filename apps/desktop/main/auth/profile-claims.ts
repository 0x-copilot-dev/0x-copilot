import type { SessionClaims } from "./oidc-client";

// Best-effort enrichment shared by the facade-brokered sign-in flows
// (Google OIDC, wallet/SIWE): the bearer handoff carries ids only, so the
// renderer-facing display claims come from `GET /v1/me/profile` with the
// freshly minted bearer. Any failure falls back to ids-only claims — a
// missing display name must never fail a successful sign-in.

interface ProfileResponse {
  readonly user_id?: string;
  readonly email?: string | null;
  readonly display_name?: string | null;
}

export async function fetchProfileClaims(
  doFetch: typeof fetch,
  facadeBaseUrl: string,
  bearerToken: string,
  userId: string,
  workspaceId: string,
): Promise<SessionClaims> {
  const fallback: SessionClaims = {
    sub: userId,
    email: null,
    name: null,
    workspaceId,
  };
  try {
    const response = await doFetch(`${facadeBaseUrl}/v1/me/profile`, {
      method: "GET",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${bearerToken}`,
      },
    });
    if (!response.ok) return fallback;
    const profile = (await response.json()) as ProfileResponse;
    return {
      sub: userId,
      email: typeof profile.email === "string" ? profile.email : null,
      name:
        typeof profile.display_name === "string" ? profile.display_name : null,
      workspaceId,
    };
  } catch {
    return fallback;
  }
}
