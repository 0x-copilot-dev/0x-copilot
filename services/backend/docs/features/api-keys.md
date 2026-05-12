# API Keys (B3)

How API keys are minted, verified, and scoped.

See also:

- [architecture/00-system-map.md](../architecture/00-system-map.md) — module location
- [architecture/01-request-lifecycle.md](../architecture/01-request-lifecycle.md) — caller types

---

## What it does

API keys allow programmatic access to the product API without a session bearer. They are
long-lived credentials issued per user with an explicit scope list. The facade verifies
them via a round-trip to the backend's `api-keys/verify` internal endpoint.

---

## Key files

| File                            | Role                                                           |
| ------------------------------- | -------------------------------------------------------------- |
| `backend_app/api_keys/store.py` | `ApiKeyStore` — CRUD on key rows                               |
| `backend_app/api_keys/auth.py`  | Constant-time verify: prefix lookup + argon2id hash comparison |

---

## Key format

`atlas_pk_<random_suffix>` — prefix `atlas_pk_` makes keys identifiable in logs without
revealing the secret. The prefix (first 8 chars after `atlas_pk_`) is stored as `key_prefix`
for listing. The full key is hashed with argon2id (with a pepper from `API_KEY_PEPPER` env var)
and stored as `secret_hash`.

The plaintext key is returned exactly once at mint time.

---

## Key record fields

| Field                                      | Type               | Notes                                     |
| ------------------------------------------ | ------------------ | ----------------------------------------- |
| `key_id`                                   | `str`              | UUID                                      |
| `org_id`, `user_id`                        | `str`              | Owner scope                               |
| `key_prefix`                               | `str`              | First 8 chars; for listing/identification |
| `secret_hash`                              | `str`              | argon2id hash of full key                 |
| `scopes`                                   | `tuple[str, ...]`  | Permission scopes granted to the key      |
| `kind`                                     | `str`              | `personal` or `workspace`                 |
| `rotated_from_id`                          | `str \| None`      | Parent key ID when rotated                |
| `created_at`, `last_used_at`, `revoked_at` | `datetime \| None` | Lifecycle timestamps                      |

---

## Operations

### Mint (`POST /v1/api-keys`)

1. Generates `atlas_pk_<secure_random>`.
2. Stores `key_prefix` + argon2id hash of the full key.
3. Returns the plaintext key (shown once only).

### Verify (internal, `POST /internal/v1/auth/api-keys/verify`)

Called by the facade's `_verify_api_key_bearer()` path:

1. Extracts `key_prefix` from the bearer.
2. Looks up the row by prefix.
3. Constant-time argon2id hash comparison.
4. Updates `last_used_at`.
5. Returns `{org_id, user_id, scopes}` to the facade.

### List (`GET /v1/api-keys`)

Returns key summaries (prefix, scopes, kind, last_used_at). Never returns the hash or plaintext.

### Revoke (`DELETE /v1/api-keys/{key_id}`)

Sets `revoked_at`. Revoked keys fail verification immediately. The facade's touch cache
is keyed on `sha256(bearer)` so a revoked key's cache entry expires naturally within the
30s TTL — sensitive routes should pass `cache_bypass=True`.

### Rotate (`POST /v1/api-keys/{key_id}/rotate`)

Creates a new key row with `rotated_from_id = key_id`, then revokes the original. Returns
the new plaintext key. Both old and new are active briefly during the rotation window.

---

## Auth flow at the facade

1. Facade receives `Authorization: Bearer atlas_pk_*`.
2. `FacadeAuthenticator.verify_with_touch()` detects `atlas_pk_` prefix.
3. Calls `POST /internal/v1/auth/api-keys/verify` (cached with `sha256(bearer)` key, 30s TTL).
4. Mints `AuthenticatedIdentity(roles=("api_key",), permission_scopes=<from row>)`.
5. Injects service headers for upstream forwarding.

---

## Security invariants

- `key_prefix` is stored in plaintext (needed for lookup without full-scan).
- `secret_hash` uses argon2id with a per-deployment pepper to resist offline brute force.
- Keys with `kind=workspace` can be administered by org admins, not just the key owner.
- Scope of a key can never exceed the scope of the minting user's role.
