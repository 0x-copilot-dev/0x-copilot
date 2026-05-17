// Frontend API client for the tier-2 adapter review pipeline (Phase 7C).
//
// All calls land on the facade, never the backend directly. The facade
// proxies onto 7A's ``/internal/v1/adapter_registry/*`` routes and stamps
// the verified identity from the bearer — see services/backend-facade/
// src/backend_facade/adapter_review_routes.py.

import type { RequestIdentity } from "../../api/config";
import { httpGet, httpJson } from "../../api/http";

import type {
  AdapterReviewCandidateDetail,
  AdapterReviewCandidatesResponse,
  AdapterReviewDecisionRequest,
  AdapterReviewDecisionResponse,
  AdapterReviewListFilters,
} from "./types";

const BASE_PATH = "/v1/admin/adapter_registry/candidates";

export function listAdapterReviewCandidates(
  identity: RequestIdentity,
  filters: AdapterReviewListFilters = {},
): Promise<AdapterReviewCandidatesResponse> {
  const extra: Record<string, string | undefined> = {};
  if (filters.status !== undefined) extra.status = filters.status;
  if (filters.layout !== undefined) extra.layout = filters.layout;
  if (filters.scheme !== undefined) extra.scheme = filters.scheme;
  if (filters.cursor !== undefined) extra.cursor = filters.cursor;
  if (filters.limit !== undefined) extra.limit = String(filters.limit);
  return httpGet<AdapterReviewCandidatesResponse>(BASE_PATH, identity, extra);
}

export function getAdapterReviewCandidate(
  candidateId: string,
): Promise<AdapterReviewCandidateDetail> {
  // ``httpJson`` rides the bearer alone; identity is stamped on the
  // facade from the verified token. We deliberately avoid threading
  // ``identity`` through the URL — the facade overrides any query-
  // string identity with the bearer's identity anyway.
  return httpJson<AdapterReviewCandidateDetail>(
    "GET",
    `${BASE_PATH}/${encodeURIComponent(candidateId)}`,
  );
}

export function decideAdapterReviewCandidate(
  candidateId: string,
  request: AdapterReviewDecisionRequest,
): Promise<AdapterReviewDecisionResponse> {
  return httpJson<AdapterReviewDecisionResponse>(
    "POST",
    `${BASE_PATH}/${encodeURIComponent(candidateId)}/decisions`,
    request,
  );
}
