import type {
  MfaFactorListResponse,
  MfaWebAuthnFinishRequestBody,
  MfaWebAuthnFinishResponse,
  MfaWebAuthnStartRequestBody,
  MfaWebAuthnStartResponse,
  TotpConfirmRequest,
  TotpEnrollRequestBody,
  TotpEnrollResponse,
} from "@0x-copilot/api-types";
import { httpJson } from "./http";

/**
 * Caller-scoped MFA enrollment for the Settings → Profile UI (PR 8.2).
 * Hits `/v1/me/mfa/*` — same identity model as the rest of `/v1/me/*`:
 * the facade verifies the session, the backend derives `org_id` /
 * `user_id` from forwarded service-token headers, no body carries
 * identity.
 *
 * For the **login-time** challenge/verify flow (pre-session), see
 * `src/api/authApi.ts` (`/v1/auth/mfa/challenge`,
 * `/v1/auth/mfa/verify`, `/v1/auth/mfa/recovery/consume`). The two
 * surfaces are deliberately distinct — see PRD 04 for the boundary
 * contract.
 */

export function listMyMfaFactors(): Promise<MfaFactorListResponse> {
  return httpJson<MfaFactorListResponse>("GET", "/v1/me/mfa/factors");
}

export function enrollTotpFactor(
  body: TotpEnrollRequestBody,
): Promise<TotpEnrollResponse> {
  return httpJson<TotpEnrollResponse>(
    "POST",
    "/v1/me/mfa/factors/totp/enroll",
    body,
  );
}

export function confirmTotpFactor(
  body: TotpConfirmRequest,
): Promise<{ factor_id: string; enabled: boolean }> {
  return httpJson<{ factor_id: string; enabled: boolean }>(
    "POST",
    "/v1/me/mfa/factors/totp/confirm",
    body,
  );
}

export async function disableMfaFactor(factorId: string): Promise<void> {
  await httpJson<void>(
    "DELETE",
    `/v1/me/mfa/factors/${encodeURIComponent(factorId)}`,
  );
}

export function webauthnRegisterStart(
  body: MfaWebAuthnStartRequestBody,
): Promise<MfaWebAuthnStartResponse> {
  return httpJson<MfaWebAuthnStartResponse>(
    "POST",
    "/v1/me/mfa/factors/webauthn/register/start",
    body,
  );
}

export function webauthnRegisterFinish(
  body: MfaWebAuthnFinishRequestBody,
): Promise<MfaWebAuthnFinishResponse> {
  return httpJson<MfaWebAuthnFinishResponse>(
    "POST",
    "/v1/me/mfa/factors/webauthn/register/finish",
    body,
  );
}
