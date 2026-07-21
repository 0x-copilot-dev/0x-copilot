/**
 * Google-link callback landing (account-linking PRD FR-L2 / NFR-7 client
 * half). The web link flow starts an OAuth round-trip whose public
 * `/v1/auth/oidc/callback` completion is a LINK result, not a sign-in
 * handoff. The facade 302-redirects such link results back to this
 * same-origin landing path with the outcome in the query string (see
 * `services/backend-facade/src/backend_facade/auth_routes.py`), so the
 * browser lands in product UI instead of on raw JSON.
 *
 * This module is the shared vocabulary for that hop: the landing path, the
 * `return_to` builder used at start, and the outcome parser used on return.
 */

/** Same-origin path the facade redirects Google LINK outcomes to. */
export const GOOGLE_LINK_CALLBACK_PATH = "/oauth/link/callback";

export type GoogleLinkStatus =
  | "linked"
  | "already_linked"
  | "merge_required"
  | "error";

export interface GoogleLinkOutcome {
  readonly status: GoogleLinkStatus;
  readonly provider: string | null;
  /**
   * Whether the caller's placeholder `@wallet.invalid` email was upgraded
   * to the verified Google address. A boolean, deliberately NOT the address
   * itself — no PII rides in the URL (privacy rule).
   */
  readonly emailUpgraded: boolean;
  /** Safe in-app path to return to (defaults handled by the caller). */
  readonly returnTo: string | null;
  /** Server-supplied detail for the `error` status. */
  readonly message: string | null;
}

/**
 * Where to send the user back to after the link result is shown. Passed as
 * the OAuth `return_to`, stored server-side on the state row, and echoed to
 * the facade so it can round-trip it into the landing redirect. We capture
 * the current in-app hash route (Settings → Profile is where the CTA
 * lives) so the user returns exactly where they were.
 */
/** The in-app Settings → Profile route (fallback "back" destination). */
export const SETTINGS_PROFILE_ROUTE = "/settings#profile";

export function buildGoogleLinkReturnTo(): string {
  if (typeof window === "undefined") return SETTINGS_PROFILE_ROUTE;
  // Capture the full in-app location (pathname + hash route) so the landing
  // returns the user exactly where they started the link.
  const here = window.location.pathname + window.location.hash;
  return isSafeInAppPath(here) ? here : SETTINGS_PROFILE_ROUTE;
}

/** True only for a same-origin, non-protocol-relative in-app path. */
export function isSafeInAppPath(value: string | null): value is string {
  return (
    value !== null &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("://")
  );
}

/** Parse the landing query string the facade appended to the redirect. */
export function parseGoogleLinkOutcome(search: string): GoogleLinkOutcome {
  const params = new URLSearchParams(search);
  const raw = params.get("link_status");
  const status: GoogleLinkStatus =
    raw === "linked" ||
    raw === "already_linked" ||
    raw === "merge_required" ||
    raw === "error"
      ? raw
      : "error";
  const returnToRaw = params.get("return_to");
  return {
    status,
    provider: params.get("provider"),
    emailUpgraded: params.get("email_upgraded") === "true",
    returnTo: isSafeInAppPath(returnToRaw) ? returnToRaw : null,
    message: params.get("message"),
  };
}
