// Audit log API surface (PR 7.1).
//
// One read endpoint behind ``/v1/audit`` on the facade, which proxies to
// the backend's unified ``/internal/v1/audit/list`` (cross-stream) route.
// The frontend never speaks to the backend directly — everything goes via
// the facade so identity is enforced once at the perimeter.

import type {
  ListAuditEventsRequest,
  ListAuditEventsResponse,
} from "@enterprise-search/api-types";
import type { RequestIdentity } from "./config";
import { httpGet } from "./http";

export function listAuditEvents(
  identity: RequestIdentity,
  params: ListAuditEventsRequest = {},
): Promise<ListAuditEventsResponse> {
  // ``httpGet`` already appends ``org_id`` + ``user_id`` from identity
  // and accepts an ``extra`` map. We allowlist + stringify here so a
  // future field addition on ``ListAuditEventsRequest`` is a typed
  // change, not a silently-dropped field.
  const extra: Record<string, string | undefined> = {};
  if (params.action !== undefined) extra.action = params.action;
  if (params.actor_user_id !== undefined)
    extra.actor_user_id = params.actor_user_id;
  if (params.resource_type !== undefined)
    extra.resource_type = params.resource_type;
  if (params.since !== undefined) extra.since = params.since;
  if (params.until !== undefined) extra.until = params.until;
  if (params.cursor !== undefined) extra.cursor = params.cursor;
  if (params.limit !== undefined) extra.limit = String(params.limit);
  return httpGet<ListAuditEventsResponse>("/v1/audit", identity, extra);
}

export type { ListAuditEventsRequest, ListAuditEventsResponse };
