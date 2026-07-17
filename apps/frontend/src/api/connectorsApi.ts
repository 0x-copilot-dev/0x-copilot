// Typed wrappers for the Phase 11 Connectors destination.
//
// Surfaces (sub-PRD §4):
//   1. `fetchConnectors(identity, opts)`            — GET /v1/connectors.
//   2. `fetchConnector(identity, id)`               — GET /v1/connectors/{id}.
//   3. `startConnectorOAuth(identity, slug)`        — POST /v1/connectors/{slug}/start-oauth.
//   4. `completeConnectorOAuth(identity, body)`     — POST /v1/connectors/oauth-callback.
//   5. `refreshConnector(identity, id)`             — POST /v1/connectors/{id}/refresh.
//   6. `disconnectConnector(identity, id)`          — POST /v1/connectors/{id}/disconnect.
//   7. `patchConnectorScopes(identity, id, body)`   — PATCH /v1/connectors/{id}/scopes.
//   8. `fetchConnectorAudit(identity, id, opts)`    — GET /v1/connectors/{id}/audit (admin).
//   9. `streamConnectorEvents({...})`               — GET /v1/connectors/stream (SSE).
//
// Network rule (apps/frontend/CLAUDE.md): apps call the **facade** only
// (`/v1/*`). The transport singleton enforces this via the same-origin
// Vite proxy → facade. 401s are intercepted globally by AuthContext via
// the transport's `onUnauthorized` hook.
//
// Wire types come from `@0x-copilot/api-types` (canonical declaration
// site at `packages/api-types/src/connectors.ts`).

import type {
  Connector,
  ConnectorAuditResponse,
  ConnectorDetailResponse,
  ConnectorListResponse,
  ConnectorOAuthCallbackRequest,
  ConnectorStatus,
  ConnectorStreamEnvelope,
  ConnectorStreamEventType,
  ConnectorScopeEntry,
  DisconnectConnectorResponse,
  PatchConnectorScopesRequest,
  PatchConnectorScopesResponse,
  RefreshConnectorResponse,
  StartConnectorOAuthResponse,
} from "@0x-copilot/api-types";
import type { ConnectorId, ConnectorSlug } from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpGet, httpJson, httpPatchQuery, httpPostQuery } from "./http";
import { getAppTransport } from "./transport";

const SSE_EVENT_NAME = "connector_event";

// ===========================================================================
// LIST — `GET /v1/connectors`
// ===========================================================================

export interface ListConnectorsFilters {
  /** Filter to `connected`, `disconnected`, `error`, or `expired`. */
  readonly status?: ConnectorStatus;
  /** Filter to a single slug — used by Home's "expired connectors" tile. */
  readonly slug?: ConnectorSlug;
  /** When true, returns only the rows the caller has installed (drops the
   *  `available` catalog half). */
  readonly installed?: boolean;
}

export interface FetchConnectorsOptions {
  readonly filters?: ListConnectorsFilters;
  readonly q?: string;
  /** Opaque cursor from a previous response's `next_cursor`. */
  readonly after?: string;
  /** 1..200; server default 50. */
  readonly limit?: number;
}

/**
 * GET /v1/connectors with allowlisted filters + cursor pagination
 * (connectors-prd §4.1, §3.1 list response). Filter encoding mirrors the
 * agents / routines / projects / inbox APIs — `filter[<axis>]=<value>`
 * keys, single value per axis.
 */
export function fetchConnectors(
  identity: RequestIdentity,
  options: FetchConnectorsOptions = {},
): Promise<ConnectorListResponse> {
  return httpGet<ConnectorListResponse>(
    "/v1/connectors",
    identity,
    encodeListParams(options),
  );
}

// ===========================================================================
// DETAIL — `GET /v1/connectors/{id}`
// ===========================================================================

/**
 * GET /v1/connectors/{id} — returns connector + "Used by" consumer
 * projection (connectors-prd §4.2). 404 for not-found / not-visible
 * (cross-audit §1.3 — not-found and not-visible collapse to one status to
 * avoid existence leaks).
 */
export function fetchConnector(
  identity: RequestIdentity,
  id: ConnectorId,
): Promise<ConnectorDetailResponse> {
  return httpGet<ConnectorDetailResponse>(
    `/v1/connectors/${encodeURIComponent(id)}`,
    identity,
  );
}

// ===========================================================================
// OAUTH — start + callback (alias of the existing MCP path)
// ===========================================================================

/**
 * POST /v1/connectors/{slug}/start-oauth — kicks off the OAuth round-trip.
 * Returns the `authorization_url` to redirect the user to; the matching
 * `state` is owned server-side and replayed on the callback (connectors-prd
 * §4.3 alias).
 */
export function startConnectorOAuth(
  identity: RequestIdentity,
  slug: ConnectorSlug,
): Promise<StartConnectorOAuthResponse> {
  return httpPostQuery<StartConnectorOAuthResponse>(
    `/v1/connectors/${encodeURIComponent(slug)}/start-oauth`,
    {},
    identity,
  );
}

/**
 * POST /v1/connectors/oauth-callback — completes the OAuth handshake and
 * inserts the connector row (connectors-prd §4.4 alias). Returns the
 * fresh `Connector` so the destination can drop the OAuth-in-flight
 * placeholder without a re-list.
 */
export function completeConnectorOAuth(
  identity: RequestIdentity,
  body: ConnectorOAuthCallbackRequest,
): Promise<Connector> {
  return httpPostQuery<Connector>(
    "/v1/connectors/oauth-callback",
    body,
    identity,
  );
}

// ===========================================================================
// MUTATIONS — refresh / disconnect / scopes
// ===========================================================================

/**
 * POST /v1/connectors/{id}/refresh — forces a token-refresh against the
 * provider. Returns the freshly-refreshed connector row (`connected` on
 * success; `error` if the provider 4xx'd).
 */
export function refreshConnector(
  identity: RequestIdentity,
  id: ConnectorId,
): Promise<RefreshConnectorResponse> {
  return httpPostQuery<RefreshConnectorResponse>(
    `/v1/connectors/${encodeURIComponent(id)}/refresh`,
    {},
    identity,
  );
}

/**
 * POST /v1/connectors/{id}/disconnect — wipes the token through the
 * existing `TokenVault` path; consumers (agents / tools / projects) are
 * preserved with a needs-reconnect hint surfaced on the FE.
 */
export function disconnectConnector(
  identity: RequestIdentity,
  id: ConnectorId,
): Promise<DisconnectConnectorResponse> {
  return httpPostQuery<DisconnectConnectorResponse>(
    `/v1/connectors/${encodeURIComponent(id)}/disconnect`,
    {},
    identity,
  );
}

/**
 * PATCH /v1/connectors/{id}/scopes — request a scope change. Atlas does
 * not unilaterally shrink scopes without provider confirmation, so the
 * server returns 202 with a `reauth_url` to drive the user through a
 * re-OAuth round-trip.
 */
export function patchConnectorScopes(
  identity: RequestIdentity,
  id: ConnectorId,
  body: PatchConnectorScopesRequest,
): Promise<PatchConnectorScopesResponse> {
  return httpPatchQuery<PatchConnectorScopesResponse>(
    `/v1/connectors/${encodeURIComponent(id)}/scopes`,
    body,
    identity,
  );
}

// ===========================================================================
// AUDIT — `GET /v1/connectors/{id}/audit` (admin)
// ===========================================================================

export interface FetchConnectorAuditOptions {
  readonly after?: string;
  readonly limit?: number;
  /** ISO8601 lower bound on `ts`. */
  readonly since?: string;
}

/**
 * GET /v1/connectors/{id}/audit — paginated read-audit log
 * (connectors-prd §4.8). Admin-only on the server; the FE renders the
 * "Audit" tab inside the connector detail only when the caller is admin.
 */
export function fetchConnectorAudit(
  identity: RequestIdentity,
  id: ConnectorId,
  options: FetchConnectorAuditOptions = {},
): Promise<ConnectorAuditResponse> {
  const params: Record<string, string | undefined> = {};
  if (options.after !== undefined) {
    params.after = options.after;
  }
  if (options.limit !== undefined) {
    params.limit = String(options.limit);
  }
  if (options.since !== undefined) {
    params.since = options.since;
  }
  return httpGet<ConnectorAuditResponse>(
    `/v1/connectors/${encodeURIComponent(id)}/audit`,
    identity,
    params,
  );
}

// ===========================================================================
// SCOPE FETCH — `GET /v1/connectors/{id}/scopes`
// ===========================================================================

/** Reuses the connector's `scopes` array shape — single source of truth. */
export interface ConnectorScopesResponse {
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
}

/**
 * GET /v1/connectors/{id}/scopes — fetch the full scope set for the scope
 * review tab (connectors-prd §4.6). Convenience over `fetchConnector` for
 * scope-only fetches; the server may include catalog-supplied scopes the
 * caller has not yet granted.
 */
export function fetchConnectorScopes(
  identity: RequestIdentity,
  id: ConnectorId,
): Promise<ConnectorScopesResponse> {
  return httpGet<ConnectorScopesResponse>(
    `/v1/connectors/${encodeURIComponent(id)}/scopes`,
    identity,
  );
}

// ===========================================================================
// SSE — `GET /v1/connectors/stream` (durable channel — connectors-prd §4.9)
// ===========================================================================

/** Closeable handle for a running connectors-events SSE subscription. */
export interface ConnectorEventsStream {
  close(): void;
}

/**
 * Open the durable connectors-events SSE stream. Each frame carries one
 * `ConnectorStreamEnvelope`; the client tracks the highest `sequence_no`
 * and reconnects with `?after_sequence=N` to resume without dropping
 * events (cross-audit §5.2).
 *
 * Reconnect policy is owned caller-side (mirrors `streamAgentEvents` /
 * `streamRoutineEvents`) — the wrapper exposes one connection attempt
 * plus a stable error hook so tests can drive the timing
 * deterministically.
 */
export function streamConnectorEvents({
  identity,
  afterSequence,
  onEvent,
  onError,
  onOpen,
}: {
  readonly identity: RequestIdentity;
  /** Highest `sequence_no` already applied; backend replays everything strictly greater. */
  readonly afterSequence?: number;
  readonly onEvent: (envelope: ConnectorStreamEnvelope) => void;
  readonly onError: (err: Event) => void;
  readonly onOpen?: () => void;
}): ConnectorEventsStream {
  return getAppTransport().subscribeServerSentEvents({
    path: "/v1/connectors/stream",
    query: connectorSseQueryFor(identity, afterSequence),
    eventName: SSE_EVENT_NAME,
    onOpen,
    onError: (_err) => onError(streamErrorEvent()),
    onMessage: (data) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(data) as unknown;
      } catch {
        // Malformed JSON — drop the frame. Mirrors agentsApi / routinesApi
        // behavior: a single bad frame must not tear down the connection;
        // the caller has `onError` for the broader "stream broken" signal.
        return;
      }
      if (isConnectorStreamEnvelope(parsed)) {
        onEvent(parsed);
      }
    },
  });
}

// ===========================================================================
// Helpers
// ===========================================================================

function encodeListParams(
  options: FetchConnectorsOptions,
): Record<string, string | undefined> {
  const params: Record<string, string | undefined> = {};
  const { filters, q, after, limit } = options;

  if (filters?.status !== undefined) {
    params["filter[status]"] = filters.status;
  }
  if (filters?.slug !== undefined) {
    params["filter[slug]"] = filters.slug;
  }
  if (filters?.installed !== undefined) {
    params.installed = filters.installed ? "true" : "false";
  }
  if (q !== undefined && q.length > 0) {
    params.q = q;
  }
  if (after !== undefined) {
    params.after = after;
  }
  if (limit !== undefined) {
    params.limit = String(limit);
  }
  return params;
}

function connectorSseQueryFor(
  identity: RequestIdentity,
  afterSequence: number | undefined,
): Record<string, string> {
  const out: Record<string, string> = {
    org_id: identity.orgId,
    user_id: identity.userId,
  };
  if (afterSequence !== undefined) {
    out.after_sequence = String(afterSequence);
  }
  return out;
}

/**
 * Loose structural check on the SSE envelope. Matches the discriminator
 * fields from connectors-prd §4.9 — `sequence_no` (number), `event_type`
 * (string), `event_id` (string), `created_at` (string). The optional
 * `connector` payload is verified by the consumer route after the type
 * narrows; mirroring `isAgentStreamEnvelope` / `isRoutineStreamEnvelope`.
 */
function isConnectorStreamEnvelope(
  value: unknown,
): value is ConnectorStreamEnvelope {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.sequence_no === "number" &&
    typeof v.event_type === "string" &&
    isKnownConnectorEvent(v.event_type) &&
    typeof v.event_id === "string" &&
    typeof v.created_at === "string"
  );
}

const KNOWN_EVENTS: ReadonlySet<ConnectorStreamEventType> =
  new Set<ConnectorStreamEventType>([
    "connector.created",
    "connector.status_changed",
    "connector.scope_changed",
    "connector.error_threshold",
    "heartbeat",
  ]);

function isKnownConnectorEvent(
  value: string,
): value is ConnectorStreamEventType {
  return KNOWN_EVENTS.has(value as ConnectorStreamEventType);
}

// The legacy onError signature was modelled after EventSource's bare
// Event — callers only react to "stream broken" and reconnect. Mirrors
// `streamAgentEvents` / `streamRoutineEvents`.
function streamErrorEvent(): Event {
  if (typeof Event === "function") {
    return new Event("error");
  }
  return { type: "error" } as unknown as Event;
}

// httpJson re-export so the route can call non-identity-gated endpoints
// when needed — keeps callers off direct fetch / transport access.
export { httpJson };
