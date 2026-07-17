/**
 * Typed client for the public ``/v1/auth/siwe/*`` surface (wallet
 * sign-in, EIP-4361). Both endpoints are pre-session — no bearer is
 * attached-or-required; the transport adds one only when a session
 * already exists, and the server ignores it here.
 */

import type {
  SiweNonceRequest,
  SiweNonceResponse,
  SiweSessionResponse,
  SiweVerifyRequest,
} from "@enterprise-search/api-types";

import { httpJson } from "./http";

export async function requestSiweNonce(
  payload: SiweNonceRequest,
): Promise<SiweNonceResponse> {
  return httpJson<SiweNonceResponse>("POST", "/v1/auth/siwe/nonce", payload);
}

export async function verifySiwe(
  payload: SiweVerifyRequest,
): Promise<SiweSessionResponse> {
  return httpJson<SiweSessionResponse>("POST", "/v1/auth/siwe/verify", payload);
}
