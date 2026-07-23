// Connectors destination (Phase 11) — canonical wire contract.
//
// Source: docs/atlas-new-design/destinations/connectors-prd.md §3.1 (data
// shape) + §4 (endpoints) + §5 (storage) + §6 (ACL + audit) + §9 (HMAC).
//
// A Connector is an authenticated bridge to an external SaaS source
// (Gmail, Slack, Salesforce, etc.). The new wire shape is a denormalized
// READ MODEL over the existing MCP registration + token vault path — no
// parallel write registry. See connectors-prd §3.2.
//
// Wire-only file: no business logic, no HTTP client, no view models. The
// server is the source of truth; this package mirrors the public payloads
// exactly as the facade serves them.
//
// Canonical types reused from elsewhere (DO NOT re-declare):
// * `ConnectorSlug` — single declaration site in ./projects.ts.
// * `ConnectorId` — branded ID in ./brands.ts.
// * `TriggerId` — branded ID in ./brands.ts (reused as `Webhook.id`).
// * `ItemRef` (kind="connector"/"agent"/"tool"/"project"/"routine") — ./refs.ts.
//
// HMAC algorithm + header names + skew window live as constants in
// `services/backend/src/backend_app/webhooks/signer.py` — single source
// of truth on the server side. The frontend wizard reads them off the
// receiver-verification snippet rendered by the API; no client-side
// constant duplication.

import type { ConnectorId, TenantId, TriggerId, UserId } from "./brands";
import type { ConnectorSlug } from "./projects";
import type { ItemRef } from "./refs";

// ---------------------------------------------------------------------------
// Status taxonomy (connectors-prd §1.6)
// ---------------------------------------------------------------------------

/**
 * Connector lifecycle status.
 *
 * * `connected`    — token valid; last read within window.
 * * `disconnected` — user revoked; token wiped; row preserved.
 * * `error`        — provider returned 401/403 on last refresh; needs
 *                    reconnect (user action).
 * * `expired`      — refresh token expired; needs full re-auth.
 *
 * State transitions are server-driven from the token-refresh worker; the
 * UI displays. Mutating writes (disconnect, scope patch) drive the
 * remaining transitions through the routes in §4.
 */
export type ConnectorStatus =
  | "connected"
  | "disconnected"
  | "error"
  | "expired";

// ---------------------------------------------------------------------------
// Access mode (desktop redesign, Phase 4 — Tools destination)
// ---------------------------------------------------------------------------
//
// Source: docs/plan/desktop-redesign/design-reference/DESIGN-SPEC.md §3
// (Tools = connectors: per-tool segmented `Read / Read & act / Off`) +
// phase-4/PRD.md FR-4.21/4.22 + §11 (access-mode is a NEW per-connector
// concept, distinct from OAuth scopes and from the global tool-use
// approval policy — the recommended shape is this new persisted field).

/**
 * Canonical per-connector access modes, as the runtime SSOT (value tuple)
 * the union derives from. Kept as an `as const` tuple so the union is also
 * runtime-enumerable (the 3-way segmented control, tests) with a single
 * declaration site — no value/type drift.
 *
 * * `read`     — the agent may READ from the connector (least privilege
 *                that still lets it see data).
 * * `read_act` — the agent may read AND ACT through the connector
 *                (write/side-effecting calls, still subject to the global
 *                approval policy in Settings → Model & behavior).
 * * `off`      — the connector is disabled for the agent; no reads, no acts.
 */
export const CONNECTOR_ACCESS_MODES = ["read", "read_act", "off"] as const;

/**
 * Per-connector access mode driving the Tools destination's 3-way
 * segmented control (Read / Read & act / Off). The global approval
 * *policy* lives separately (Settings → Model & behavior); this field is
 * the per-connector *which app may do what* switch.
 */
export type ConnectorAccessMode = (typeof CONNECTOR_ACCESS_MODES)[number];

// ---------------------------------------------------------------------------
// Scope — the OAuth scopes a user granted, per slug, per connection.
// ---------------------------------------------------------------------------

/**
 * One OAuth scope on a connector. Provider-specific scope strings (e.g.
 * `gmail.readonly`) carry along a human-readable description sourced from
 * the catalog file at backend bootstrap — the frontend does not interpret
 * scope strings, only renders them.
 */
export interface ConnectorScopeEntry {
  readonly scope: string;
  readonly granted: boolean;
  readonly description: string;
}

// ---------------------------------------------------------------------------
// Connector — the destination's primary row (denormalized read model)
// ---------------------------------------------------------------------------

/**
 * One connected (or once-connected) SaaS source.
 *
 * Storage is a denormalized view over `mcp_servers` + `token_vault`
 * metadata (connectors-prd §3.2 / §5.1). The wire field set is stable
 * across the in-memory + Postgres backends. `tenant_id` rides every row
 * so the resolver can call `is_member(tenant, project, user)` without
 * caller-supplied trust.
 *
 * `last_sync_at` is updated by the existing tool-invocation pipeline
 * (Phase 10 wires `runtime_tool_invocations` → `last_sync_at` on touch)
 * and is `null` for newly-installed connectors that have not yet seen a
 * tool call.
 */
export interface Connector {
  readonly id: ConnectorId;
  readonly tenant_id: TenantId;
  readonly slug: ConnectorSlug;
  readonly display_name: string;
  readonly description: string;
  readonly status: ConnectorStatus;
  readonly status_reason?: string;
  /**
   * Per-connector access mode (Tools destination 3-way segment). REQUIRED —
   * the backend always emits it now that `PATCH /v1/connectors/{id}/access-mode`
   * and the durable `connectors.access_mode` column exist (PRD-06). Consumers
   * read it directly; there is no `?? "off"` fallback (that fallback existed
   * only to paper over a field the server never sent, and was the direct cause
   * of the "Off everywhere" symptom).
   */
  readonly access_mode: ConnectorAccessMode;
  readonly owner_user_id: UserId;
  /**
   * Scopes granted by the user, per the most recent OAuth round-trip.
   * Provider-dependent string values; the catalog file (loaded at
   * backend bootstrap, see `services/backend/src/backend_app/connectors/
   * catalog.yaml`) supplies the description.
   */
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
  readonly last_sync_at: string | null;
  readonly last_error_at?: string;
  readonly created_at: string;
  readonly updated_at: string;
}

// ---------------------------------------------------------------------------
// Availability (AC9 — desktop connector reconciliation overlay)
// ---------------------------------------------------------------------------
//
// The honest, stable availability state a reconciled connector reports BEFORE
// any live provider probe. Preview connectors read `preview` until the
// deployment enables them; tenant-template / admin-gated connectors read
// `admin_setup_required`. Mirrors `ConnectorAvailability` in
// `services/backend/src/backend_app/connectors/profile_catalog.py`.
//
// ADDITIVE + optional-only: this enum is consumed only by the new (optional)
// catalog fields below and by the desktop-only `connectors-desktop.ts`
// transport. No existing web payload is changed.

export type ConnectorAvailability =
  | "available"
  | "preview"
  | "admin_setup_required"
  | "tenant_disabled"
  | "unsupported_by_policy"
  | "tool_contract_mismatch"
  | "temporarily_unavailable";

/**
 * One user-facing capability line on a reconciled connector (e.g. "Search Jira
 * issues"). `status` distinguishes a supported read tool from one that needs a
 * broader scope, or an operation the profile explicitly does not support.
 * ADDITIVE + optional-only.
 */
export interface ConnectorCapabilitySummary {
  readonly id: string;
  readonly label: string;
  readonly status: "supported" | "scope_required" | "unsupported";
  readonly read_only: boolean;
}

// ---------------------------------------------------------------------------
// Catalog entry — slugs Atlas knows about but the caller has not installed
// ---------------------------------------------------------------------------

/**
 * One available-to-install slug. Wire shape is intentionally minimal: the
 * detail view fetches the rest after the user picks a card to install.
 *
 * `icon_hint` is a hint string the FE may map to a built-in icon registry
 * (e.g. `"gmail"`, `"slack"`); when absent the FE renders a letter
 * glyph.
 *
 * The `display_group` / `release_stage` / `availability` / `capabilities`
 * fields are AC9 ADDITIONS: every one is OPTIONAL, so existing web call sites
 * and snapshots keep compiling unchanged and older payloads (which omit them)
 * stay valid. Only the desktop reconciled catalog populates them today.
 */
export interface ConnectorCatalogEntry {
  readonly slug: ConnectorSlug;
  readonly display_name: string;
  readonly description: string;
  readonly icon_hint?: string;
  readonly display_group?: string;
  readonly release_stage?: "stable" | "preview";
  readonly availability?: ConnectorAvailability;
  readonly availability_reason?: string;
  readonly capabilities?: ReadonlyArray<ConnectorCapabilitySummary>;
}

// ---------------------------------------------------------------------------
// List response
// ---------------------------------------------------------------------------

/**
 * `GET /v1/connectors` response. `connectors` is the installed set the
 * caller can read; `available` is the not-yet-installed catalog.
 *
 * Cursor pagination follows the home/inbox/routines convention: an opaque
 * `next_cursor` string. `null` when the page is the last.
 */
export interface ConnectorListResponse {
  readonly connectors: ReadonlyArray<Connector>;
  readonly available: ReadonlyArray<ConnectorCatalogEntry>;
  readonly next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Detail response — the connector + consumer projection
// ---------------------------------------------------------------------------

/**
 * The "Used by" projection rendered on the connector detail page. Each
 * entry is an `ItemRef` so the FE's `<ItemLink>` registry can resolve
 * label/icon hints. `chats_with_grant` is a count only (privacy: per-chat
 * fan-out lives behind admin-only audit), not a list.
 */
export interface ConnectorConsumers {
  readonly agents: ReadonlyArray<ItemRef>;
  readonly tools: ReadonlyArray<ItemRef>;
  readonly projects: ReadonlyArray<ItemRef>;
  readonly chats_with_grant: number;
}

/**
 * `GET /v1/connectors/{id}` response.
 */
export interface ConnectorDetailResponse {
  readonly connector: Connector;
  readonly consumers: ConnectorConsumers;
}

// ---------------------------------------------------------------------------
// Mutating-route request / response shapes
// ---------------------------------------------------------------------------

/**
 * `POST /v1/connectors/{slug}/start-oauth` response.
 *
 * Sends the user to `authorization_url`; the server records the matching
 * `state` against the started session. Reuses the existing MCP OAuth
 * round-trip (connectors-prd §4.3 alias).
 */
export interface StartConnectorOAuthResponse {
  readonly authorization_url: string;
  readonly state: string;
}

/**
 * `POST /v1/connectors/oauth-callback` request body. Aliases the existing
 * MCP OAuth callback path. Returns the newly-created `Connector`.
 */
export interface ConnectorOAuthCallbackRequest {
  readonly code: string;
  readonly state: string;
}

/**
 * `PATCH /v1/connectors/{id}/scopes` request body. The server compares
 * `scopes` against the currently-granted set and triggers a re-OAuth flow
 * (response is `202 { reauth_url }`).
 */
export interface PatchConnectorScopesRequest {
  readonly scopes: ReadonlyArray<ConnectorScopeEntry>;
}

/**
 * `PATCH /v1/connectors/{id}/scopes` response. `202 Accepted` — the
 * server is requesting a re-OAuth round-trip to confirm the new scope
 * set. Atlas does not unilaterally shrink scopes without provider
 * confirmation.
 */
export interface PatchConnectorScopesResponse {
  readonly reauth_url: string;
  readonly state: string;
}

/**
 * `PATCH /v1/connectors/{id}/access-mode` request body (desktop redesign,
 * Phase 4). Sets the per-connector Read / Read & act / Off mode driving
 * the Tools destination segment. Unlike the scopes PATCH this does NOT
 * trigger a re-OAuth round-trip — the mode is a local gate, not an OAuth
 * grant change. The host applies it optimistically and reverts on failure
 * (PRD FR-4.22).
 */
export interface SetConnectorAccessModeRequest {
  readonly access_mode: ConnectorAccessMode;
}

/**
 * `PATCH /v1/connectors/{id}/access-mode` response — the connector row
 * with its updated `access_mode`.
 */
export interface SetConnectorAccessModeResponse {
  readonly connector: Connector;
}

/**
 * `POST /v1/connectors/{id}/refresh` response — the freshly-refreshed
 * connector row (status flips to `connected` on success; `error` on
 * provider 4xx).
 */
export interface RefreshConnectorResponse {
  readonly connector: Connector;
}

/**
 * `POST /v1/connectors/{id}/disconnect` response — the disconnected row
 * (status flips to `disconnected`; token wiped through the existing
 * `TokenVault` path; consumers preserved with a needs-reconnect hint
 * rendered on the FE).
 */
export interface DisconnectConnectorResponse {
  readonly connector: Connector;
}

// ---------------------------------------------------------------------------
// Audit-log read projection (connectors-prd §4.8)
// ---------------------------------------------------------------------------

/**
 * One row in the read-audit log. Wire shape is a flat projection over
 * `runtime_tool_invocations` (joined to tools whose `transport.connector_ref`
 * points at this connector) and `connector_read_events`.
 *
 * `caller` is an `ItemRef` so the FE renders deep links into the original
 * run / agent / routine.
 */
export interface ConnectorAuditEntry {
  readonly id: string;
  readonly connector_id: ConnectorId;
  readonly tenant_id: TenantId;
  readonly ts: string;
  readonly caller: ItemRef;
  readonly endpoint: string;
  readonly bytes_read: number | null;
  readonly status: "ok" | "error" | "auth_required";
  readonly status_detail?: string;
}

/**
 * `GET /v1/connectors/{id}/audit` response. Cursor pagination is opaque;
 * the FE renders `<ActivityList>` over the rows.
 */
export interface ConnectorAuditResponse {
  readonly entries: ReadonlyArray<ConnectorAuditEntry>;
  readonly next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Webhook — connectors-prd §3.1 / §4.10 / §9 (HMAC management UX)
// ---------------------------------------------------------------------------
//
// Routines §9.7 Q6 HMAC-of-payload signature lands here. The wire shape
// is locked at Phase 11; the management UI (Phase 11 sub-PRD §7) is the
// destination home for webhook lifecycle. Routine `triggers[].webhook`
// references this row by `id`.
//
// The secret is NEVER on the wire. The wizard's copy-once reveal returns
// the secret in the create / rotate responses only; subsequent reads
// never carry it.

export type WebhookSecretStrategy = "rotating" | "static";

/** Only `hmac-sha256` is supported in v1 (connectors-prd §3.1). */
export type WebhookHmacAlgo = "hmac-sha256";

export type WebhookStatus = "active" | "paused";

export interface Webhook {
  readonly id: TriggerId;
  readonly tenant_id: TenantId;
  readonly url: string;
  readonly secret_strategy: WebhookSecretStrategy;
  readonly hmac_algo: WebhookHmacAlgo;
  /** CIDR strings; empty array means "no restriction". */
  readonly ip_allowlist: ReadonlyArray<string>;
  readonly status: WebhookStatus;
  readonly last_fire_at: string | null;
  readonly last_status_code?: number;
  /** Linkback to the routine that registered the webhook. */
  readonly routine_ref?: ItemRef;
  readonly created_at: string;
  readonly rotates_at: string | null;
}

/** Create / rotate response envelopes — the plaintext secret is
 *  surfaced EXACTLY ONCE via these shapes (copy-once reveal).
 *  Subsequent GETs return the redacted `Webhook` without a plaintext
 *  channel. */
export interface WebhookCreateResponse {
  readonly webhook: Webhook;
  readonly secret_plaintext: string;
}

export interface WebhookRotateResponse {
  readonly webhook: Webhook;
  readonly secret_plaintext: string;
  /** Previous secret remains valid for the 14-day grace window
   *  (connectors-prd §9.2) so receivers can roll without a hard
   *  cutover. Null when there's no grace (first rotation or after the
   *  previous expiry has already elapsed). */
  readonly grace_secret_plaintext: string | null;
}

export interface WebhookListResponse {
  readonly items: ReadonlyArray<Webhook>;
  readonly next_cursor: string | null;
}

export interface WebhookTestFireResponse {
  /** Upstream HTTP status; null when the request never completed
   *  (timeout / DNS / connection refused). */
  readonly response_status: number | null;
  readonly response_ok: boolean;
  /** Error class name on transport failure (httpx-side), otherwise
   *  absent. Stable enough for the wizard to switch UI states; not
   *  guaranteed to be a stable contract for programmatic consumers. */
  readonly error?: string;
}

/**
 * Body of `POST /v1/connectors/webhooks`. Server fills defaults for
 * everything the caller omits — strategy + algo + IP allowlist + status
 * come from tenant-policy defaults when the wizard does not override.
 */
export interface CreateWebhookRequest {
  readonly url: string;
  readonly secret_strategy?: WebhookSecretStrategy;
  readonly hmac_algo?: WebhookHmacAlgo;
  readonly ip_allowlist?: ReadonlyArray<string>;
  /** Caller-supplied secret. Required when `secret_strategy === "static"`
   *  (the server generates one for `"rotating"`). */
  readonly secret_plaintext?: string;
}

/**
 * Body of `PATCH /v1/connectors/webhooks/{id}`. Every field optional;
 * unset fields are left unchanged server-side.
 */
export interface PatchWebhookRequest {
  readonly url?: string;
  readonly ip_allowlist?: ReadonlyArray<string>;
  readonly status?: WebhookStatus;
}

/**
 * Body of `POST /v1/connectors/webhooks/{id}/test-fire`. Empty in v1 —
 * the server constructs the payload deterministically from the webhook's
 * registration so receivers can hardcode-match.
 */
export interface TestFireWebhookRequest {
  /** Reserved for forward-compat; ignored server-side in v1. */
  readonly note?: string;
}

// ---------------------------------------------------------------------------
// SSE — `GET /v1/connectors/stream` (connectors-prd §4.9)
// ---------------------------------------------------------------------------

/**
 * Connector SSE event types.
 *
 * * `connector.created`         — new row inserted (post-OAuth completion).
 * * `connector.status_changed`  — status transition (e.g. connected →
 *                                 error after a 401 refresh).
 * * `connector.scope_changed`   — scope set was added to or shrunk via
 *                                 re-OAuth.
 * * `connector.error_threshold` — N consecutive errors crossed the
 *                                 tenant-policy threshold; FE renders the
 *                                 "needs reconnect" badge prominently.
 * * `heartbeat`                 — keepalive comment frame on the SSE
 *                                 wire; not a real event.
 */
export type ConnectorStreamEventType =
  | "connector.created"
  | "connector.status_changed"
  | "connector.scope_changed"
  | "connector.error_threshold"
  | "heartbeat";

/**
 * SSE envelope mirroring the inbox / home / project streams. Monotonic
 * `sequence_no` per `(tenant_id, user_id)` channel; reconnect via
 * `Last-Event-ID`.
 */
export interface ConnectorStreamEnvelope {
  readonly event_id: string;
  readonly sequence_no: number;
  readonly event_type: ConnectorStreamEventType;
  readonly connector?: Connector;
  readonly created_at: string;
}
