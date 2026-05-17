import { httpJson } from "./http";

/**
 * W0.1 — Dev IdP client.
 *
 * Two endpoints, both proxied by the facade in development only:
 *   - GET  /v1/dev/personas       — list personas (FE switcher).
 *   - POST /v1/dev/identity/mint  — mint a real HMAC bearer.
 *
 * Production builds tree-shake everything here — every caller is gated
 * by `import.meta.env.DEV`.
 *
 * Lives in `api/*` (not next to the persona-slug persistence helpers in
 * `features/auth/devIdp.ts`) so the CLAUDE.md rule "all HTTP clients
 * live in src/api/*" holds.
 */

export interface DevPersonaSummary {
  slug: string;
  display_name: string;
  primary_email: string;
  org_id: string;
  org_slug: string;
  roles: string[];
  permission_scopes: string[];
}

export interface DevMintResponse {
  bearer: string;
  expires_at: string;
  persona_slug: string;
  identity: {
    org_id: string;
    user_id: string;
    display_name: string;
    primary_email: string;
    roles: string[];
    permission_scopes: string[];
  };
}

export async function listDevPersonas(): Promise<DevPersonaSummary[]> {
  const body = await httpJson<{ personas: DevPersonaSummary[] }>(
    "GET",
    "/v1/dev/personas",
  );
  return body.personas ?? [];
}

export function mintDevBearer(personaSlug: string): Promise<DevMintResponse> {
  return httpJson<DevMintResponse>("POST", "/v1/dev/identity/mint", {
    persona_slug: personaSlug,
  });
}
