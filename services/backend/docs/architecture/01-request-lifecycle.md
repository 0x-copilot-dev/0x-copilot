# Request Lifecycle — backend

How a request travels from the caller to the handler, including auth verification,
scope checking, and the difference between public and internal routes.

See also:

- [00-system-map.md](00-system-map.md) — module responsibilities
- [features/identity-auth.md](../features/identity-auth.md) — session and auth detail
- [reference/internal-api.md](../reference/internal-api.md) — full route list

---

## Caller types

| Caller                                         | How they authenticate                                                                                        | What they can reach                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| `backend-facade` (on behalf of a browser user) | Injects `X-Enterprise-Service-Token` + `x-enterprise-org-id` + `x-enterprise-user-id` + roles/scopes headers | `/internal/v1/*`                                                        |
| `ai-backend` worker                            | Same service-token + identity headers                                                                        | `/internal/v1/mcp/*`, `/internal/v1/skills/*`, `/internal/v1/runtime/*` |
| Browser (dev only)                             | Bearer token verified by facade; never hits `:8100` directly                                                 | Never directly                                                          |
| SCIM provisioner (IdP)                         | SCIM bearer token, verified by `identity/scim.py`                                                            | `/internal/v1/auth/scim/resource/*`                                     |

**Invariant:** Caller-supplied `org_id`, `user_id`, roles, and scopes are treated as untrusted
until verified from the `X-Enterprise-Service-Token` header. The backend does not re-verify
the user's bearer — that is the facade's responsibility.

---

## Auth verification path

`backend_app/auth.py` — `BackendServiceAuthenticator`

1. `internal_scoped_identity(request)` — For routes under `/internal/v1/*`:
   - Checks `X-Enterprise-Service-Token == ENTERPRISE_SERVICE_TOKEN`.
   - Reads `x-enterprise-org-id`, `x-enterprise-user-id`, `x-enterprise-roles`,
     `x-enterprise-permission-scopes` headers.
   - Returns `ScopedIdentity` (org_id, user_id, roles, permission_scopes).
   - Fails with 401 if the service token is missing or wrong.

2. `scoped_identity(request)` — For SCIM bearer routes and dev-query fallback.

The `ScopedIdentity` object is passed as a FastAPI dependency into every route handler.
Route handlers must not derive identity from the request URL or body.

---

## RBAC enforcement

`backend_app/identity/rbac.py` — A10

Route handlers declare their scope requirement as a dependency:

```python
@router.get("/internal/v1/auth/members")
async def list_members(
    identity: ScopedIdentity = Depends(RequireScopes("admin:users")),
): ...
```

`RequireScopes(scope)`:

1. Checks `scope in identity.permission_scopes`.
2. If `RBAC_MODE=audit`: logs the denial and continues.
3. If `RBAC_MODE=enforce`: returns `403 Forbidden`.

`public_route()` marks intentionally open endpoints (OIDC callbacks, SAML assertions,
login form submissions). All other routes must declare a scope requirement.

**MFA pending:** Sessions that haven't completed MFA carry the `mfa:pending` scope.
This scope only grants access to MFA challenge/verify routes, nothing else.

---

## Public vs internal route split

| Route prefix                      | Exposed via facade?                  | Called by                         |
| --------------------------------- | ------------------------------------ | --------------------------------- |
| `/v1/mcp/*`                       | Yes (via `backend-facade /v1/mcp/*`) | Browser → facade → backend        |
| `/v1/skills*`                     | Yes                                  | Browser → facade → backend        |
| `/v1/api-keys/*`                  | Yes                                  | Browser → facade → backend        |
| `/v1/health`                      | Yes                                  | Load balancer                     |
| `/v1/dev/*`                       | Yes (dev only)                       | Browser → facade → backend        |
| `/internal/v1/auth/*`             | No                                   | facade, ai-backend                |
| `/internal/v1/mcp/*`              | No                                   | ai-backend worker                 |
| `/internal/v1/skills/*`           | No                                   | ai-backend worker, facade         |
| `/internal/v1/runtime/policies/*` | No                                   | ai-backend worker                 |
| `/internal/v1/audit*`             | No                                   | facade (merged read), admin tools |
| `/internal/v1/siem/*`             | No                                   | Admin exporter                    |
| `/internal/v1/billing/*`          | No                                   | Future billing service            |
| `/scim/v2/*` (via facade)         | Yes                                  | IdP SCIM provisioner              |

The facade's `/v1/*` routing is the canonical source of truth for what is browser-accessible.
See [backend-facade reference/api-surface.md](../../../backend-facade/docs/reference/api-surface.md).

---

## Session touch

For session-authenticated routes (anything requiring a live session), the facade calls
`POST /internal/v1/auth/sessions/touch` with the session ID before forwarding the request.
The backend validates the session (not revoked, not expired) and returns the current
session state (roles, scopes, MFA status). This is cached on the facade side for 30s.

---

## Deployment profile gates

`deployment_profile.py` — `DeploymentFeatureToggles`

Some routes and behaviours are gated by the deployment profile:

| Toggle                    | When false                 | Route/behaviour affected          |
| ------------------------- | -------------------------- | --------------------------------- |
| `allow_self_signup`       | Self-signup disabled       | `POST /v1/auth/register` disabled |
| `dev_auth_bypass_allowed` | Dev IdP disabled           | `/v1/dev/*` routes not registered |
| `require_kms_token_vault` | Startup fails              | `LocalTokenVault` refused in prod |
| `enforce_rls`             | PostgreSQL RLS not applied | Tenant isolation advisory only    |

The profile is resolved at startup from `ENTERPRISE_DEPLOYMENT_PROFILE`. Production
profiles fail closed on violated safety constraints.

---

## Database access pattern

All stores use asyncpg connection pools. Queries are tenant-scoped:
every `SELECT` and `INSERT` includes `WHERE org_id = $1`. There is no global query
that operates across all tenants.

Row-level security (RLS) is applied in production via `staged/do_rls.sql` to enforce
`org_id` isolation at the Postgres level, providing defence-in-depth beyond application-level
scoping.
