# PR 29 — A10: RBAC Enforcement at Every Route

**Spec ID:** A10 | **Track:** Identity & Access | **Wave:** 8 (RBAC + Restore) | **Estimated effort:** L
**Depends on:** A1 (roles), A2 (sessions carry permission_scopes), all of A3–A8 (real role assignments exist)
**Required for:** all bank/gov deploys

---

## 1. Functional Specification

### 1.1 Goal

Today permission scopes are _carried_ in headers and the bearer token but not _enforced_ at resource boundaries. Any authenticated caller can hit any route. This PR introduces a `RequireScopes`/`RequireRoles` decorator pattern, annotates every existing route, and enforces default-deny via a CI static check.

### 1.2 User-visible behavior

- **End user:** unchanged for normal flows (their roles cover the routes they use).
- **Anyone calling an admin route without `admin:*` scope:** 403 with audit row.
- **Operator:** can roll out in audit mode (log denies, pass through) before flipping to enforce.

### 1.3 Out of scope

- Attribute-based access control (ABAC).
- Per-resource ACLs ("user X can edit this skill").
- Tenant-aware data filters at the ORM layer (existing `org_id` filters + C5 RLS already do this).

---

## 2. Technical Specification

### 2.1 Architecture

- Permission scope catalog as constants in `packages/service-contracts/src/enterprise_service_contracts/scopes.py`.
- FastAPI dependencies `RequireScopes(*scopes)` and `RequireRoles(*roles)` in both `services/backend` and `services/ai-backend`.
- Two-phase rollout via `RBAC_MODE`:
  - `RBAC_MODE=audit` — log denies to `identity_audit_events`, pass through.
  - `RBAC_MODE=enforce` — actual 403.
- CI static check fails build if any FastAPI route is unannotated.
- Default-deny: routes that don't declare scopes (other than explicitly public ones like `/healthz`) cannot ship.

### 2.2 Schema changes

None new (uses A1's `roles.permission_scopes`).

### 2.3 Endpoints

None new. **Behavior changes on every existing endpoint** — riskiest auth PR; ships last in the auth track.

### 2.4 Code changes

**New** `packages/service-contracts/src/enterprise_service_contracts/scopes.py`:

```python
# RBAC scope catalog
MCP_READ = "mcp:read"
MCP_WRITE = "mcp:write"
SKILLS_READ = "skills:read"
SKILLS_WRITE = "skills:write"
CONNECTORS_AUTH = "connectors:auth"
RUNTIME_USE = "runtime:use"
ADMIN_USERS = "admin:users"
ADMIN_IDP = "admin:idp"
ADMIN_AUDIT_EXPORT = "admin:audit_export"
ADMIN_BUDGETS = "admin:budgets"
ADMIN_RETENTION = "admin:retention"
ADMIN_SIEM = "admin:siem"
AUDIT_READ = "audit:read"
MFA_PENDING = "mfa:pending"

ALL_SCOPES = frozenset({...})
```

**New** `services/backend/src/backend_app/identity/rbac.py`:

```python
def RequireScopes(*scopes: str) -> Callable:
    """FastAPI dependency. Returns the verified identity if all scopes present."""
    async def dep(
        identity: ScopedIdentity = Depends(scoped_identity),
        rbac_mode: str = Depends(get_rbac_mode),
    ) -> ScopedIdentity:
        missing = [s for s in scopes if s not in identity.permission_scopes]
        if missing:
            await write_audit_event(
                action="rbac.denied",
                actor_user_id=identity.user_id,
                metadata={"required": scopes, "missing": missing, "route": ...},
                outcome="denied",
            )
            if rbac_mode == "enforce":
                raise HTTPException(403, "missing scopes")
        return identity
    return dep

def RequireRoles(*roles: str) -> Callable: ...
```

Same shape in `services/ai-backend/src/runtime_api/rbac.py`.

**Annotate every route:**

- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — every route. Estimated 25+ sites; replace inline `BackendServiceAuthenticator.scoped_identity(...)` calls with `Depends(RequireScopes(...))`.
- [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py) — every route.
- Reuse existing scope-gating seam at [services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py:46-52](../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py#L46-L52) — already does scope checks for tools.

**Per-route scope mapping** (excerpt — full table in spec doc):
| Route | Required scopes |
|----------------------------------------------|--------------------------------|
| `GET /v1/agent/conversations` | `runtime:use` |
| `POST /v1/agent/conversations/{id}/runs` | `runtime:use` |
| `GET /v1/mcp/servers` | `mcp:read` |
| `POST /v1/mcp/servers` | `mcp:write` |
| `GET /v1/skills` | `skills:read` |
| `POST /v1/skills` | `skills:write` |
| `GET /v1/usage/me` | `runtime:use` |
| `GET /v1/usage/org` | `audit:read` OR `admin:users` |
| `POST /v1/budgets` | `admin:budgets` |
| `GET /v1/auth/sessions` | (any authenticated; no scope) |
| `POST /internal/v1/auth/lockouts/.../unlock` | `admin:users` |

**CI static check** `tools/check_route_scopes.py`:

- Walk FastAPI router AST.
- For each registered route, look for `Depends(RequireScopes(...))` or an explicit `@public_route` marker.
- Fail if missing.

### 2.5 Trust model & failure semantics

- 403 paired with an audit row (`action='rbac.denied'`).
- `RBAC_MODE=audit` for one release minimum to gather telemetry before enforcing.
- Public routes (`/healthz`, `/v1/auth/login`) explicitly marked `@public_route`.

### 2.6 Tenant isolation

RBAC is orthogonal to tenancy. Tenant scoping continues via `org_id` filter + C5 RLS. RBAC checks WHO within an org can do what.

### 2.7 Observability

- Audit: every deny logged.
- Metric: `rbac_check_total{route_hash, outcome}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Route requiring `admin:users` returns 403 (in `enforce` mode) or logs+passes (in `audit` mode) when caller lacks scope.
- [ ] Route with no scope annotation fails CI build.
- [ ] Tenant-isolation regression suite still passes.
- [ ] Existing happy paths still work (employees can use `runtime:use` routes; admins can use admin routes).

### 3.2 Test plan

**Unit:**

- `RequireScopes` 403s when scope missing in enforce mode; 200 in audit mode.
- `RequireScopes` returns identity when all scopes present.

**Per-route fixture:**

- Enumerate every route × every role; assert expected outcome against an explicit fixture matrix in `tests/integration/rbac/test_route_matrix.py`.

**Static check:**

- CI script flags a fixture route added without annotation.

**Regression:**

- Existing tenant-isolation tests still pass.

### 3.3 Compliance evidence produced

- "RBAC enforcement at resource level" — explicitly called out as missing in CLAUDE.md today.
- Default-deny posture (CI-enforced).
- Audit trail of denies.
- Per-route documented scopes (table in spec).

### 3.4 Rollout plan

1. Land with `RBAC_MODE=audit` everywhere. 403-eligible requests log to `identity_audit_events` but pass through.
2. After 1 release of telemetry, flip per-environment to `RBAC_MODE=enforce`.
3. Per-deployment override (bank/gov ⇒ enforce from day one).

### 3.5 Backout plan

Set `RBAC_MODE=audit`. All denies become logs.

### 3.6 Definition of done

- [ ] Scope catalog in service-contracts.
- [ ] `RequireScopes`/`RequireRoles` in both services.
- [ ] Every route annotated.
- [ ] CI static check green.
- [ ] Per-route × per-role fixture matrix passes.
- [ ] Existing test suites pass.
- [ ] Audit rows produced on deny.

---

## 4. Critical files

- New: `packages/service-contracts/src/enterprise_service_contracts/scopes.py`
- New: `services/backend/src/backend_app/identity/rbac.py`
- New: `services/ai-backend/src/runtime_api/rbac.py`
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) — every route.
- Modify: [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py) — every route.
- Modify: [services/ai-backend/src/runtime_api/auth.py:24-86](../../services/ai-backend/src/runtime_api/auth.py#L24-L86)
- Modify: [services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py:46-52](../../services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py#L46-L52)
- New: `tools/check_route_scopes.py`
- New: `tests/integration/rbac/test_route_matrix.py`
