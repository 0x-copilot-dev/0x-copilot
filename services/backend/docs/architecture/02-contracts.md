# Contracts — backend

All Pydantic domain records used at API and persistence boundaries.
Every public class extends `BackendContract` (extra=forbid, frozen=True, validate_assignment=True).

See also:

- [00-system-map.md](00-system-map.md) — which module owns which domain
- [01-request-lifecycle.md](01-request-lifecycle.md) — how requests reach route handlers
- [features/mcp-registry.md](../features/mcp-registry.md) — MCP record lifecycle

Source: `backend_app/contracts.py`

---

## Base type and validators

`BackendContract` — base Pydantic model. All records inherit it.

`Validators` — shared normalization and input validation:

| Method                                        | What it does                                                              |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| `normalize_id(v)`                             | Strip + `^[A-Za-z0-9][A-Za-z0-9._:-]*$` check                             |
| `normalize_skill_slug(v)`                     | Lowercase, replace `-`/space with `_`, regex clean                        |
| `normalize_text(v)`                           | Strip + non-empty check                                                   |
| `validate_public_mcp_url(v, allow_localhost)` | Must be https (or http+localhost); blocks private IPs and RFC-1918 ranges |

---

## MCP registry records

### Enums

| Enum              | Values                                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------------------- |
| `McpTransport`    | `http`, `sse`, `stdio`                                                                                |
| `McpAuthMode`     | `none`, `oauth2`, `api_key`, `service_account`                                                        |
| `McpAuthState`    | `unauthenticated`, `auth_skipped`, `auth_pending`, `authenticated`, `auth_failed`, `auth_unsupported` |
| `McpServerHealth` | `healthy`, `degraded`, `unavailable`, `disabled`                                                      |

### `McpServerRecord`

Persistence row for one registered MCP server.

| Field                                       | Type                           | Notes                                     |
| ------------------------------------------- | ------------------------------ | ----------------------------------------- |
| `server_id`                                 | `str`                          | Default: `uuid4().hex`                    |
| `org_id`, `user_id`                         | `str`                          | Tenant scope + owner                      |
| `name`                                      | `str`                          | Slug-normalized; unique per org           |
| `display_name`                              | `str`                          | UI label                                  |
| `url`                                       | `str`                          | Validated public HTTPS URL                |
| `transport`                                 | `McpTransport`                 | Default `http`                            |
| `auth_mode`                                 | `McpAuthMode`                  | Default `oauth2`                          |
| `auth_state`                                | `McpAuthState`                 | Default `unauthenticated`                 |
| `health`                                    | `McpServerHealth`              | Default `healthy`                         |
| `enabled`                                   | `bool`                         | Default `True`                            |
| `oauth_client`                              | `McpOAuthClientConfig \| None` | Encrypted client config                   |
| `logo_url`, `brand_color`, `scopes_summary` | `str \| None`                  | Brand metadata (presentation only)        |
| `default_scopes`                            | `tuple[str, ...]`              | Resume-from-paused payload                |
| `admin_managed`                             | `bool`                         | Gates "Enable in Settings" popover action |
| `description`                               | `str`                          | Copied from catalog on install            |
| `created_at`, `updated_at`                  | `datetime`                     | UTC timestamps                            |

### `McpAuthSessionRecord`

In-flight PKCE OAuth session while user completes the OAuth popup.

| Field                            | Type       | Notes                             |
| -------------------------------- | ---------- | --------------------------------- |
| `session_id`                     | `str`      | Default: `uuid4().hex`            |
| `server_id`, `org_id`, `user_id` | `str`      | Scope                             |
| `state`                          | `str`      | CSRF token (UUID)                 |
| `code_verifier`                  | `str`      | PKCE S256 verifier                |
| `redirect_uri`, `auth_url`       | `str`      | OAuth redirect + initial auth URL |
| `expires_at`                     | `datetime` | Session TTL                       |

### `TokenEnvelope`

Encrypted token pair stored per (server, user, org).

| Field                            | Type               | Notes                                          |
| -------------------------------- | ------------------ | ---------------------------------------------- |
| `connection_id`                  | `str`              | Default: `uuid4().hex`                         |
| `server_id`, `org_id`, `user_id` | `str`              | Scope                                          |
| `encrypted_access_token`         | `str`              | Fernet (dev) or KMS envelope                   |
| `encrypted_refresh_token`        | `str \| None`      | Optional                                       |
| `token_type`                     | `str`              | Default `Bearer`                               |
| `expires_at`                     | `datetime \| None` | Token expiry                                   |
| `kms_key_id`                     | `str \| None`      | NULL for Fernet rows; KMS key ref for rotation |

### HTTP shapes

| Class                          | Direction      | Notes                                                                                           |
| ------------------------------ | -------------- | ----------------------------------------------------------------------------------------------- |
| `CreateMcpServerRequest`       | Client → route | `org_id`, `user_id`, `url`, optional display_name + transport + auth_mode                       |
| `UpdateMcpServerRequest`       | Client → route | Partial: display_name, enabled, oauth_client                                                    |
| `McpServerResponse`            | Route → client | `from_record()` factory; strips `oauth_client` secrets, exposes `oauth_client_configured: bool` |
| `McpCatalogEntryResponse`      | Route → client | Static catalog entry; `slug`, brand metadata, `requires_pre_registered_client`                  |
| `InstallMcpServerRequest`      | Client → route | Installs a catalog slug; idempotent by slug → `seed:<slug>` server_id                           |
| `McpAuthStartRequest/Response` | Client → route | Begins OAuth; returns `auth_url`, `expires_at`                                                  |
| `McpAuthCallbackRequest`       | IdP → route    | CSRF `state` + `code` or `error`                                                                |

### Internal shapes (consumed by ai-backend)

| Class                            | Notes                                                                             |
| -------------------------------- | --------------------------------------------------------------------------------- |
| `InternalMcpServerCard`          | Trimmed view for `GET /internal/v1/mcp/servers`; no secrets; includes `load_cost` |
| `InternalMcpClientSession`       | `url`, `transport`, `auth_state`, `credential_ref` for proxy calls                |
| `InternalMcpRpcRequest/Response` | JSON-RPC payload pass-through to the backend's MCP proxy                          |

---

## Skill records

### Enums

| Enum              | Values                        |
| ----------------- | ----------------------------- |
| `SkillScope`      | `user`, `org`                 |
| `SkillSourceType` | `user`, `preloaded`, `system` |

### `SkillRecord`

| Field                            | Type              | Notes                                                                          |
| -------------------------------- | ----------------- | ------------------------------------------------------------------------------ |
| `skill_id`                       | `str`             | Default: `uuid4().hex`                                                         |
| `org_id`, `user_id`              | `str`             | Scope                                                                          |
| `name`                           | `str`             | Slug-normalized                                                                |
| `display_name`, `description`    | `str`             | UI text                                                                        |
| `markdown`                       | `str`             | The skill prompt body                                                          |
| `virtual_path`                   | `str`             | Logical path for skill lookup                                                  |
| `enabled`                        | `bool`            | Default `True`                                                                 |
| `scope`                          | `SkillScope`      | `user` or `org`                                                                |
| `source_type`                    | `SkillSourceType` | `user` = created by user; `preloaded` = seed; `system` = ai-backend filesystem |
| `version`                        | `int ≥ 1`         | Incremented on content update                                                  |
| `allowed_tools`, `compatibility` | `tuple[str, ...]` | Tool-use and model restrictions                                                |

### HTTP shapes

| Class                 | Notes                                                                     |
| --------------------- | ------------------------------------------------------------------------- |
| `CreateSkillRequest`  | `org_id`, `user_id`, `markdown`, optional display_name, scope             |
| `UpdateSkillRequest`  | Partial: markdown, display_name, enabled, scope                           |
| `SkillResponse`       | Public view of a SkillRecord                                              |
| `InternalSkillCard`   | Minimal view for ai-backend's skill listing                               |
| `InternalSkillBundle` | Full skill content for ai-backend's skill execution (includes `markdown`) |

---

## Identity records

### Core identity

| Record                     | ID prefix | Purpose                                                                     |
| -------------------------- | --------- | --------------------------------------------------------------------------- |
| `OrganizationRecord`       | `org_`    | Org with `slug`, `deployment_kind`, `status`                                |
| `UserRecord`               | `usr_`    | User with `primary_email`, `status`, `is_service_account`                   |
| `OrganizationMemberRecord` | `mem_`    | `(org, user)` membership; `source` = local/oidc/saml/scim                   |
| `RoleRecord`               | `role_`   | Role with `permission_scopes`; `is_system=True` → `org_id` must be NULL     |
| `RoleAssignmentRecord`     | `asn_`    | `(org, user, role)` with `granted_by`, `revoked_at`                         |
| `AuthProviderRecord`       | `prv_`    | OIDC/SAML/SCIM provider config; `encrypted_client_secret` for OAuth clients |

### Sessions (A2)

| Record               | ID prefix | Purpose                                                                                                         |
| -------------------- | --------- | --------------------------------------------------------------------------------------------------------------- |
| `SessionRecord`      | `sid_`    | `token_hash` (sha256 of bearer signature), `roles`, `permission_scopes`, `connector_scopes`, `mfa_satisfied_at` |
| `SessionMintResult`  | —         | `bearer_token` plaintext (returned once only)                                                                   |
| `SessionTouchResult` | —         | Canonical identity from the session row; `mfa_satisfied`, `mfa_satisfied_at`                                    |

### OIDC (A3)

| Record                     | ID prefix | Purpose                                                                             |
| -------------------------- | --------- | ----------------------------------------------------------------------------------- |
| `OidcAuthenticationRecord` | `oac_`    | In-flight authorize request; `state` (CSRF), `nonce`, `code_verifier`, `expires_at` |
| `OidcIdentityRecord`       | `oid_`    | `(provider_id, subject)` → `user_id` mapping                                        |
| `OidcRefreshTokenRecord`   | `ort_`    | Encrypted refresh token from the IdP                                                |
| `OidcJwksCacheRecord`      | `jwk_`    | Cached JWKS document for ID token validation                                        |

### Local passwords (A4)

| Record                     | ID prefix | Purpose                                                                             |
| -------------------------- | --------- | ----------------------------------------------------------------------------------- |
| `LocalCredentialRecord`    | `crd_`    | argon2id hash + `previous_hashes` (reuse window) + `must_rotate_at`                 |
| `PasswordPolicyRecord`     | `pwp_`    | Per-org complexity + rotation policy                                                |
| `PasswordResetTokenRecord` | `prt_`    | Single-use sha256-hashed reset token                                                |
| `IdentityPolicyRecord`     | —         | Per-org toggles: `local_password_enabled`, `mfa_required`, `step_up_window_seconds` |

### SAML (A5)

| Record                     | ID prefix | Purpose                                                      |
| -------------------------- | --------- | ------------------------------------------------------------ |
| `SamlAuthenticationRecord` | `sac_`    | In-flight SAML request; `status` = pending/consumed/rejected |
| `SamlIdentityRecord`       | `sid_`    | `(provider_id, name_id)` → `user_id`                         |

### MFA (A6)

| Record                     | ID prefix | Purpose                                        |
| -------------------------- | --------- | ---------------------------------------------- |
| `MfaFactorRecord`          | `mff_`    | Generic factor row; `kind` = totp/webauthn     |
| `TotpSecretRecord`         | `tot_`    | Encrypted TOTP seed + `last_step` replay guard |
| `WebAuthnCredentialRecord` | `wac_`    | COSE public key + `sign_count`                 |
| `MfaChallengeRecord`       | `mfc_`    | Single-use nonce binding a verify request      |
| `MfaRecoveryCodeRecord`    | `mfr_`    | One-shot sha256-hashed recovery code           |

### SCIM (A7)

| Record                  | ID prefix | Purpose                                                                 |
| ----------------------- | --------- | ----------------------------------------------------------------------- |
| `ScimTokenRecord`       | `sct_`    | Per-org SCIM bearer; `token_hash` (sha256) + `token_prefix` for listing |
| `ScimExternalIdRecord`  | `sxi_`    | `external_id` → `user_id` or `group_id` (exactly one)                   |
| `ScimGroupRecord`       | `scg_`    | SCIM-managed group with optional `mapped_role_id`                       |
| `ScimGroupMemberRecord` | `sgm_`    | `(group, user)` membership with `removed_at` soft-delete                |

### Lockouts (A8)

| Record                 | ID prefix | Purpose                                                                       |
| ---------------------- | --------- | ----------------------------------------------------------------------------- |
| `LockoutPolicyRecord`  | `lkp_`    | Per-org: `max_failures`, `failure_window_seconds`, `lockout_duration_seconds` |
| `AccountLockoutRecord` | `lko_`    | One active lockout per (org, user); `unlocked_at` ends it                     |

### Invitations (PR 4.2)

`InvitationRecord` (`inv_`) — `token_hash` (sha256) + `token_prefix`; soft revoke + soft accept via timestamps.
`InvitationMintResult` — carries `token_plaintext` which is returned once and never stored.

### Login email-first / magic-link (PR 5.1)

| Record                     | Purpose                                                            |
| -------------------------- | ------------------------------------------------------------------ |
| `AuthProviderDomainRecord` | Domain → `(org, provider)` claim; drives SSO enforcement           |
| `MagicLinkTokenRecord`     | sha256-hashed token; `candidate_orgs` materialized at request time |
| `MagicLinkCallbackResult`  | `SESSION_MINTED` or `WORKSPACE_PICK_REQUIRED` outcome              |

---

## Audit records

All four audit chains share the same chain-signature fields: `seq`, `prev_hash`, `signature`, `key_version` — appended by the store on write. These are `None` in newly constructed objects.

| Record                     | Chain                                              |
| -------------------------- | -------------------------------------------------- |
| `AuditEventRecord`         | MCP events (OAuth, token rotation)                 |
| `SkillAuditEventRecord`    | Skill CRUD events                                  |
| `IdentityAuditEventRecord` | Identity events (login, role change, provisioning) |
| `DeployAuditEventRecord`   | CI/CD deploy events                                |

---

## Pydantic conventions

- All records: `extra="forbid"`, `frozen=True` — callers cannot set arbitrary fields.
- ID fields always validated via `Validators.normalize_id()` — rejects empty strings and shell-injection characters.
- Sensitive secrets (bearer tokens, TOTP seeds, client secrets) are never stored as plaintext in any record; the encrypted form uses `TokenVault` wrapping.
- `_Fields` / `_IdentityFields` constant pools are used by validator decorator arguments to prevent typo-silent field mismatches.
