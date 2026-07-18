// AC9 — Desktop MCP connector OAuth transport (desktop-only variant).
//
// This module is DELIBERATELY separate from ./connectors.ts. The shipped web
// redirect flow (`apps/frontend` ConnectorsRoute.tsx / connectorsApi.ts)
// consumes `StartConnectorOAuthResponse` (`{ authorization_url; state }`) and
// `ConnectorOAuthCallbackRequest` (`{ code; state }`) — those shapes are LEFT
// BYTE-IDENTICAL. The desktop OAuth transport requires extra fields
// (`oauth_session_id`, a `callback` union, `requested_product_scope`, an
// optional `code`); folding them into the shared web shapes would be a
// breaking change (see 09-ac9-desktop-connectors.md §"Web compatibility and
// the shared-type split"). They live here instead, so:
//
//   * web typecheck + the shipped web connector flow stay unchanged, and
//   * only the desktop facade routes accept/return these variants.
//
// Wire-only file: no logic, no fetch, no view models. The backend
// (`services/backend`) is the source of truth; provider access/refresh tokens
// and client secrets NEVER appear on this wire — they stay encrypted in the
// backend TokenVault. Every response here carries only safe connection
// metadata.

import type { ConnectorSlug } from "./projects";
import type {
  ConnectorAvailability,
  ConnectorCapabilitySummary,
} from "./connectors";

// ---------------------------------------------------------------------------
// Where the desktop should receive the OAuth code.
// ---------------------------------------------------------------------------
//
// The backend RECONSTRUCTS the redirect target from these validated fields; it
// never trusts an arbitrary redirect URI. The loopback path is fixed
// server-side; only the (validated, unprivileged) port varies. The deep-link
// URI is the single registered `enterprise://oauth/callback` scheme.

export const DESKTOP_CONNECTOR_LOOPBACK_PATH = "/connectors/oauth/cb" as const;
export const DESKTOP_CONNECTOR_DEEP_LINK_URI =
  "enterprise://oauth/callback" as const;

export interface DesktopLoopbackCallback {
  readonly kind: "desktop_loopback";
  readonly port: number;
  readonly path: typeof DESKTOP_CONNECTOR_LOOPBACK_PATH;
}

export interface DesktopDeepLinkCallback {
  readonly kind: "desktop_deep_link";
  readonly uri: typeof DESKTOP_CONNECTOR_DEEP_LINK_URI;
}

export type DesktopConnectorCallback =
  | DesktopLoopbackCallback
  | DesktopDeepLinkCallback;

// ---------------------------------------------------------------------------
// Start OAuth — desktop variant of `POST /v1/connectors/{slug}/desktop/start-oauth`.
// ---------------------------------------------------------------------------

/**
 * The product scope the desktop is asking for. `read` is least-privilege and
 * the default; `draft` unlocks the optional draft/compose tools (still gated
 * by runtime per-call approval). Write scopes are never requested from the
 * desktop start body — they are a separate, deliberate reauthorization.
 */
export type DesktopRequestedProductScope = "read" | "draft";

/**
 * `POST /v1/connectors/{slug}/desktop/start-oauth` request body. Only the
 * loopback port / deep-link kind and the requested product scope cross the
 * wire — the backend owns the redirect reconstruction and the actual provider
 * scope set.
 */
export interface DesktopStartConnectorOAuthRequest {
  readonly callback: DesktopConnectorCallback;
  readonly requested_product_scope: DesktopRequestedProductScope;
}

/**
 * `POST /v1/connectors/{slug}/desktop/start-oauth` response. `oauth_session_id`
 * equals the single-use `state` the backend minted; the desktop arms its
 * loopback / deep-link listener on `state` and opens `authorization_url` in
 * the system browser. `requested_permissions` are safe permission identifiers
 * (scope strings / admin-permission names) for display only — never a token.
 */
export interface DesktopStartConnectorOAuthResponse {
  readonly oauth_session_id: string;
  readonly authorization_url: string;
  readonly state: string;
  readonly expires_at: string;
  readonly requested_permissions: ReadonlyArray<string>;
}

// ---------------------------------------------------------------------------
// Callback — desktop variant of `POST /v1/connectors/desktop/oauth-callback`.
// ---------------------------------------------------------------------------

/**
 * `POST /v1/connectors/desktop/oauth-callback` request body. The desktop main
 * process posts ONLY `code` + `state` (+ provider error metadata) — never a
 * token. `oauth_session_id` must equal `state`; the backend also enforces that
 * the caller's verified org/user owns the session (confused-deputy defense).
 */
export interface DesktopConnectorOAuthCallbackRequest {
  readonly oauth_session_id: string;
  readonly code?: string;
  readonly state: string;
  readonly error?: string;
  readonly error_description?: string;
}

/**
 * `POST /v1/connectors/desktop/oauth-callback` response — SAFE post-callback
 * metadata only. No provider access/refresh token and no client secret ever
 * appears here (they stay in the backend TokenVault). `auth_state` reflects the
 * MCP registration state after token exchange (`authenticated` on success).
 */
export interface DesktopConnectorConnectionResult {
  readonly server_id: string;
  readonly connector_slug: ConnectorSlug;
  readonly display_group: string;
  readonly auth_state: string;
}

// ---------------------------------------------------------------------------
// Reconciled desktop catalog — `GET /v1/connectors/desktop/catalog`.
// ---------------------------------------------------------------------------

/**
 * One reconciled desktop connector profile row: a marketing slug joined to an
 * installable MCP server behind a pinned, verified profile. Availability is the
 * honest default state BEFORE any live probe (preview connectors read
 * `preview` until the deployment enables them; tenant-template profiles read
 * `admin_setup_required`). Provider scopes / tools are description-only.
 */
export interface DesktopConnectorCatalogEntry {
  readonly slug: ConnectorSlug;
  readonly display_name: string;
  readonly description: string;
  readonly display_group: string;
  readonly release_stage: "stable" | "preview";
  readonly availability: ConnectorAvailability;
  readonly requested_permissions: ReadonlyArray<string>;
  readonly capabilities: ReadonlyArray<ConnectorCapabilitySummary>;
  readonly unsupported_capabilities: ReadonlyArray<string>;
  readonly reference_urls: ReadonlyArray<string>;
}

/**
 * `GET /v1/connectors/desktop/catalog` response. The backend-owned
 * reconciliation overlay — NOT a third client catalog. The desktop renders it
 * read-only and kicks the connect flow per slug through the main process.
 */
export interface DesktopConnectorCatalogResponse {
  readonly entries: ReadonlyArray<DesktopConnectorCatalogEntry>;
}
