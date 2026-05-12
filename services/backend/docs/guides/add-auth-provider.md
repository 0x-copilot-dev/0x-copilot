# Guide — Add a New Auth Provider

How to add a new authentication provider (OIDC, SAML, or a new protocol).

See also:

- [features/identity-auth.md](../features/identity-auth.md) — existing provider implementations
- [architecture/00-system-map.md](../architecture/00-system-map.md) — identity module layout

---

## For OIDC providers (new IdP tenant, same protocol)

If the new provider is an OIDC IdP (Azure AD, Google Workspace, Okta, etc.), no new code
is required — the protocol is already implemented. Configure via the admin API:

1. `POST /internal/v1/auth/workspace/providers` with `kind=oidc` + `config` (discovery URL,
   `client_id`, `client_secret`).
2. The provider is stored as an `AuthProviderRecord`.
3. `POST /internal/v1/auth/workspace/providers/{id}/domains` to claim a domain for SSO enforcement.
4. Test the flow with `GET /internal/v1/auth/oidc/authorize?provider_id=...`.

---

## For a new OIDC variant (custom claims, non-standard discovery)

1. Read `backend_app/identity/oidc.py` — understand `OidcService.authorize` and `callback`.
2. If the IdP doesn't support standard OIDC discovery (`/.well-known/openid-configuration`),
   override the discovery URL in `AuthProviderRecord.config["discovery_url"]`.
3. If claims mapping is non-standard, add a claim extractor in `OidcService._extract_user_info()`.
4. Write tests in `tests/unit/identity/test_oidc.py`.
5. Update `features/identity-auth.md` with the variant.

---

## For a new SAML provider

Same as OIDC — the protocol implementation exists. Configure:

1. `POST /internal/v1/auth/workspace/providers` with `kind=saml` + `config` (metadata URL or
   XML, `sso_url`, `certificate`).
2. Download the SP metadata from `GET /internal/v1/auth/saml/metadata/{provider_id}` and
   upload it to the IdP.
3. Test with `GET /internal/v1/auth/saml/authorize?provider_id=...`.

---

## For a new auth protocol (new `AuthProviderKind`)

This requires implementing a new service. Follow this checklist:

### 1. Add the `AuthProviderKind` enum value

`backend_app/contracts.py` — add to `AuthProviderKind(StrEnum)`.

### 2. Create the service module

`backend_app/identity/<protocol>.py` — implement:

```python
class <Protocol>Service:
    async def authorize(self, request: <Protocol>AuthorizeRequest) -> <Protocol>AuthorizeResult:
        ...
    async def callback(self, request: <Protocol>CallbackRequest) -> <Protocol>CallbackResult:
        ...
```

The callback must:

- Validate CSRF/state tokens.
- Validate the identity claim (signature, certificate, or token).
- Resolve or JIT-provision the user via `IdentityStore`.
- Create a session via `SessionService`.
- Return a `SessionMintResult`-equivalent with `requires_mfa`.

### 3. Create the store module (if needed)

`backend_app/identity/<protocol>_store.py` — implement an in-memory + Postgres variant.
Add the appropriate `CREATE TABLE` in a numbered migration under `migrations/`.

### 4. Create the record types

`backend_app/contracts.py` — add Pydantic record + HTTP shapes following existing patterns.
All secrets must be encrypted at rest (use `TokenVault` or sha256 hash for tokens).

### 5. Register routes

`backend_app/routes/<protocol>.py` — add a FastAPI router.
Register in `backend_app/app.py`.
Declare scope requirements with `RequireScopes(...)` or `public_route()`.

### 6. Update the facade

The facade must proxy the new provider's routes. Add entries in
`backend_facade/auth_routes.py` → `register_auth_routes()`.

### 7. Write tests

- Unit tests for the service in `tests/unit/identity/test_<protocol>.py`
- Integration test for the full auth flow

### 8. Update docs

- Add a section to `features/identity-auth.md`
- Add route entries to `reference/internal-api.md`
- Add any new env vars to `reference/env-vars.md`

---

## Invariants to preserve

- Sessions are always created by `SessionService.create()` — never construct a `SessionRecord` directly.
- The plaintext bearer is returned exactly once from `SessionMintResult.bearer_token`.
- All secrets (refresh tokens, client secrets) must go through `TokenVault` or sha256-hash before storage.
- Login attempts must be recorded via `IdentityStore.append_login_attempt()` for lockout and audit.
- `requires_mfa` in the callback result must be `True` when `IdentityPolicyRecord.mfa_required=True` and the user hasn't satisfied MFA.
