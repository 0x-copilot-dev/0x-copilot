# Internal API — backend

All routes under `/internal/v1/*`. These routes are **not** exposed via `backend-facade`.
Callers: `ai-backend` (MCP, skills, runtime policies), `backend-facade` (auth/sessions).

Auth for all internal routes: `X-Enterprise-Service-Token` header must match `ENTERPRISE_SERVICE_TOKEN`.
Identity is derived from `x-enterprise-org-id` and `x-enterprise-user-id` headers.

See also:

- [reference/public-api.md](public-api.md) — browser-facing routes
- [architecture/01-request-lifecycle.md](../architecture/01-request-lifecycle.md) — auth verification path
- [docs/specs/internal-api.md](../specs/internal-api.md) — detailed contract spec

---

## Sessions (A2)

Called by the facade to validate and refresh session state.

| Method   | Path                                      | Scope         | Notes                                               |
| -------- | ----------------------------------------- | ------------- | --------------------------------------------------- |
| `POST`   | `/internal/v1/auth/sessions`              | Service token | Create session; returns `SessionMintResult`         |
| `POST`   | `/internal/v1/auth/sessions/touch`        | Service token | Validate + touch; returns `SessionTouchResult`      |
| `GET`    | `/internal/v1/auth/sessions`              | Service token | List active sessions for user                       |
| `DELETE` | `/internal/v1/auth/sessions/{session_id}` | Service token | Revoke session                                      |
| `POST`   | `/internal/v1/auth/api-keys/verify`       | Service token | Verify `atlas_pk_*` bearer; returns identity claims |

---

## OIDC SSO (A3)

| Method | Path                               | Auth                | Notes                                       |
| ------ | ---------------------------------- | ------------------- | ------------------------------------------- |
| `POST` | `/internal/v1/auth/oidc/authorize` | Service token       | Begin OIDC flow; returns `auth_url`         |
| `POST` | `/internal/v1/auth/oidc/callback`  | Service token       | Exchange code; returns `OidcCallbackResult` |
| `GET`  | `/internal/v1/auth/oidc/providers` | Public (login page) | List enabled OIDC providers                 |

---

## SAML 2.0 (A5)

| Method    | Path                                             | Auth                        | Notes                       |
| --------- | ------------------------------------------------ | --------------------------- | --------------------------- |
| `POST`    | `/internal/v1/auth/saml/authorize`               | Service token               | Begin SAML flow             |
| `POST`    | `/internal/v1/auth/saml/acs`                     | Public (assertion endpoint) | ACS: consume SAML assertion |
| `GET`     | `/internal/v1/auth/saml/metadata/{provider_id}`  | Public                      | SP metadata XML             |
| `GET/PUT` | `/internal/v1/auth/saml/providers/{provider_id}` | Service token               | SAML provider config        |

---

## Local passwords (A4)

| Method | Path                                        | Auth          | Notes                                  |
| ------ | ------------------------------------------- | ------------- | -------------------------------------- |
| `POST` | `/internal/v1/auth/passwords/login`         | Service token | Local login; returns bearer            |
| `POST` | `/internal/v1/auth/passwords/change`        | Service token | Change password                        |
| `POST` | `/internal/v1/auth/passwords/reset/request` | Public        | Request reset email (anti-enumeration) |
| `POST` | `/internal/v1/auth/passwords/reset/confirm` | Public        | Consume reset token                    |
| `POST` | `/internal/v1/auth/passwords/bootstrap`     | Service token | One-time admin creation                |

---

## MFA (A6)

| Method | Path                                             | Auth          | Notes                          |
| ------ | ------------------------------------------------ | ------------- | ------------------------------ |
| `GET`  | `/internal/v1/auth/mfa/factors`                  | Service token | List enrolled factors          |
| `POST` | `/internal/v1/auth/mfa/totp/enroll`              | Service token | Begin TOTP enrollment          |
| `POST` | `/internal/v1/auth/mfa/totp/confirm`             | Service token | Confirm TOTP setup             |
| `POST` | `/internal/v1/auth/mfa/challenge`                | Service token | Issue challenge nonce          |
| `POST` | `/internal/v1/auth/mfa/verify`                   | Service token | Verify challenge response      |
| `POST` | `/internal/v1/auth/mfa/recovery/consume`         | Service token | Consume one-shot recovery code |
| `POST` | `/internal/v1/auth/mfa/webauthn/register/start`  | Service token | Begin WebAuthn registration    |
| `POST` | `/internal/v1/auth/mfa/webauthn/register/finish` | Service token | Finish WebAuthn registration   |

---

## User profile (me)

| Method    | Path                               | Auth          | Notes                           |
| --------- | ---------------------------------- | ------------- | ------------------------------- |
| `GET`     | `/internal/v1/auth/me`             | Service token | Caller's profile (from headers) |
| `GET/PUT` | `/internal/v1/auth/me/avatar`      | Service token | Avatar blob                     |
| `GET/PUT` | `/internal/v1/auth/me/mfa`         | Service token | MFA status                      |
| `GET/PUT` | `/internal/v1/auth/me/preferences` | Service token | User preferences                |
| `GET/PUT` | `/internal/v1/auth/me/profile`     | Service token | Display name etc.               |

---

## Members and invitations

| Method             | Path                                        | Scope               | Notes                    |
| ------------------ | ------------------------------------------- | ------------------- | ------------------------ |
| `GET`              | `/internal/v1/auth/members`                 | `admin:users`       | List org members         |
| `GET/PATCH/DELETE` | `/internal/v1/auth/members/{user_id}`       | `admin:users`       | Member detail            |
| `POST`             | `/internal/v1/auth/invitations`             | `admin:users`       | Create invitation        |
| `GET`              | `/internal/v1/auth/invitations`             | `admin:users`       | List pending invitations |
| `POST`             | `/internal/v1/auth/invitations/accept`      | Public (token auth) | Accept invitation        |
| `POST`             | `/internal/v1/auth/invitations/{id}/revoke` | `admin:users`       | Revoke invitation        |

---

## Lockouts

| Method | Path                                          | Scope         | Notes                |
| ------ | --------------------------------------------- | ------------- | -------------------- |
| `GET`  | `/internal/v1/auth/lockouts`                  | `admin:users` | List active lockouts |
| `POST` | `/internal/v1/auth/lockouts/{user_id}/unlock` | `admin:users` | Manually unlock      |

---

## SCIM (A7)

| Method                 | Path                                          | Auth          | Notes                  |
| ---------------------- | --------------------------------------------- | ------------- | ---------------------- |
| `GET/POST`             | `/internal/v1/auth/scim/resource/Users`       | SCIM bearer   | SCIM User list/create  |
| `GET/PUT/PATCH/DELETE` | `/internal/v1/auth/scim/resource/Users/{id}`  | SCIM bearer   | SCIM User detail       |
| `GET/POST`             | `/internal/v1/auth/scim/resource/Groups`      | SCIM bearer   | SCIM Group list/create |
| `GET/PUT/PATCH/DELETE` | `/internal/v1/auth/scim/resource/Groups/{id}` | SCIM bearer   | SCIM Group detail      |
| `POST`                 | `/internal/v1/auth/scim/tokens`               | `admin:users` | Mint SCIM bearer token |
| `GET`                  | `/internal/v1/auth/scim/tokens`               | `admin:users` | List SCIM tokens       |
| `DELETE`               | `/internal/v1/auth/scim/tokens/{token_id}`    | `admin:users` | Revoke SCIM token      |

---

## Workspace and policies

| Method    | Path                                     | Auth          | Notes                        |
| --------- | ---------------------------------------- | ------------- | ---------------------------- |
| `GET/PUT` | `/internal/v1/auth/workspace`            | Service token | Workspace config             |
| `GET/PUT` | `/internal/v1/auth/workspace/mfa-policy` | `admin:users` | MFA policy                   |
| `GET/PUT` | `/internal/v1/auth/notifications`        | Service token | Notification preferences     |
| `GET/PUT` | `/internal/v1/auth/privacy`              | Service token | Privacy / data residency     |
| `GET/PUT` | `/internal/v1/tool-use-policies`         | Service token | Tool allowlist/denylist      |
| `GET/PUT` | `/internal/v1/runtime/policies`          | Service token | Runtime-level policy toggles |

---

## Login email-first / magic-link (PR 5.1)

| Method | Path                                           | Auth                | Notes                  |
| ------ | ---------------------------------------------- | ------------------- | ---------------------- |
| `POST` | `/internal/v1/auth/login-email-first/discover` | Public              | Domain → IdP discovery |
| `POST` | `/internal/v1/auth/login-email-first/start`    | Public              | Request magic link     |
| `POST` | `/internal/v1/auth/login-email-first/callback` | Public (token)      | Consume magic link     |
| `POST` | `/internal/v1/auth/login-email-first/select`   | Public (pick_token) | Workspace selection    |

---

## MCP (internal — for ai-backend)

| Method | Path                                       | Auth          | Notes                                           |
| ------ | ------------------------------------------ | ------------- | ----------------------------------------------- |
| `GET`  | `/internal/v1/mcp/servers`                 | Service token | `InternalMcpServerListResponse` filtered by org |
| `GET`  | `/internal/v1/mcp/sessions/{server_id}`    | Service token | `InternalMcpClientSession` with credential_ref  |
| `POST` | `/internal/v1/mcp/servers/{id}/auth/start` | Service token | Begin OAuth for ai-backend approval flow        |
| `POST` | `/internal/v1/mcp/servers/{id}/rpc`        | Service token | JSON-RPC proxy to MCP server                    |

---

## Skills (internal — for ai-backend)

| Method | Path                             | Auth          | Notes                                     |
| ------ | -------------------------------- | ------------- | ----------------------------------------- |
| `GET`  | `/internal/v1/skills`            | Service token | `InternalSkillListResponse` (no markdown) |
| `GET`  | `/internal/v1/skills/{skill_id}` | Service token | `InternalSkillBundle` (full markdown)     |

---

## Audit

| Method | Path                        | Scope                | Notes                                         |
| ------ | --------------------------- | -------------------- | --------------------------------------------- |
| `GET`  | `/internal/v1/audit`        | `audit:read`         | Cursor-paged unified read across all 4 chains |
| `GET`  | `/internal/v1/audit/export` | `admin:audit_export` | Bulk export with cursor                       |
| `POST` | `/internal/v1/audit/deploy` | Service token        | Record a CI/CD deploy event                   |

---

## SIEM

| Method | Path                          | Scope        | Notes                             |
| ------ | ----------------------------- | ------------ | --------------------------------- |
| `GET`  | `/internal/v1/siem/status`    | `admin:siem` | Cursor position + exporter health |
| `POST` | `/internal/v1/siem/retry`     | `admin:siem` | Retry dead-letter events          |
| `GET`  | `/internal/v1/siem/exporters` | `admin:siem` | List configured exporters         |
