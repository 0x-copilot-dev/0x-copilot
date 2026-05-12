# Policies

Tool-use policies, MFA policy, privacy settings, and workspace configuration.

See also:

- [architecture/00-system-map.md](../architecture/00-system-map.md) — module locations

---

## What it does

Backend owns three policy domains:

1. **Tool-use policies** — which tools are allowed/denied per org
2. **MFA and identity policy** — whether MFA is required, step-up windows, which IdPs are active
3. **Privacy settings** — data residency region, conversation retention slider

---

## Tool-use policies (`backend_app/policies/store.py`)

Routes: `GET/PUT /internal/v1/tool-use-policies` (service token).

`ToolUsePolicyRecord` — per-org policy row:

| Field           | Type        | Notes                                      |
| --------------- | ----------- | ------------------------------------------ |
| `org_id`        | `str`       | Tenant scope                               |
| `allowed_tools` | `list[str]` | Allowlist (`*` = all)                      |
| `denied_tools`  | `list[str]` | Denylist (takes precedence over allowlist) |
| `updated_at`    | `datetime`  | UTC                                        |

The ai-backend reads these at run-start as part of `McpPermissionPolicy`. A tool in
`denied_tools` is never added to the model's tool list, regardless of the connector's
capability list.

---

## MFA policy (`IdentityPolicyRecord` — `backend_app/contracts.py`)

Part of the identity store (`backend_app/identity/store.py`).

Routes: `GET/PUT /internal/v1/auth/workspace/mfa-policy` — `RequireScopes("admin:users")`.

| Field                    | Type   | Default | Notes                                                                 |
| ------------------------ | ------ | ------- | --------------------------------------------------------------------- |
| `mfa_required`           | `bool` | `False` | When `True`, new sessions get `mfa:pending` scope until MFA satisfied |
| `step_up_window_seconds` | `int`  | 300     | How long a completed MFA satisfies step-up routes                     |
| `local_password_enabled` | `bool` | `True`  | When `False`, local password routes return 404                        |

The facade's `requires_recent_mfa(identity, max_age_seconds)` checks `mfa_satisfied_at`
against this window for step-up-gated routes.

---

## Privacy settings (`backend_app/privacy/store.py`)

Routes: `GET/PUT /internal/v1/auth/privacy` (service token).

`PrivacySettingsRecord` — per-org:

| Field                         | Type          | Notes                                 |
| ----------------------------- | ------------- | ------------------------------------- |
| `org_id`                      | `str`         | Tenant scope                          |
| `data_residency_region`       | `str \| None` | e.g., `us-east-1`, `eu-west-1`        |
| `conversation_retention_days` | `int \| None` | Feeds the ai-backend retention policy |
| `updated_at`                  | `datetime`    | UTC                                   |

The ai-backend reads `conversation_retention_days` via the retention policy resolver to
compute expiry dates for conversation data. When `None`, the platform default applies.

---

## Workspace settings (`backend_app/routes/workspace.py`)

Routes: `/internal/v1/auth/workspace/*` (service token).

Aggregates org-level configuration surfaced to the settings UI:

- Display name / slug / branding
- Enabled auth providers
- SCIM provisioning status
- MFA policy state

`GET /internal/v1/auth/workspace` — returns a summary of all toggles for the Settings page.
`PUT /internal/v1/auth/workspace` — updates workspace-level settings.

---

## Notification preferences (`backend_app/notifications/store.py`)

Routes: `GET/PUT /internal/v1/auth/notifications` (service token).

`NotificationPreferenceRecord` — per-user:

| Field                                  | Type              | Notes                                          |
| -------------------------------------- | ----------------- | ---------------------------------------------- |
| `user_id`, `org_id`                    | `str`             | Owner                                          |
| `email_enabled`                        | `bool`            | Master email toggle                            |
| `quiet_hours_start`, `quiet_hours_end` | `time \| None`    | Local time window for notification suppression |
| `channels`                             | `dict[str, bool]` | Per-channel overrides                          |
