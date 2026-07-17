# PR 06 — A2: Server-issued Sessions and Bearer-Token Binding

**Spec ID:** A2 | **Track:** Identity & Access | **Wave:** 2 (Auth Foundation) | **Estimated effort:** L
**Depends on:** A1 (users/orgs schema), C2 (migrations)
**Required for:** A3 (OIDC), A4 (local password), A5 (SAML), A6 (MFA), A9 (frontend)

---

## 1. Functional Specification

### 1.1 Goal

Today the bearer token is HMAC-signed and trusted _as-is_ — there is no server-side record that the token corresponds to an _active_ session, no "log out everywhere", no per-device tracking, no instant revocation. We need server-issued sessions backed by a `sessions` table, while keeping the same compact `payload.signature` wire shape for back-compat.

### 1.2 User-visible behavior

- **End user:** can list their active sessions; can revoke a specific session ("log out other devices"); logout is instant.
- **Operator:** can hard-revoke a user's sessions for incident response.
- **Frontend:** `GET /v1/auth/session` works as before (compat).

### 1.3 Out of scope

- How a session gets created via login (A3 OIDC, A4 local password, A5 SAML).
- MFA gating (A6).
- Per-device push notifications.
- Anything that minted the existing externally-issued tokens stops working _after_ `REQUIRE_SESSION_BINDING=true` is flipped on (a later operational step, not in this PR).

---

## 2. Technical Specification

### 2.1 Architecture

- **Same wire shape** for the bearer token: existing `base64(json_payload).base64(hmac_sha256(payload))`. Adds a `sid` claim that names the server-side session.
- **Per-request flow:** facade verifies the HMAC signature locally → calls backend `POST /internal/v1/auth/sessions/touch` → backend validates `sid` is active → returns refreshed identity. Result cached _for the request scope only_ (no shared state).
- **Bootstrap (this PR only):** new `POST /internal/v1/auth/sessions/dev-mint` endpoint that takes `{org_id, user_id, roles, scopes, ttl_seconds}` and returns a fresh bearer + session_id. Used to replace the "external token minter" assumption until A3/A4/A5 ship.
- **Token in DB:** SHA-256 hash of the bearer's `signature` (treat the signature as the secret since the payload is public). Plaintext bearer never stored.

### 2.2 Schema changes

Migration `services/backend/migrations/0005_sessions.sql`:

```sql
CREATE TABLE sessions (
    session_id          TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL REFERENCES users(user_id),
    token_hash          TEXT NOT NULL,                 -- sha256 of bearer signature, never plaintext
    roles               JSONB NOT NULL DEFAULT '[]'::jsonb,
    permission_scopes   JSONB NOT NULL DEFAULT '[]'::jsonb,
    connector_scopes    JSONB NOT NULL DEFAULT '{}'::jsonb,
    auth_provider_id    TEXT,                          -- which IdP (NULL for dev-mint)
    mfa_satisfied_at    TIMESTAMPTZ,                   -- NULL until A6 verifies MFA
    client_ip           TEXT,
    user_agent          TEXT,
    device_label        TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revocation_reason   TEXT
);
CREATE UNIQUE INDEX idx_sessions_token_active
    ON sessions (token_hash) WHERE revoked_at IS NULL;
CREATE INDEX idx_sessions_user
    ON sessions (org_id, user_id, revoked_at, expires_at);
CREATE INDEX idx_sessions_expiring
    ON sessions (expires_at) WHERE revoked_at IS NULL;
```

### 2.3 Endpoints

**Backend internal (`/internal/v1/auth/sessions/*`):**

- `POST /internal/v1/auth/sessions` — body: `{org_id, user_id, roles, permission_scopes, connector_scopes, auth_provider_id, ttl_seconds, client_ip, user_agent, device_label}` → `{session_id, bearer_token, expires_at}`. Mints a session and signs a bearer with `sid` claim.
- `POST /internal/v1/auth/sessions/touch` — body: `{bearer_token}` → `{session_id, identity, roles, permission_scopes, connector_scopes, mfa_satisfied}`; updates `last_seen_at`. Returns 401 if revoked or expired.
- `POST /internal/v1/auth/sessions/{session_id}/revoke` — idempotent; sets `revoked_at` and `revocation_reason`.
- `GET /internal/v1/auth/sessions?user_id=&org_id=` — list active sessions for a user.
- `POST /internal/v1/auth/sessions/dev-mint` — gated; bootstrap only. Allowed when `dev_auth_bypass_allowed=true` from C1 toggles.

**Facade public (`/v1/auth/*`):**

- `GET /v1/auth/session` — returns identity for the current bearer (replaces existing `/v1/session`; keep `/v1/session` as alias for one release per [services/backend-facade/src/backend_facade/app.py:48](../../services/backend-facade/src/backend_facade/app.py#L48) and [apps/frontend/src/api/sessionApi.ts](../../apps/frontend/src/api/sessionApi.ts)).
- `GET /v1/auth/sessions` — list mine.
- `DELETE /v1/auth/sessions/{session_id}` — revoke mine.
- `POST /v1/auth/logout` — revokes the current session.

### 2.4 Code changes

**New backend modules:**

- `services/backend/src/backend_app/identity/sessions.py` — `SessionService` with `create`, `touch`, `revoke`, `list_active`, `dev_mint`.
- `services/backend/src/backend_app/identity/session_store.py` — `PostgresSessionStore`, `InMemorySessionStore`.
- Routes appended to [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py).

**Facade changes:**

- New `services/backend-facade/src/backend_facade/auth_routes.py` — public `/v1/auth/*` surface.
- Update [services/backend-facade/src/backend_facade/auth.py](../../services/backend-facade/src/backend_facade/auth.py) `_identity_from_payload` (~L185) to accept `sid` claim and call backend touch. Behind `REQUIRE_SESSION_BINDING` env flag — when false, accept tokens without `sid` (back-compat); when true, reject.
- Per-request cache: a small `LRU(maxsize=128)` keyed on `(token_hash, current_minute_bucket)` — capped TTL 30s. Strictly per-process; no shared cache.
- Wire into [services/backend-facade/src/backend_facade/app.py:44](../../services/backend-facade/src/backend_facade/app.py#L44) `create_app`.

**Service-contracts addition:**

- `packages/service-contracts/src/copilot_service_contracts/auth_claims.py` — claim names `SID_CLAIM = "sid"`, `EXP_CLAIM = "exp"`, etc.

### 2.5 Trust model & failure semantics

- HMAC verify happens at facade _first_ (cheap reject on forged tokens before hitting backend).
- After HMAC ok, **every protected request** does a backend touch. Cached for 30s.
- Backend touch is the canonical revocation gate. Setting `sessions.revoked_at` makes the next non-cached request 401.
- Stale cache during the 30s window: acceptable trade-off for revocation latency vs backend QPS. Sensitive endpoints (e.g. `admin:*`, A10 RBAC) MUST set `cache_bypass=True` so revocation is immediate.
- Token hash storage prevents leaked DB dumps from being usable; payload alone is not sufficient because the HMAC `signature` is the secret-bearing half.

### 2.6 Tenant isolation

- Sessions have `org_id`; list/revoke endpoints filter by it.
- A user in org_a cannot revoke a session in org_b even if they know the session_id.
- Cross-tenant negative test required.

### 2.7 Observability

- New audit actions in `identity_audit_events`: `session.created`, `session.revoked`, `session.expired_swept`, `session.dev_minted`.
- Metrics: `sessions_active{org_id_hash}`, `sessions_created_total`, `sessions_revoked_total{reason}`, `session_touch_cache_hit_ratio`.
- Background sweeper (small, lives inside backend) hard-deletes sessions older than `expires_at + retention_days` (default 30d) and emits a single audit row per sweep.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `dev-mint` creates a session and returns a bearer that subsequent requests accept.
- [ ] Revoking a session causes the next non-cached request to 401 within 30s (cached) or immediately (cache-bypass routes).
- [ ] `GET /v1/auth/sessions` lists only the calling user's sessions.
- [ ] `DELETE /v1/auth/sessions/{id}` rejects cross-user/cross-tenant attempts.
- [ ] Existing externally-issued bearer tokens (no `sid` claim) still work when `REQUIRE_SESSION_BINDING=false`; rejected when `=true`.
- [ ] Token hash never appears in logs.

### 3.2 Test plan

**Unit:**

- `test_token_hash_storage_only` — assert plaintext bearer absent from store.
- `test_revoke_then_touch_returns_401`
- `test_expired_session_returns_401`
- `test_concurrent_session_limit` — configurable, default 10; oldest auto-revoked.
- `test_back_compat_no_sid_claim` — `REQUIRE_SESSION_BINDING=false` accepts; `=true` rejects.

**Integration:**

- End-to-end facade → backend touch happens on every protected route.
- "Log out other devices" actually invalidates other sessions.
- `cache_bypass=True` makes revocation immediate.

**Tenant-isolation:**

- User in org_a calling `DELETE /v1/auth/sessions/<org_b_session>` → 404 (not 403, to avoid leaking session existence).

### 3.3 Compliance evidence produced

- "Session revocation" — instant via DB.
- "Audit of every login/logout" — `session.created` / `session.revoked` rows.
- "Configurable session lifetime" — env-driven default; per-org override available via `identity_policy` (consumed in A3/A4/A5).

### 3.4 Rollout plan

1. PR lands with `REQUIRE_SESSION_BINDING=false` everywhere. Existing externally-minted tokens still work.
2. After A3 (OIDC) or A4 (local password) ships and clients can mint via the new path, flip `REQUIRE_SESSION_BINDING=true` per environment.
3. Old token minter decommissioned.

### 3.5 Backout plan

Set `REQUIRE_SESSION_BINDING=false`. App returns to accepting any HMAC-valid token regardless of `sid`.

### 3.6 Definition of done

- [ ] Migration 0005 applied.
- [ ] All endpoints implemented + tested.
- [ ] Facade per-request touch + cache wired.
- [ ] dev-mint endpoint replaces the external-minter assumption in dev.
- [ ] `apps/frontend/src/api/sessionApi.ts` continues to work without changes (backed by `/v1/auth/session` alias).
- [ ] Background sweeper running.

---

## 4. Critical files

- New: `services/backend/migrations/0005_sessions.sql` (+ rollback)
- New: `services/backend/src/backend_app/identity/sessions.py`
- New: `services/backend/src/backend_app/identity/session_store.py`
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — register routes.
- Modify: [services/backend-facade/src/backend_facade/auth.py:98-116](../../services/backend-facade/src/backend_facade/auth.py#L98-L116) — `sid` claim handling.
- Modify: [services/backend-facade/src/backend_facade/auth.py:185-187](../../services/backend-facade/src/backend_facade/auth.py#L185-L187) — `_identity_from_payload`.
- Modify: [services/backend-facade/src/backend_facade/app.py:44](../../services/backend-facade/src/backend_facade/app.py#L44), [services/backend-facade/src/backend_facade/app.py:48](../../services/backend-facade/src/backend_facade/app.py#L48)
- New: `services/backend-facade/src/backend_facade/auth_routes.py`
- New: `packages/service-contracts/src/copilot_service_contracts/auth_claims.py`
- New: `services/backend/src/backend_app/identity/session_sweeper.py`
