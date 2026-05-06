import type {
  MfaFactorListResponse,
  MfaWebAuthnFinishRequestBody,
  MfaWebAuthnFinishResponse,
  MfaWebAuthnStartRequestBody,
  MfaWebAuthnStartResponse,
  TotpConfirmRequest,
  TotpEnrollRequestBody,
  TotpEnrollResponse,
} from "@enterprise-search/api-types";
import { assertOk, correlationHeaders, jsonHeaders } from "./http";

/**
 * Caller-scoped MFA enrollment for the Settings → Profile UI (PR 8.2).
 *
 * Same identity model as the rest of `/v1/me/*` — the facade verifies
 * the session, the backend derives org_id / user_id from the
 * service-token headers it forwards. No body carries identity.
 */

export async function listMyMfaFactors(): Promise<MfaFactorListResponse> {
  const response = await fetch("/v1/me/mfa/factors", {
    headers: correlationHeaders(),
  });
  await assertOk(response);
  return (await response.json()) as MfaFactorListResponse;
}

export async function enrollTotpFactor(
  body: TotpEnrollRequestBody,
): Promise<TotpEnrollResponse> {
  const response = await fetch("/v1/me/mfa/factors/totp/enroll", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  await assertOk(response);
  return (await response.json()) as TotpEnrollResponse;
}

export async function confirmTotpFactor(
  body: TotpConfirmRequest,
): Promise<{ factor_id: string; enabled: boolean }> {
  const response = await fetch("/v1/me/mfa/factors/totp/confirm", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  await assertOk(response);
  return (await response.json()) as { factor_id: string; enabled: boolean };
}

export async function disableMfaFactor(factorId: string): Promise<void> {
  const response = await fetch(
    `/v1/me/mfa/factors/${encodeURIComponent(factorId)}`,
    { method: "DELETE", headers: correlationHeaders() },
  );
  await assertOk(response);
}

export async function webauthnRegisterStart(
  body: MfaWebAuthnStartRequestBody,
): Promise<MfaWebAuthnStartResponse> {
  const response = await fetch("/v1/me/mfa/factors/webauthn/register/start", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  await assertOk(response);
  return (await response.json()) as MfaWebAuthnStartResponse;
}

export async function webauthnRegisterFinish(
  body: MfaWebAuthnFinishRequestBody,
): Promise<MfaWebAuthnFinishResponse> {
  const response = await fetch("/v1/me/mfa/factors/webauthn/register/finish", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  await assertOk(response);
  return (await response.json()) as MfaWebAuthnFinishResponse;
}
