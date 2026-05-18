"""Phase 12 — Settings module.

Three namespaces land in this PR:

  user:    "notifications"        -> NotificationDefaults (per-user)
  tenant:  "notifications"        -> WorkspaceNotificationDefaults (admin)
  tenant:  "security.webhooks"    -> WebhookSecurityDefaults (admin)

Storage shape:

  * User namespaces ride inside the existing ``user_preferences``
    (migration 0018) JSONB blob keyed as a top-level dict entry. This
    means the Phase 2 home activity window pref + P9-A2 last_visit
    cursor (both stored under ``home.*``) are preserved by deep-merge —
    a PATCH on ``settings.notifications`` never clobbers ``home.*``.

  * Tenant namespaces ride in the new ``tenant_settings`` table
    (migration 0033). One row per (tenant_id, namespace).

The HMAC algorithm + header names referenced by the
``security.webhooks`` namespace REMAIN in
``backend_app.webhooks.signer`` — settings does not redefine those
constants.
"""

from backend_app.settings.routes import register_settings_routes
from backend_app.settings.service import SettingsService
from backend_app.settings.store import (
    InMemorySettingsStore,
    SettingsStore,
    TENANT_NAMESPACES,
    USER_NAMESPACES,
)


__all__ = [
    "InMemorySettingsStore",
    "SettingsService",
    "SettingsStore",
    "TENANT_NAMESPACES",
    "USER_NAMESPACES",
    "register_settings_routes",
]
