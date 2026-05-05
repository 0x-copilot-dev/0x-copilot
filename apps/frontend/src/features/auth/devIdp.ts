/**
 * W0.1 — Dev IdP client.
 *
 * Two endpoints, both proxied by the facade in development only:
 *   - GET  /v1/dev/personas              — list personas (FE switcher).
 *   - POST /v1/dev/identity/mint         — mint a real HMAC bearer.
 *
 * Production builds tree-shake everything in this module — every caller
 * is guarded by ``import.meta.env.DEV``.
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

const DEFAULT_PERSONA_SLUG = "sarah_acme";
const PERSONA_SLUG_STORAGE_KEY = "enterprise.dev.persona_slug";

/** Read the most-recently-selected persona slug, falling back to the default. */
export function loadActivePersonaSlug(): string {
  if (typeof window === "undefined") return DEFAULT_PERSONA_SLUG;
  try {
    return (
      window.localStorage.getItem(PERSONA_SLUG_STORAGE_KEY) ??
      DEFAULT_PERSONA_SLUG
    );
  } catch {
    return DEFAULT_PERSONA_SLUG;
  }
}

export function persistActivePersonaSlug(slug: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PERSONA_SLUG_STORAGE_KEY, slug);
  } catch {
    // localStorage failure (private browsing, quota): mint will still
    // succeed but the choice won't persist across reloads.
  }
}

export async function listDevPersonas(): Promise<DevPersonaSummary[]> {
  const r = await fetch("/v1/dev/personas", { credentials: "same-origin" });
  if (!r.ok) {
    throw new Error(`dev IdP /personas failed: ${r.status}`);
  }
  const body = (await r.json()) as { personas: DevPersonaSummary[] };
  return body.personas ?? [];
}

export async function mintDevBearer(
  personaSlug: string,
): Promise<DevMintResponse> {
  const r = await fetch("/v1/dev/identity/mint", {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ persona_slug: personaSlug }),
  });
  if (!r.ok) {
    throw new Error(`dev IdP /mint failed: ${r.status}`);
  }
  return (await r.json()) as DevMintResponse;
}
