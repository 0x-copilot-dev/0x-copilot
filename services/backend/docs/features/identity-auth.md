# Identity and Auth

Sessions, login flows, OIDC, SAML, local passwords, MFA, SCIM, account lockouts,
invitations, and magic-link / workspace-picker.

See also:

- [architecture/01-request-lifecycle.md](../architecture/01-request-lifecycle.md) — RBAC enforcement
- [architecture/02-contracts.md](../architecture/02-contracts.md) — all identity Pydantic records
- [guides/add-auth-provider.md](../guides/add-auth-provider.md) — how to add a new IdP

---

## Sessions (A2)

`backend_app/identity/sessions.py` — `SessionService`

Sessions are server-issued rows keyed on `sha256(bearer_signature)`. The plaintext bearer
is returned exactly once from `SessionMintResult.bearer_token`.

| Operation                            | What happens                                                                                                                |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `create(request)`                    | Generates HMAC-signed bearer (payload.signature format); stores `SessionRecord` with `token_hash = sha256(signature)`       |
| `touch(session_id, token_hash)`      | Validates not revoked/expired; updates `last_seen_at`; returns `SessionTouchResult` with current roles/scopes/mfa_satisfied |
| `revoke(session_id, org_id, reason)` | Sets `revoked_at`; invalidates the facade's touch cache                                                                     |
| `list(org_id, user_id)`              | Active sessions for the user                                                                                                |
| `sweep_expired()`                    | Background task; expires rows where `expires_at < now()`                                                                    |

**Session binding** — When `REQUIRE_SESSION_BINDING=true` (production hardening), bearers
without a `sid` claim are rejected at the facade. All A2+ login flows mint bearers with `sid`.

**Bearer format:** `<base64url_payload>.<base64url_hmac_signature>` signed with `ENTERPRISE_AUTH_SECRET`.

---

## Dev IdP (W0.1)

`backend_app/dev_idp/routes.py` — only registered when `BACKEND_ENVIRONMENT=development`.

| Endpoint                     | What it does                                                                              |
| ---------------------------- | ----------------------------------------------------------------------------------------- |
| `GET /v1/dev/personas`       | Lists dev test accounts (`DevPersona` from `dev_personas.yaml`)                           |
| `POST /v1/dev/identity/mint` | Mints a real signed bearer from a `DevMintRequest`; uses the same HMAC path as production |

Production has no dev bypass. The facade proxies these two endpoints at `/v1/dev/*` so
the browser, curl, and pytest fixtures can use the same surface.

---

## Local password auth (A4)

`backend_app/identity/passwords.py` — `PasswordService`

| Operation                  | Notes                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `login(LocalLoginRequest)` | Argon2 verify; account lockout check; returns `LocalLoginResult` with `requires_mfa` if org has `mfa_required=True` |
| `change_password(request)` | Verifies current; enforces reuse window; hashes new with argon2id                                                   |
| `request_reset(request)`   | Anti-enumeration: always 200; emails reset token if user exists                                                     |
| `confirm_reset(request)`   | Consumes single-use token; sets new password hash                                                                   |
| `bootstrap_admin(request)` | One-time admin creation; refused if any admin exists; requires `BOOTSTRAP_ADMIN_TOKEN`                              |

Hash format: full argon2id encoded string. Previous hashes kept for `PasswordPolicyRecord.reuse_window` (default 5).

---

## Email-first / magic-link / workspace picker (PR 5.1)

`backend_app/identity/login_email_first.py`

The login flow:

1. `AuthDiscoverRequest(email)` → `AuthDiscoverResponse` — resolves domain to org/IdP; returns `kind`:
   - `SSO` → redirect to OIDC/SAML provider
   - `PERSONAL` or `MAGIC_LINK` → send magic link
   - `UNKNOWN` → unknown domain (no SSO enforced)

2. `POST /internal/v1/auth/login-email-first/start` — sends magic link email (anti-enumeration: always 202).
3. `POST /internal/v1/auth/login-email-first/callback` — consumes token:
   - Single org → `SESSION_MINTED` outcome → bearer
   - Multiple orgs → `WORKSPACE_PICK_REQUIRED` → workspace picker list + `pick_token`
4. `POST /internal/v1/auth/login-email-first/select` — consumes `pick_token` + chosen `org_id` → mints session.

`MagicLinkTokenRecord.candidate_orgs` is materialized at request time for the picker.

---

## OIDC SSO (A3)

`backend_app/identity/oidc.py` — `OidcService`

| Operation                         | Notes                                                                                                                |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `authorize(OidcAuthorizeRequest)` | PKCE flow: generates `state`, `nonce`, `code_verifier`; stores `OidcAuthenticationRecord`; returns `auth_url`        |
| `callback(OidcCallbackRequest)`   | Validates `state` (CSRF); exchanges code; validates ID token (JWKS); JIT-provisions user if not found; mints session |
| `refresh(provider_id, user_id)`   | Refreshes the encrypted `OidcRefreshTokenRecord`                                                                     |

JWKS is fetched via `identity/jwks.py` and cached in `OidcJwksCacheRecord` (TTL-bounded).

JIT provisioning — on first login, creates `UserRecord` + `OrganizationMemberRecord` + default role assignment.
`attributes_snapshot` in `OidcIdentityRecord` stores the last-seen claims for audit.

---

## SAML 2.0 SSO (A5)

`backend_app/identity/saml.py` — `SamlService`

| Operation                         | Notes                                                                                                                |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `authorize(SamlAuthorizeRequest)` | SP-initiated: creates SAML authn request XML; stores `SamlAuthenticationRecord` with `status=pending`                |
| `consume(SamlConsumeRequest)`     | ACS endpoint: validates signature + replay defense; updates record to `consumed`; JIT-provisions user; mints session |

Replay defense: `assertion_id` is stored; duplicate `assertion_id` within TTL → rejected.
SP-initiated vs IdP-initiated: IdP-initiated flows have `request_id=NULL` in the record.
The pluggable verifier is `identity/_saml_lib.py`.

### Provider config (stored in `auth_providers.config` JSONB)

```json
{
  "idp_entity_id": "<str>",
  "idp_sso_url": "<str>",
  "idp_x509_cert": "<str — PEM, headers optional>",
  "sp_entity_id": "<str>",
  "sp_acs_url": "<str>",
  "attribute_map": { "email": "...", "display_name": "...", "groups": "..." },
  "allow_idp_initiated": false,
  "auto_provision_user": false,
  "group_role_map": { "<group_name>": "<role_name>" },
  "sp_signing_key_ref": null,
  "sp_decryption_key_ref": null
}
```

`sp_signing_key_ref` / `sp_decryption_key_ref` are declared but not yet consumed — present so a follow-up adding assertion encryption does not need a schema migration.

### Trust model

**Inside the verifier** (production: `OneLoginSamlVerifier` backed by `python3-saml`):

- `Signature` — checked against `idp_x509_cert`.
- `NotBefore` / `NotOnOrAfter` — 60s clock skew tolerance.
- `AudienceRestriction` — must match `sp_entity_id`.
- `InResponseTo` — for SP-initiated flows, must match a pending `request_id`.

**After parsing** (by `SamlService`):

- `assertion_id` UNIQUE — replay defense; duplicate → `SamlReplayDetected`.
- Org binding — assertion is looked up by `(provider_id, name_id)` together, so an assertion from org A's IdP cannot link to an org B user.

### Pluggable verifier

`identity/_saml_lib.py` defines a `SamlVerifier` Protocol with:

- `build_authn_request`, `build_metadata`, `parse_response`.

Production uses `OneLoginSamlVerifier` (wraps `python3-saml`; requires `xmlsec1` system package).
Tests use `FakeSamlVerifier` — returns a pre-configured `ParsedSamlAssertion` and can raise `SamlSignatureError` / `SamlAssertionExpired` / `SamlAudienceMismatch` without requiring a real XML round-trip.

---

## MFA (A6)

`backend_app/identity/mfa.py` — `MfaService`

### TOTP

| Operation                  | Notes                                                                                                                                                   |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enroll_totp(request)`     | Generates TOTP seed; encrypts with TokenVault; creates `MfaFactorRecord(enabled=False)`; returns `TotpEnrollResult` with `otpauth_url` + recovery codes |
| `confirm_totp(request)`    | Verifies the first TOTP code; sets `enabled=True`                                                                                                       |
| `verify(MfaVerifyRequest)` | Validates TOTP code; checks `last_step` replay guard; sets `mfa_satisfied_at` on session                                                                |

### WebAuthn

| Operation           | Notes                                                       |
| ------------------- | ----------------------------------------------------------- |
| `register_start`    | Issues `PublicKeyCredentialCreationOptions`                 |
| `register_finish`   | Validates attestation; stores `WebAuthnCredentialRecord`    |
| `verify(assertion)` | Validates signature + `sign_count`; sets `mfa_satisfied_at` |

Recovery codes: 10 one-shot sha256-hashed codes generated at TOTP enrollment. Never re-shown.

**MFA pending sessions** — if `mfa_required=True` and user hasn't completed MFA, the session is minted with `permission_scopes=("mfa:pending",)`. This scope only grants access to MFA challenge/verify routes. All other routes return 403.

---

## Account lockouts (A8)

`backend_app/identity/lockout.py` — `LockoutService`

| Operation                              | Notes                                                                                                                                                           |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `check_and_increment(org_id, user_id)` | Reads `LockoutPolicyRecord`; if `enforce_lockout=True` and `failure_count >= max_failures` within window → creates `AccountLockoutRecord`; raises lockout error |
| `unlock(org_id, user_id, reason)`      | Sets `unlocked_at`; records `unlocked_by_user_id`                                                                                                               |
| `auto_unlock_sweep()`                  | Unlocks rows where `auto_unlock_at < now()`                                                                                                                     |

`permanent_after_n_lockouts=0` means temporary lockouts only. Setting it to N causes
the account to be permanently locked after N auto-lock events.

---

## SCIM 2.0 (A7)

`backend_app/identity/scim.py` — `ScimService`

| Operation         | Notes                                                                                           |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| User CRUD         | Create/update/delete via SCIM User schema; maps to `UserRecord` + `OrganizationMemberRecord`    |
| Group CRUD        | Create/update/delete via SCIM Group schema; maps to `ScimGroupRecord` + `ScimGroupMemberRecord` |
| Group → role sync | `ScimGroupRecord.mapped_role_id` → `RoleAssignmentRecord` updated on group member add/remove    |
| Token management  | `mint_token` / `revoke_token` / `rotate_token` on `ScimTokenRecord`                             |

Auth: `ScimTokenRecord.token_hash` (sha256); never plaintext at rest. `token_prefix` (first 8 chars) for listing.

Filter parsing: `identity/scim_filter.py` — hand-rolled SCIM filter parser (supports `eq`, `ne`, `co`, `and`, `or`).
Serialization: `identity/scim_serializer.py` — SCIM 2.0 wire format.

### Schema tables

```sql
scim_tokens
  token_id PK, org_id, provider_id REFERENCES auth_providers,
  token_hash UNIQUE (sha256), token_prefix (first 8 chars),
  created_by_user_id, created_at, expires_at, revoked_at, last_used_at

scim_external_ids
  mapping_id PK, org_id, user_id (NULLABLE), group_id (NULLABLE),
  provider_id, external_id, created_at
  CHECK ((user_id IS NOT NULL) <> (group_id IS NOT NULL))
  UNIQUE (provider_id, external_id)

scim_groups
  group_id PK, org_id, display_name, external_id, mapped_role_id,
  created_at, updated_at, deleted_at
  UNIQUE (org_id, display_name) WHERE deleted_at IS NULL

scim_group_members
  membership_id PK, org_id, group_id, user_id,
  added_at, removed_at
  UNIQUE (group_id, user_id) WHERE removed_at IS NULL
```

### Token shape

`secrets.token_urlsafe(32)` plaintext returned exactly once at mint. Server stores `sha256(plaintext)` as `token_hash` and the first 8 chars as `token_prefix`. Mint does not auto-revoke the previous token — zero-downtime rotation requires both old and new to be valid simultaneously.

### Trust model

- Bearer validated by `sha256(presented) == scim_tokens.token_hash`. Revoked or expired → 401.
- All queries scope by `org_id` resolved from the token row. Token from org A cannot read org B users regardless of filter.
- `scim_required` policy (`identity_policies.scim_required`): when on, local password login returns 404 and OIDC JIT provisioning is rejected — IdP must provision via SCIM only.

### Soft-delete semantics

`PATCH .../Users/{id}` with `{op: replace, path: active, value: false}` sets `users.deleted_at`.
`value: true` reactivates by clearing `deleted_at`. `DELETE` is accepted but rare — most IdPs prefer the active-flip.

### Group → role sync

When `scim_groups.mapped_role_id` is set:

- Member added → role assignment created.
- Member removed → role assignment revoked (`revoked_at` set).
- Group deleted → role revoked for all members.

`mapped_role_id = NULL` means the group tracks membership but creates no role assignments.

### Endpoints

Facade public — mounted at `/scim/v2/*` (NOT under `/v1`):

| Method                 | Path                                                                           |
| ---------------------- | ------------------------------------------------------------------------------ |
| `GET/POST`             | `/scim/v2/Users`                                                               |
| `GET/PUT/PATCH/DELETE` | `/scim/v2/Users/{id}`                                                          |
| `GET/POST`             | `/scim/v2/Groups`                                                              |
| `GET/PUT/PATCH/DELETE` | `/scim/v2/Groups/{id}`                                                         |
| `GET`                  | `/scim/v2/ServiceProviderConfig`, `/scim/v2/Schemas`, `/scim/v2/ResourceTypes` |

Backend internal token management — requires `admin:idp` scope:

| Method   | Path                                                                          |
| -------- | ----------------------------------------------------------------------------- |
| `POST`   | `/internal/v1/auth/scim/{provider_id}/tokens` — mint (returns plaintext once) |
| `GET`    | `/internal/v1/auth/scim/{provider_id}/tokens` — list (prefix only)            |
| `DELETE` | `/internal/v1/auth/scim/{provider_id}/tokens/{token_id}` — revoke             |

---

## Invitations (PR 4.2)

`backend_app/identity/invitations.py` — `InvitationsService`

1. Admin calls `POST /internal/v1/auth/invitations` → `InvitationMintResult` (plaintext token returned once).
2. Invite email sent with a link containing the plaintext token.
3. Recipient calls `POST /internal/v1/auth/invitations/accept` with the token → validates not expired/revoked/consumed; creates user + member row; mints session.
4. Admin can revoke via `POST /internal/v1/auth/invitations/{invite_id}/revoke`.

The partial unique index in the DB allows re-inviting after revoke or accept.

---

## RBAC (A10)

`backend_app/identity/rbac.py` — `RequireScopes`, `RequireRoles`, `public_route`

### Scope catalog

All scopes are constants in `packages/service-contracts/src/enterprise_service_contracts/scopes.py`. Both backend and ai-backend import this module — a typo fails at import time, not in production.

| Scope                | Purpose                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| `runtime:use`        | Allows calling ai-backend runtime routes                                 |
| `mcp:read`           | List MCP servers                                                         |
| `mcp:write`          | Create/update/delete MCP servers                                         |
| `connectors:auth`    | Start MCP OAuth flows                                                    |
| `skills:read`        | List/fetch skills                                                        |
| `skills:write`       | Create/update/delete skills                                              |
| `admin:users`        | Manage members, invitations, lockouts                                    |
| `admin:idp`          | Manage IdP providers, SCIM tokens                                        |
| `admin:audit_export` | Export audit logs                                                        |
| `admin:budgets`      | Manage workspace budgets                                                 |
| `admin:retention`    | Manage retention policies                                                |
| `admin:siem`         | Configure SIEM export                                                    |
| `audit:read`         | Read (but not export) audit events                                       |
| `mfa:pending`        | Lifecycle marker: session minted before MFA completed — not a permission |

### RBAC_MODE

`RBAC_MODE=audit` (default) — log denials to the identity audit chain and allow through.
`RBAC_MODE=enforce` — log denials AND return 403. Bank/government profiles flip to `enforce` at deployment.
Misconfigured value → silently falls back to `audit`; misconfig recorded in the audit row metadata.

### `mfa:pending` semantics

A session minted before MFA verify carries `permission_scopes=("mfa:pending",)`. Any scope check against this session fails **except** routes marked `public_route()`. This keeps the session capable of the MFA verify dance (`/challenge`, `/verify`, `/recovery/consume`) and nothing else.

### Usage pattern

```python
from enterprise_service_contracts.scopes import MCP_WRITE
from backend_app.identity.rbac import RequireScopes

@app.post("/v1/mcp/servers", dependencies=[Depends(RequireScopes(MCP_WRITE))])
def create_server(...): ...
```

### Per-route scope mapping (backend)

| Route                                                                       | Scope                                    |
| --------------------------------------------------------------------------- | ---------------------------------------- |
| `/v1/health`, `/healthz`, `/readyz`                                         | public                                   |
| `POST/GET/PATCH/DELETE /v1/mcp/servers`                                     | `mcp:write` / `mcp:read`                 |
| `POST /v1/mcp/servers/{id}/auth/*`                                          | `connectors:auth`                        |
| `GET /v1/mcp/oauth/callback`                                                | public (OAuth state is the trust anchor) |
| `GET /internal/v1/mcp/cards`, `client-session`, `rpc`                       | `runtime:use`                            |
| `POST /internal/v1/mcp/.../test-token`                                      | `mcp:write`                              |
| `POST/GET/PUT/DELETE /v1/skills/*`                                          | `skills:write` / `skills:read`           |
| `GET /internal/v1/skills/*`                                                 | `runtime:use`                            |
| `POST /internal/v1/audit/export`, `audit/deploy`                            | `admin:audit_export`                     |
| `POST /internal/v1/auth/lockouts/{id}/unlock`                               | `admin:users`                            |
| `GET /internal/v1/auth/lockouts*`, `login-attempts`                         | `admin:users`                            |
| `GET /internal/v1/auth/me/login-attempts`                                   | `runtime:use` (self-service)             |
| `POST /internal/v1/auth/mfa/factors/*`                                      | `runtime:use`                            |
| `POST /internal/v1/auth/mfa/{challenge,verify,recovery/consume}`            | public (mfa-pending tolerant)            |
| `POST /internal/v1/auth/oidc/*`                                             | public (SSO entry/exit)                  |
| `POST /internal/v1/auth/saml/*`                                             | public (SSO entry/exit)                  |
| `POST /internal/v1/auth/local/{verify,bootstrap-admin}`, `password/reset/*` | public (no session yet)                  |
| `POST /internal/v1/auth/password/change`                                    | `runtime:use`                            |
| `POST /internal/v1/auth/sessions{,/touch,/dev-mint}`                        | public (no session yet)                  |
| `POST /internal/v1/auth/sessions/{id}/revoke`, `GET /sessions`              | `runtime:use` (self-service)             |
| `POST/GET/DELETE /internal/v1/auth/scim/{id}/tokens*`                       | `admin:idp`                              |
| `*/internal/v1/auth/scim/resource/*`                                        | public (SCIM bearer is the trust anchor) |

### CI scope-coverage check

`tools/check_route_scopes.py` boots each service's FastAPI app, walks every route, and asserts it declares an RBAC policy. The check runs in `ci-backend.yml` and `ci-ai-backend.yml`. A route added without an annotation fails CI.

The detector recognises four sanctioned factories: `RequireScopes(...)`, `RequireRoles(...)`, `RequireAnyScope(...)` (ai-backend only — OR semantics), and `public_route()`.
