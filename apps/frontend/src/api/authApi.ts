/**
 * Typed client for the public ``/v1/auth/*`` surface (A2 + A3 + A4 + A6 + A8).
 *
 * Distinct from ``sessionApi.ts`` (which still hits the legacy ``/v1/session``
 * for the in-flight identity bootstrap). Once the AuthContext fully replaces
 * the legacy session-load this file becomes the single seat for auth HTTP.
 */

import type {
  AccountSession,
  AccountSessionListResponse,
  AuthProviderSummary,
  AuthProvidersResponse,
  LoginAttempt,
  LoginAttemptListResponse,
  LoginRequest,
  LoginResponse,
  MfaChallengeRequest,
  MfaChallengeResponse,
  MfaFactorListResponse,
  MfaFactorSummary,
  MfaVerifyRequest,
  MfaVerifyResponse,
  TotpConfirmRequest,
  TotpEnrollResponse,
} from "@enterprise-search/api-types";

import { assertOk, correlationHeaders, jsonHeaders } from "./http";

const REQUEST_BEARER_HEADER = "authorization";

let _bearerProvider: () => string | null = () => null;

/**
 * Wire the AuthContext into the API client so every protected request
 * picks up the current bearer without prop-threading. Called once by
 * ``AuthProvider`` after it boots.
 */
export function configureAuthBearerProvider(
  provider: () => string | null,
): void {
  _bearerProvider = provider;
}

function authHeaders(): HeadersInit {
  const bearer = _bearerProvider();
  return bearer
    ? { ...correlationHeaders(), [REQUEST_BEARER_HEADER]: `Bearer ${bearer}` }
    : correlationHeaders();
}

function authJsonHeaders(): HeadersInit {
  return { "content-type": "application/json", ...authHeaders() };
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: authHeaders() });
  await assertOk(response);
  return (await response.json()) as T;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: authJsonHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  await assertOk(response);
  if (response.status === 204) {
    return undefined as T;
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

async function del(path: string): Promise<void> {
  const response = await fetch(path, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await assertOk(response);
}

// ---------------------------------------------------------------------------
// Identity bootstrap
// ---------------------------------------------------------------------------

export interface SessionIdentity {
  org_id: string;
  user_id: string;
  roles: string[];
  permission_scopes: string[];
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
// MFA
// ---------------------------------------------------------------------------

export async function listMfaFactors(): Promise<MfaFactorSummary[]> {
  const data = await get<MfaFactorListResponse>("/v1/auth/mfa/factors");
  return data.factors;
}

export async function enrollTotp(
  displayName: string,
): Promise<TotpEnrollResponse> {
  return post<TotpEnrollResponse>("/v1/auth/mfa/factors/totp/enroll", {
    display_name: displayName,
  });
}

export async function confirmTotp(
  payload: TotpConfirmRequest,
): Promise<{ factor_id: string; enabled: boolean }> {
  return post<{ factor_id: string; enabled: boolean }>(
    "/v1/auth/mfa/factors/totp/confirm",
    payload,
  );
}

export async function disableMfaFactor(factorId: string): Promise<void> {
  await del(`/v1/auth/mfa/factors/${encodeURIComponent(factorId)}`);
}

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
