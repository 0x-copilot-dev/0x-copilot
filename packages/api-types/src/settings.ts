// Settings (Phase 12 — polish bucket) — wire contract.
//
// Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
//   §3 (data shape — referenced from §4.4), §4.4 (Settings endpoints),
//   §6.4 (ACL: user defaults = owner; workspace defaults = admin),
//   §7.4 (frontend surface — /settings/notifications +
//   /settings/security/webhooks).
//
// Settings live in JSONB blobs keyed by namespace on the existing
// `tenant_settings` / `user_settings` tables (sub-PRD §5.2). This file
// defines the wire types for the three new namespaces Phase 12 lands:
//
//   * `notifications` — per-user (NotificationDefaults).
//   * `notifications` — admin workspace (WorkspaceNotificationDefaults).
//   * `security.webhooks` — admin workspace (WebhookSecurityDefaults).
//
// Settings is intentionally NOT a destination (master PRD §3.5); it
// lives off the profile menu. The `/settings/*` routes share visual
// shell components but no destination card in the nav.
//
// Notification preferences v2 (`NotificationChannelV2`,
// `NotificationEventKind`, `NotificationQuietHours`) ALREADY live in
// `index.ts` (the PR-B4 block, Phase 7/8). This file is intentionally a
// thin compositional layer: it defines DEFAULTS blobs as namespaced
// JSONB payloads. The PR-B4 user-preferences endpoint stays unchanged.

import type { UserId } from "./brands";

// ---------------------------------------------------------------------------
// User notification defaults (sub-PRD §4.4)
// ---------------------------------------------------------------------------

/**
 * Per-destination toggle. Each destination opts in/out of notifications.
 * The set is open-ended (the FE renders one row per known destination);
 * the wire keeps it as a plain dict keyed by destination slug for
 * forward-compat.
 *
 * Known destination slugs at Phase 12: `home`, `inbox`, `todos`,
 * `projects`, `library`, `agents`, `tools`, `connectors`, `team`,
 * `memory`, `routines`. New destinations append additional keys without
 * breaking the wire.
 */
export type PerDestinationToggle = Readonly<Record<string, boolean>>;

/**
 * `GET /v1/settings/notifications` response — per-user defaults.
 *
 * `quiet_hours` mirrors the existing `NotificationQuietHours` shape
 * (defined in index.ts under PR-B4). We do not re-export that here to
 * avoid a circular dependency — the canonical declaration stays in
 * index.ts.
 */
export interface NotificationDefaults {
  readonly user_id: UserId;
  /** Per-destination on/off. Missing keys default to `true`. */
  readonly destinations_enabled: PerDestinationToggle;
  /** Quiet-hours window; when `enabled=false`, notifications never mute. */
  readonly quiet_hours: NotificationQuietHoursBlob;
  readonly updated_at: string;
}

/**
 * Inlined quiet-hours shape so this file is self-contained. Matches the
 * PR-B4 `NotificationQuietHours` exactly (sub-PRD §U-S1).
 */
export interface NotificationQuietHoursBlob {
  readonly enabled: boolean;
  /** HH:MM 24h, in `tz`. */
  readonly from_local: string;
  /** HH:MM 24h, in `tz`. */
  readonly to_local: string;
  /** IANA tz id, e.g. "America/Los_Angeles". */
  readonly tz: string;
}

/** Body for `PATCH /v1/settings/notifications` (user). */
export interface UpdateNotificationDefaultsRequest {
  readonly destinations_enabled?: PerDestinationToggle;
  readonly quiet_hours?: NotificationQuietHoursBlob;
}

// ---------------------------------------------------------------------------
// Workspace notification defaults (admin) — sub-PRD §U-S2
// ---------------------------------------------------------------------------

/**
 * `GET /v1/settings/workspace/notifications` response — admin defaults
 * applied to new users (and as the fallback for users who haven't set
 * personal defaults).
 *
 * The `quiet_hours` workspace default is purely advisory — individual
 * users can override on their own row (`NotificationDefaults`).
 */
export interface WorkspaceNotificationDefaults {
  readonly destinations_enabled: PerDestinationToggle;
  readonly quiet_hours: NotificationQuietHoursBlob;
  readonly updated_at: string;
  readonly updated_by_user_id: UserId | null;
}

/** Body for `PATCH /v1/settings/workspace/notifications` (admin). */
export interface UpdateWorkspaceNotificationDefaultsRequest {
  readonly destinations_enabled?: PerDestinationToggle;
  readonly quiet_hours?: NotificationQuietHoursBlob;
}

// ---------------------------------------------------------------------------
// Webhook security defaults (admin) — sub-PRD §U-S3 / Routines §9.7 Q6
// ---------------------------------------------------------------------------

/**
 * `GET /v1/settings/security/webhooks` response — workspace webhook
 * signing defaults. Read by Phase 11 Connectors webhook create endpoint
 * to default-on HMAC, default-on IP allowlist, and to enforce maximum
 * secret-age rotation policy.
 *
 *   * `default_hmac_on`       — when `true`, new webhooks default to
 *     HMAC signing on (Routines §9.7 Q6 HMAC-of-payload UX).
 *   * `require_ip_allowlist`  — when `true`, webhook create endpoint
 *     refuses requests that don't carry an IP allowlist.
 *   * `max_secret_age_days`   — webhook secrets older than this trigger
 *     a "rotate me" warning surfaced to the admin (Phase 11 Connectors).
 *     `0` means "never expire".
 */
export interface WebhookSecurityDefaults {
  readonly default_hmac_on: boolean;
  readonly require_ip_allowlist: boolean;
  readonly max_secret_age_days: number;
  readonly updated_at: string;
  readonly updated_by_user_id: UserId | null;
}

/** Body for `PATCH /v1/settings/security/webhooks` (admin). */
export interface UpdateWebhookSecurityDefaultsRequest {
  readonly default_hmac_on?: boolean;
  readonly require_ip_allowlist?: boolean;
  readonly max_secret_age_days?: number;
}
