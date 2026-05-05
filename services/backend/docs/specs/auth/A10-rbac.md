# A10 — RBAC enforcement (implementation contract)

Roadmap source: [docs/roadmap/29-a10-rbac-enforcement.md](../../../../../docs/roadmap/29-a10-rbac-enforcement.md).
Implementation deltas only — what shipped and where it differs from
the roadmap text.

## Scope catalog

`packages/service-contracts/src/enterprise_service_contracts/scopes.py`
holds 14 string constants. Both backend and ai-backend import this
module so a typo on either side fails at import time, not at the
moment a 403 lands in production.

```
RUNTIME_USE         = "runtime:use"
MCP_READ            = "mcp:read"
MCP_WRITE           = "mcp:write"
CONNECTORS_AUTH     = "connectors:auth"
SKILLS_READ         = "skills:read"
SKILLS_WRITE        = "skills:write"
ADMIN_USERS         = "admin:users"
ADMIN_IDP           = "admin:idp"
ADMIN_AUDIT_EXPORT  = "admin:audit_export"
ADMIN_BUDGETS       = "admin:budgets"
ADMIN_RETENTION     = "admin:retention"
ADMIN_SIEM          = "admin:siem"
AUDIT_READ          = "audit:read"
MFA_PENDING         = "mfa:pending"  # lifecycle marker, not a permission
```

## RBAC dependency

Both services ship a `RequireScopes`, `RequireRoles`, and `public_route`
factory under `services/<svc>/src/.../rbac.py`. The ai-backend variant
adds `RequireAnyScope` for the OR semantic the spec documents on
`/v1/usage/org` (audit:read OR admin:users) — the backend service
doesn't currently need it but a future admin route can adopt the same
pattern.

Usage: route-level via `dependencies=[Depends(RequireScopes(...))]`
keeps handler signatures untouched. Several routers (e.g. ai-backend's
`/v1/agent/*`) set the dependency at the APIRouter level so every
route on the router inherits it; per-route additions for stricter
admin scopes compose naturally.

```python
from enterprise_service_contracts.scopes import MCP_WRITE
from backend_app.identity.rbac import RequireScopes

@app.post(
    "/v1/mcp/servers",
    response_model=McpServerResponse,
    dependencies=[Depends(RequireScopes(MCP_WRITE))],
)
def create_server(...): ...
```

## RBAC_MODE

`RBAC_MODE=audit` (default) — log denies via the identity audit log
and pass through. `RBAC_MODE=enforce` — log denies AND return 403.
Misconfiguration (any other value) silently falls back to audit so
the deploy stays usable while operators fix the env; the misconfig
shows up in the audit row's metadata.

The two-phase rollout is the same as the roadmap: ship audit, gather
telemetry on which legitimate calls would 403, then flip enforce per
environment. Bank/gov deploys flip enforce from day one via deployment
profile.

## mfa:pending semantics

A session that minted before MFA verify carries `mfa:pending` in its
permission scopes. The RBAC check refuses any scope on such a session
EXCEPT routes explicitly marked `public_route()`. That keeps the
session capable of the verify dance (`/internal/v1/auth/mfa/challenge`,
`/verify`, `/recovery/consume`) but locks it out of everything else.

## Per-route scope mapping

Backend (`services/backend/src/backend_app/app.py` + `routes/*.py`):

| Route                                                                       | Scope                                     |
| --------------------------------------------------------------------------- | ----------------------------------------- |
| `/v1/health`, `/healthz`, `/readyz`                                         | public                                    |
| `POST/GET/PATCH/DELETE /v1/mcp/servers`                                     | `mcp:write` / `mcp:read`                  |
| `POST /v1/mcp/servers/{id}/auth/*`                                          | `connectors:auth`                         |
| `GET /v1/mcp/oauth/callback`                                                | public (OAuth state is the trust anchor)  |
| `GET /internal/v1/mcp/cards`, `client-session`, `rpc`                       | `runtime:use`                             |
| `POST /internal/v1/mcp/.../test-token`                                      | `mcp:write`                               |
| `POST/GET/PUT/DELETE /v1/skills/*`                                          | `skills:write` / `skills:read`            |
| `GET /internal/v1/skills/*`                                                 | `runtime:use`                             |
| `POST /internal/v1/audit/export`, `audit/deploy`                            | `admin:audit_export`                      |
| `POST /internal/v1/auth/lockouts/{id}/unlock`                               | `admin:users`                             |
| `GET /internal/v1/auth/lockouts*`, `login-attempts`                         | `admin:users`                             |
| `GET /internal/v1/auth/me/login-attempts`                                   | `runtime:use` (self-service)              |
| `POST /internal/v1/auth/mfa/factors/*`                                      | `runtime:use`                             |
| `POST /internal/v1/auth/mfa/{challenge,verify,recovery/consume}`            | public (mfa-pending tolerant)             |
| `POST /internal/v1/auth/oidc/*`                                             | public (SSO entry/exit)                   |
| `POST /internal/v1/auth/saml/*`                                             | public (SSO entry/exit)                   |
| `POST /internal/v1/auth/local/{verify,bootstrap-admin}`, `password/reset/*` | public (no session yet)                   |
| `POST /internal/v1/auth/password/change`                                    | `runtime:use`                             |
| `POST /internal/v1/auth/sessions{,/touch,/dev-mint}`                        | public (caller has no usable session yet) |
| `POST /internal/v1/auth/sessions/{id}/revoke`, `GET /sessions`              | `runtime:use` (self-service)              |
| `POST/GET/DELETE /internal/v1/auth/scim/{id}/tokens*`                       | `admin:idp`                               |
| `* /internal/v1/auth/scim/resource/*`                                       | public (SCIM bearer is the trust anchor)  |

ai-backend (`services/ai-backend/src/runtime_api/http/routes.py`):

| Router                              | Default                      | Per-route override                                     |
| ----------------------------------- | ---------------------------- | ------------------------------------------------------ |
| `/v1/agent/*`                       | `runtime:use` (router-level) | —                                                      |
| `/v1/usage/*`                       | `runtime:use` (router-level) | `/org` adds `RequireAnyScope(audit:read, admin:users)` |
| `/v1/budgets/*`                     | `runtime:use` (router-level) | every route except `/me` adds `admin:budgets`          |
| `/internal/v1/skills/system`        | `runtime:use`                | —                                                      |
| `/internal/v1/audit/cursor`         | `admin:audit_export`         | —                                                      |
| `/v1/health`, `/healthz`, `/readyz` | public                       | —                                                      |

## CI scope-coverage check

`tools/check_route_scopes.py` boots each service's FastAPI app, walks
every route, and asserts the route declares its RBAC policy (route-level
or via router-level `dependencies=`). The check runs as a step in both
`ci-backend.yml` and `ci-ai-backend.yml`; a route added without an
annotation fails CI before it can ship to prod. `tools/test_check_route_scopes.py`
also wires the check into pytest so devs see the failure locally.

The detector recognises the four sanctioned dependency factories:

- `RequireScopes(...)` → carries `__rbac_required_scopes__` attribute
- `RequireRoles(...)` → carries `__rbac_required_roles__`
- `RequireAnyScope(...)` → carries `__rbac_required_any_scopes__`
- `public_route()` → returns a closure named `_public`

## Behavior changes vs phase-1 inline auth

Existing handlers still call `BackendServiceAuthenticator.scoped_identity(...)`
to resolve the verified identity for use inside the body. The route-level
`Depends(RequireScopes(...))` registers the RBAC policy for the static
check and runs the audit/enforce gate at request time. The two coexist
deliberately — refactoring every handler signature to consume the
dependency-returned identity was bigger than the value it added (the
inline call already does the identity resolve and is well-tested).

## Tests

- `services/backend/tests/identity/test_rbac.py` — 12 unit tests
  pinning audit-vs-enforce mode, mfa:pending blocking, RequireRoles
  any-of, public_route, header parsing of CSV scopes.
- `services/ai-backend/tests/unit/runtime_api/test_rbac.py` — 8 tests
  with the same shape against ai-backend's variant + RequireAnyScope.
- `tools/test_check_route_scopes.py` — runs the static check from each
  service's venv as a pytest gate.

## Out of scope (per roadmap §1.3)

- Attribute-based access control (ABAC).
- Per-resource ACLs ("user X can edit this skill specifically").
- Tenant-aware data filters at the ORM layer (existing `org_id`
  filters + C5 RLS already do this).
