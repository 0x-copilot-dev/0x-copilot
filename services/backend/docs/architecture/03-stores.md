# Stores — backend

How data is persisted. Each domain has an in-memory and a Postgres implementation;
both implement the same interface.

See also:

- [00-system-map.md](00-system-map.md) — file-to-responsibility map
- [02-contracts.md](02-contracts.md) — Pydantic records these stores persist

---

## Store selection

There is no single `BACKEND_STORE_BACKEND` env var. Instead, store implementations are
wired at startup inside `backend_app/app.py` based on:

- `DATABASE_URL` present → Postgres stores
- No `DATABASE_URL` → In-memory stores (dev/test only)

All stores are injected as FastAPI application state and passed through `service.py`.

---

## CrossTenantWriteError

`store.py` — `CrossTenantWriteError(Exception)`

Raised when an upsert finds a composite-key row belonging to a different `org_id`.
The public message is intentionally generic. Always includes `table` attribute for
internal logging.

---

## MCP stores

`backend_app/store.py`

### `McpServerStore` interface

| Method                                        | What it does                                                                        |
| --------------------------------------------- | ----------------------------------------------------------------------------------- |
| `upsert(record)`                              | Create or update a server record; raises `CrossTenantWriteError` on org_id mismatch |
| `get(server_id, org_id)`                      | Returns `McpServerRecord \| None`                                                   |
| `list(org_id, user_id)`                       | Returns all servers for (org, user)                                                 |
| `delete(server_id, org_id)`                   | Soft or hard delete; org_id guard                                                   |
| `update_auth_state(server_id, org_id, state)` | Atomic `auth_state` transition                                                      |

Implementations:

- `InMemoryMcpStore` — `dict[server_id, McpServerRecord]`; no durability
- `PostgresMcpStore` — asyncpg pool; `SELECT … WHERE org_id=$1` on every read/write

### `McpAuthSessionStore`

Stores in-flight PKCE sessions. TTL-enforced on read (`expires_at < now()` → not found).

| Method                                  | Notes                                                           |
| --------------------------------------- | --------------------------------------------------------------- |
| `create(record)`                        | Insert; raises if `state` collision                             |
| `consume(state)`                        | Atomic consume — marks row used; returns `McpAuthSessionRecord` |
| `get_by_session_id(session_id, org_id)` | Session lookup                                                  |

### `McpTokenStore`

Stores `TokenEnvelope` rows (encrypted OAuth tokens).

| Method                            | Notes                                              |
| --------------------------------- | -------------------------------------------------- |
| `upsert(envelope)`                | Create or replace for (server_id, org_id, user_id) |
| `get(server_id, org_id, user_id)` | Returns `TokenEnvelope \| None`                    |
| `delete(server_id, org_id)`       | Revoke all tokens for a server                     |

---

## Skill store

`backend_app/store.py` — `SkillStore`

| Method                                 | Notes                                            |
| -------------------------------------- | ------------------------------------------------ |
| `upsert(record)`                       | Increments `version` on update                   |
| `get(skill_id, org_id)`                | Returns `SkillRecord \| None`                    |
| `list(org_id, user_id, scope)`         | Lists user or org-scoped skills                  |
| `list_for_ai_backend(org_id, user_id)` | Returns `InternalSkillCard[]` — no markdown      |
| `get_bundle(skill_id, org_id)`         | Returns `InternalSkillBundle` with full markdown |
| `delete(skill_id, org_id)`             | Hard delete                                      |

Implementations:

- `InMemorySkillStore` — in-memory dict
- `PostgresSkillStore` — asyncpg pool

---

## Audit store

`backend_app/store.py` — `AuditStore`

Four separate append-only chains. Each write:

1. Reads the last `(seq, signature)` for `(org_id)`.
2. Computes the new chain link (seq+1, sha256 of prev_hash + payload).
3. Signs with `AuditChainSigner` (from `enterprise_audit_chain` package).
4. Inserts with the chain fields populated.

The chain is monotone and append-only. No UPDATE or DELETE is permitted on audit rows.

| Method                          | Notes                               |
| ------------------------------- | ----------------------------------- |
| `append_mcp_event(record)`      | Appends to the MCP audit chain      |
| `append_skill_event(record)`    | Appends to the skill audit chain    |
| `append_identity_event(record)` | Appends to the identity audit chain |
| `append_deploy_event(record)`   | Appends to the deploy audit chain   |

Implementations:

- `InMemoryAuditStore` — list per chain; chain signer runs in-memory
- `PostgresAuditStore` — asyncpg pool; uses row-level locking to serialize chain writes per org

**Warning:** The in-memory audit store is acceptable only in development. Never use it in
environments where audit is a compliance control.

---

## Identity stores

`backend_app/identity/store.py` — `IdentityStore`

One store class owns all identity-domain tables:

| Method family                                     | Tables             |
| ------------------------------------------------- | ------------------ |
| `get/create/update/delete_org`                    | `organizations`    |
| `get/create/update/delete_user`                   | `users`            |
| `add/remove/list_member`                          | `org_memberships`  |
| `get/create/update/delete_role`                   | `roles`            |
| `assign/revoke_role`                              | `role_assignments` |
| `get/create/update/delete_provider`               | `auth_providers`   |
| `get/list_login_attempt` + `append_login_attempt` | `login_attempts`   |

All queries include `WHERE org_id = $1` guards. The Postgres adapter additionally applies
row-level security (RLS) when `enforce_rls=True` in the deployment profile.

Implementations:

- `InMemoryIdentityStore` — in-memory dicts; simulates all constraints (unique email, active-role invariant)
- `PostgresIdentityStore` — asyncpg pool; CITEXT columns for email case-insensitivity

---

## Session store

`backend_app/identity/session_store.py` — `SessionStore`

| Method                               | Notes                                                                        |
| ------------------------------------ | ---------------------------------------------------------------------------- |
| `create(record)`                     | Insert session row                                                           |
| `touch(session_id, token_hash)`      | Returns `SessionTouchResult`; updates `last_seen_at`; 401 if revoked/expired |
| `revoke(session_id, org_id, reason)` | Sets `revoked_at`                                                            |
| `list(org_id, user_id)`              | Active sessions for a user                                                   |
| `sweep_expired()`                    | Called by the background sweeper lifespan task                               |

The session sweeper (`session_sweeper.py`) runs as a FastAPI lifespan task on a configurable
interval (default 60s).

---

## Other stores

| Store file                            | Domain                                                                                                           |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `identity/password_store.py`          | `LocalCredentialRecord`, `PasswordPolicyRecord`, `PasswordResetTokenRecord`                                      |
| `identity/mfa_store.py`               | `MfaFactorRecord`, `TotpSecretRecord`, `WebAuthnCredentialRecord`, `MfaChallengeRecord`, `MfaRecoveryCodeRecord` |
| `identity/oidc_store.py`              | `OidcAuthenticationRecord`, `OidcIdentityRecord`, `OidcRefreshTokenRecord`, `OidcJwksCacheRecord`                |
| `identity/saml_store.py`              | `SamlAuthenticationRecord`, `SamlIdentityRecord`                                                                 |
| `identity/scim_store.py`              | `ScimTokenRecord`, `ScimExternalIdRecord`, `ScimGroupRecord`, `ScimGroupMemberRecord`                            |
| `identity/lockout_store.py`           | `LockoutPolicyRecord`, `AccountLockoutRecord`                                                                    |
| `identity/me_store.py`                | User profile preferences (avatar ref, display settings)                                                          |
| `identity/invitation_store.py`        | `InvitationRecord`                                                                                               |
| `identity/login_email_first_store.py` | Magic-link state machine records                                                                                 |
| `identity/avatar_store.py`            | User avatar blobs                                                                                                |
| `api_keys/store.py`                   | API key rows: `key_prefix`, `secret_hash`, `scopes`, `kind`, `rotated_from_id`                                   |
| `notifications/store.py`              | Notification preferences + quiet hours                                                                           |
| `policies/store.py`                   | Tool-use policy rows                                                                                             |
| `privacy/store.py`                    | Data residency region + privacy settings                                                                         |

---

## DB pool configuration (C4)

`_BackendPoolEnv` in `store.py` — env-var driven:

| Env var                                   | Default |
| ----------------------------------------- | ------- |
| `BACKEND_DB_POOL_MIN_SIZE`                | 5       |
| `BACKEND_DB_POOL_MAX_SIZE`                | 50      |
| `BACKEND_DB_POOL_ACQUIRE_TIMEOUT_SECONDS` | 5.0     |
| `BACKEND_DB_STATEMENT_TIMEOUT_MS`         | 10000   |
| `BACKEND_DB_LOCK_TIMEOUT_MS`              | 3000    |
| `BACKEND_DB_IDLE_IN_TXN_TIMEOUT_MS`       | 30000   |

---

## Migration runner

`backend_app/db/migrate.py` — yoyo-based; reads numbered SQL files from `migrations/`.
Run at service startup when `BACKEND_AUTO_MIGRATE=true`. Schema constants are imported
from `migrations.py` (generated from the SQL files).
