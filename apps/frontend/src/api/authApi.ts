/**
 * Typed client for the public ``/v1/auth/*`` surface (A2 + A3 + A4 + A6 + A8).
 */

import type {
  AccountSession,
  AccountSessionListResponse,
  AuthDiscoverRequest,
  AuthDiscoverResponse,
  AuthProviderSummary,
  AuthProvidersResponse,
  LoginAttempt,
  LoginAttemptListResponse,
  LoginRequest,
  LoginResponse,
  MagicLinkCallbackResponse,
  MagicLinkStartRequest,
  MagicLinkStartResponse,
  MfaChallengeRequest,
  MfaChallengeResponse,
  MfaVerifyRequest,
  MfaVerifyResponse,
  SessionSelectRequest,
  SessionSelectResponse,
} from "@enterprise-search/api-types";

import { httpJson } from "./http";

// Bearer attachment lives in `./http` (configureAuthBearerProvider) so
// every API helper picks up the active session, not just authApi. The
// re-export keeps AuthProvider's existing import surface stable.
export { configureAuthBearerProvider } from "./http";

// Thin per-method wrappers that route through the shared transport
// singleton (see PRD 05). Kept as local names so the rest of this
// module's call sites stay legible.
function get<T>(path: string): Promise<T> {
  return httpJson<T>("GET", path);
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return httpJson<T>("POST", path, body);
}

async function del(path: string): Promise<void> {
  await httpJson<void>("DELETE", path);
}

// ---------------------------------------------------------------------------
// Identity bootstrap
// ---------------------------------------------------------------------------

export interface SessionIdentity {
  org_id: string;
  user_id: string;
  roles: string[];
  permission_scopes: string[];
  /**
   * Optional human-readable name for the signed-in user. Populated by the
   * backend session response when the auth contract carries it; absent
   * today (the HMAC bearer + `_identity_envelope` in
   * `services/backend-facade/auth_routes.py` only ship org/user/roles/scopes),
   * so consumers must treat this as nullable. Kept here so a future
   * auth-contract PR widens the wire and existing FE consumers
   * (`ThreadWelcome` greeting, sidebar `UserCard`) pick it up without code
   * changes.
   */
  display_name?: string | null;
}

export interface SessionEnvelope {
  identity: SessionIdentity;
}

export async function fetchCurrentSession(): Promise<SessionEnvelope> {
  return get<SessionEnvelope>("/v1/auth/session");
}

// ---------------------------------------------------------------------------
// Provider listing + login
// ---------------------------------------------------------------------------

export async function listAuthProviders(
  orgId: string,
): Promise<AuthProviderSummary[]> {
  const params = new URLSearchParams({ org_id: orgId });
  const data = await get<AuthProvidersResponse>(`/v1/auth/providers?${params}`);
  return data.providers;
}

export async function loginWithPassword(
  payload: LoginRequest,
): Promise<LoginResponse> {
  return post<LoginResponse>("/v1/auth/login", payload);
}

export async function logout(): Promise<void> {
  await post<void>("/v1/auth/logout");
}

// ---------------------------------------------------------------------------
// Sessions panel
// ---------------------------------------------------------------------------

export async function listAccountSessions(): Promise<AccountSession[]> {
  const data = await get<AccountSessionListResponse>("/v1/auth/sessions");
  return data.sessions;
}

export async function revokeAccountSession(sessionId: string): Promise<void> {
  await del(`/v1/auth/sessions/${encodeURIComponent(sessionId)}`);
}

// ---------------------------------------------------------------------------
// MFA — login-time challenge/verify only
// ---------------------------------------------------------------------------
//
// Caller-scoped factor management (list / enroll / confirm / disable) is in
// `src/api/mfaApi.ts` and hits `/v1/me/mfa/*`. The two surfaces are
// intentionally distinct: `/v1/auth/mfa/*` is the pre-session login dance
// (challenge + verify + recovery), `/v1/me/mfa/*` is the post-session
// enrollment UI in Settings. Don't add factor-CRUD shims here.

export async function issueMfaChallenge(
  payload: MfaChallengeRequest,
): Promise<MfaChallengeResponse> {
  return post<MfaChallengeResponse>("/v1/auth/mfa/challenge", payload);
}

export async function verifyMfaChallenge(
  payload: MfaVerifyRequest,
): Promise<MfaVerifyResponse> {
  return post<MfaVerifyResponse>("/v1/auth/mfa/verify", payload);
}

export async function consumeRecoveryCode(
  code: string,
): Promise<{ code_id: string; consumed_at: string }> {
  return post<{ code_id: string; consumed_at: string }>(
    "/v1/auth/mfa/recovery/consume",
    { code },
  );
}

// ---------------------------------------------------------------------------
// Login attempts (caller's own history)
// ---------------------------------------------------------------------------

export async function listMyLoginAttempts(limit = 20): Promise<LoginAttempt[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  const data = await get<LoginAttemptListResponse>(
    `/v1/auth/me/login-attempts?${params}`,
  );
  return data.attempts;
}

// ---------------------------------------------------------------------------
// Login email-first / magic-link / workspace picker (PR 5.1)
//
// Discovery is the only call that fires from the typing user — debounced 450ms
// in <EmailStep>. Magic-link start is fire-and-forget (always 202 from the
// server; we treat any other 2xx response identically). Magic-link callback
// runs on the dedicated callback page after the user clicks the email URL.
// ---------------------------------------------------------------------------

export async function discoverAuth(
  payload: AuthDiscoverRequest,
): Promise<AuthDiscoverResponse> {
  return post<AuthDiscoverResponse>("/v1/auth/discover", payload);
}

export async function startMagicLink(
  payload: MagicLinkStartRequest,
): Promise<MagicLinkStartResponse> {
  return post<MagicLinkStartResponse>("/v1/auth/magic-link/start", payload);
}

export async function consumeMagicLink(
  token: string,
): Promise<MagicLinkCallbackResponse> {
  return post<MagicLinkCallbackResponse>("/v1/auth/magic-link/callback", {
    token,
  });
}

export async function selectWorkspace(
  payload: SessionSelectRequest,
): Promise<SessionSelectResponse> {
  return post<SessionSelectResponse>("/v1/auth/sessions/select", payload);
}
