// Typed wrappers for the Phase 12 Settings module
// (sub-PRD `team-memory-cmdk-prd.md` §4.4).
//
// Three namespaces:
//   * User notification defaults (owner only)
//       GET   /v1/settings/notifications
//       PATCH /v1/settings/notifications
//   * Workspace notification defaults (admin only)
//       GET   /v1/settings/workspace/notifications
//       PATCH /v1/settings/workspace/notifications
//   * Workspace webhook security defaults (admin only)
//       GET   /v1/settings/security/webhooks
//       PATCH /v1/settings/security/webhooks
//
// Settings is NOT a destination per master PRD §3.5 — these blobs are
// rendered under `/settings/*` profile-menu pages. Storage is JSONB on
// the existing `tenant_settings` / `user_settings` tables (sub-PRD §5.2
// — no parallel table).

import type {
  NotificationDefaults,
  UpdateNotificationDefaultsRequest,
  UpdateWebhookSecurityDefaultsRequest,
  UpdateWorkspaceNotificationDefaultsRequest,
  WebhookSecurityDefaults,
  WorkspaceNotificationDefaults,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "./config";
import { httpGet, httpPatchQuery } from "./http";

// ===========================================================================
// User notification defaults
// ===========================================================================

/** GET /v1/settings/notifications — per-user defaults (owner only). */
export function getUserNotificationDefaults(
  identity: RequestIdentity,
): Promise<NotificationDefaults> {
  return httpGet<NotificationDefaults>("/v1/settings/notifications", identity);
}

/** PATCH /v1/settings/notifications — per-user defaults patch. */
export function patchUserNotificationDefaults(
  identity: RequestIdentity,
  body: UpdateNotificationDefaultsRequest,
): Promise<NotificationDefaults> {
  return httpPatchQuery<NotificationDefaults>(
    "/v1/settings/notifications",
    body,
    identity,
  );
}

// ===========================================================================
// Workspace notification defaults
// ===========================================================================

/** GET /v1/settings/workspace/notifications — admin only. */
export function getWorkspaceNotificationDefaults(
  identity: RequestIdentity,
): Promise<WorkspaceNotificationDefaults> {
  return httpGet<WorkspaceNotificationDefaults>(
    "/v1/settings/workspace/notifications",
    identity,
  );
}

/** PATCH /v1/settings/workspace/notifications — admin only. */
export function patchWorkspaceNotificationDefaults(
  identity: RequestIdentity,
  body: UpdateWorkspaceNotificationDefaultsRequest,
): Promise<WorkspaceNotificationDefaults> {
  return httpPatchQuery<WorkspaceNotificationDefaults>(
    "/v1/settings/workspace/notifications",
    body,
    identity,
  );
}

// ===========================================================================
// Webhook security defaults
// ===========================================================================

/** GET /v1/settings/security/webhooks — admin only. */
export function getWebhookSecurityDefaults(
  identity: RequestIdentity,
): Promise<WebhookSecurityDefaults> {
  return httpGet<WebhookSecurityDefaults>(
    "/v1/settings/security/webhooks",
    identity,
  );
}

/** PATCH /v1/settings/security/webhooks — admin only. */
export function patchWebhookSecurityDefaults(
  identity: RequestIdentity,
  body: UpdateWebhookSecurityDefaultsRequest,
): Promise<WebhookSecurityDefaults> {
  return httpPatchQuery<WebhookSecurityDefaults>(
    "/v1/settings/security/webhooks",
    body,
    identity,
  );
}
