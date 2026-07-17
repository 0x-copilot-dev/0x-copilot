# Auth and Identity — backend-facade

How the facade verifies bearer tokens, touches sessions, handles API keys,
and enforces step-up MFA.

See also:

- [01-routing.md](01-routing.md) — how identity flows into upstream requests
- [backend docs: features/identity-auth.md](../../../backend/docs/features/identity-auth.md) — session and auth details on the backend side

Source: `backend_facade/auth.py`

---

## Bearer token format

`<base64url_payload>.<base64url_hmac_signature>`

The payload is a base64url-encoded JSON object:

```json
{
  "org_id": "org_abc",
  "user_id": "usr_xyz",
  "roles": ["employee"],
  "permission_scopes": ["runtime:use"],
  "connector_scopes": { "server_id": ["read"] },
  "sid": "sid_123"
}
```

`sid` is the session ID claim (present in all A2+ tokens; absent in legacy back-compat tokens).

---

## `FacadeAuthenticator`

### `authenticate_request(request)` — sync, HMAC-only

1. Reads `Authorization: Bearer <token>` header.
2. Splits on `.` into `payload_part` and `signature_part`.
3. Recomputes `HMAC-SHA256(payload_part, ENTERPRISE_AUTH_SECRET)`.
4. `hmac.compare_digest()` constant-time comparison.
5. Decodes payload JSON → `AuthenticatedIdentity`.
6. If `REQUIRE_SESSION_BINDING=true`, rejects bearers without a `sid` claim.

**Does not touch the session DB.** Used for quick requests where session staleness is acceptable.

### `verify_with_touch(request, backend_url, http_client)` — async, DB-backed

Extends `authenticate_request()` with a backend session touch:

1. Detects `atlas_pk_*` prefix → routes to `_verify_api_key_bearer()` path.
2. Otherwise: runs HMAC verify locally first.
3. Extracts `sid` from the bearer payload.
4. Computes `token_hash = sha256(signature_part)` (never exposes the signature itself).
5. Checks `_TouchCache` for `(token_hash, time_bucket)`.
   - **Cache hit** → return cached `AuthenticatedIdentity`.
   - **Cache miss** → call `POST /internal/v1/auth/sessions/touch` on backend.
6. Backend returns canonical identity (roles, scopes, `mfa_satisfied_at`) or 401 (revoked/expired).
7. On 401: invalidate the token's cache entries, raise `SessionRevoked(401)`.
8. On success: store in `_TouchCache`, return canonical identity.

`cache_bypass=True` skips the cache — used by logout, session-revoke routes, and
any route where instant revocation is required.

---

## `_TouchCache` — LRU session identity cache

| Parameter | Value                                           |
| --------- | ----------------------------------------------- |
| Max size  | 128 entries                                     |
| TTL       | 30 seconds                                      |
| Key       | `(sha256(token_signature), floor(now / 30))`    |
| Lock      | `threading.Lock` — safe for multi-threaded ASGI |

The time-bucket trick (`floor(now / TTL)`) makes cache expiration implicit: as wall clock
crosses the next 30s boundary, the old bucket key is never read again and naturally
falls out of the LRU. No explicit expiry sweep needed.

`invalidate(token_hash)` — drops all bucket entries for a token. Called on logout/revoke
so the immediately-following request forces a fresh touch.

---

## API key path (`atlas_pk_*`)

1. `verify_with_touch()` detects `atlas_pk_` prefix.
2. Computes `sha256(bearer)` → cache key.
3. Cache miss → `POST /internal/v1/auth/api-keys/verify` on backend.
4. Backend verifies argon2id hash (with pepper), stamps `last_used_at`, returns `{org_id, user_id, scopes}`.
5. Mints `AuthenticatedIdentity(roles=("api_key",), permission_scopes=<from row>)`.
6. Stores in touch cache (keyed on `sha256(bearer)`).

API keys cannot obtain roles beyond `("api_key",)`. All scope checks on the upstream
services apply normally.

---

## `AuthenticatedIdentity`

Frozen dataclass — immutable once constructed.

| Field               | Source                                                 |
| ------------------- | ------------------------------------------------------ |
| `org_id`, `user_id` | Bearer payload (after touch: from session row)         |
| `roles`             | Bearer payload (after touch: from session row)         |
| `permission_scopes` | Bearer payload (after touch: from session row)         |
| `connector_scopes`  | Bearer payload (after touch: from session row)         |
| `mfa_satisfied_at`  | `None` (HMAC-only path); from session row (touch path) |
| `session_id`        | `sid` claim (or `None` for legacy/dev-bypass tokens)   |

### Helper methods

`scoped_params(extra)` → `{org_id, user_id, **extra}` — for GET query params.
`scoped_payload(payload, include_request_context)` → overwrites `org_id`/`user_id` in the body.

---

## Step-up MFA (`requires_recent_mfa`)

`auth.py` — `requires_recent_mfa(identity, max_age_seconds, now)`

Used by routes that require a recent MFA verify (e.g., SCIM token mint, password change).
Checks `identity.mfa_satisfied_at`:

- `None` → raises `StepUpRequired(max_age_seconds, elapsed=None)`
- `(now - mfa_satisfied_at) > max_age_seconds` → raises `StepUpRequired(..., elapsed)`

`StepUpRequired` is an `HTTPException(403)` with:

```
WWW-Authenticate: x-step-up max_age="<n>", realm="0x-copilot"
```

The frontend reads this header to prompt for a fresh second factor without logging out.

---

## Service headers injected upstream

```python
{
    "X-Enterprise-Service-Token": ENTERPRISE_SERVICE_TOKEN,
    "x-enterprise-org-id": identity.org_id,
    "x-enterprise-user-id": identity.user_id,
    "x-enterprise-roles": ",".join(identity.roles),
    "x-enterprise-permission-scopes": ",".join(identity.permission_scopes),
    "x-enterprise-connector-scopes": json.dumps(identity.connector_scopes),
    "x-request-id": <current_request_id>,  # from RequestContextMiddleware
}
```

The upstream (`backend` or `ai-backend`) treats all identity headers as trusted
**only because** `X-Enterprise-Service-Token` is valid. Without the service token,
upstream rejects with 401.

---

## Dev auth path

No `DEV_AUTH_BYPASS`. Dev sessions go through a real HMAC bearer minted by
`POST /v1/dev/identity/mint` (proxied from `backend`). The facade verifies it the same
way as production. The only difference: `backend` only registers `/v1/dev/*` when
`BACKEND_ENVIRONMENT=development`, so the mint path is closed in production.

When `FACADE_ENVIRONMENT=development`, the facade registers two unauthenticated proxy routes:

- `GET /v1/dev/personas` → `backend/v1/dev/personas`
- `POST /v1/dev/identity/mint` → `backend/v1/dev/identity/mint`

These proxies are registered via `_dev_idp_enabled()` which checks both `FACADE_ENVIRONMENT`
and `DeploymentProfileLoader.load().toggles.dev_auth_bypass_allowed`.
