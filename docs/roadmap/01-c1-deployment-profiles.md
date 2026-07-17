# PR 01 — C1: ENTERPRISE_DEPLOYMENT_PROFILE Config

**Spec ID:** C1 | **Track:** Deployment & DB | **Wave:** 0 (Foundation) | **Estimated effort:** M
**Depends on:** none
**Required for:** every other PR (profile toggles gate later behavior)

---

## 1. Functional Specification

### 1.1 Goal

Establish a single, typed source of truth for _what kind of deployment this process is running in_. The product ships in three profiles from the same container image: SaaS multi-tenant, single-tenant managed (we operate it for one customer), single-tenant self-hosted (customer operates it). Each profile flips a different set of safety defaults — what we allow today (e.g. `DEV_AUTH_BYPASS=true`) is unsafe in a bank deployment, and we need the runtime to refuse the unsafe combination instead of relying on the operator to remember.

### 1.2 User-visible behavior

- **Operators** set `ENTERPRISE_DEPLOYMENT_PROFILE` once per environment. The value is logged at startup ("Enterprise deployment profile: single_tenant_managed; toggles: …") and surfaced on `/v1/health`.
- **Developers** see no change in `make dev` (default profile is `saas_multi_tenant`).
- **Bank/gov operators** see the process refuse to start when an unsafe combination is set (e.g. `single_tenant_managed` + `DEV_AUTH_BYPASS=true`) with a specific error pointing to the offending env var.
- A new doc `docs/deployment/profiles.md` describes the toggle matrix.

### 1.3 Out of scope

- Implementing any of the toggles' downstream behavior. This PR only adds the loader, the typed object, and the startup banner. Each later PR consumes specific toggles.
- Helm chart and `docker-compose.prod.yml` artifacts beyond a minimal scaffolding referencing the new env var (full chart values come with C12).
- Customer onboarding tooling.

---

## 2. Technical Specification

### 2.1 Architecture

Hard service-boundary rule means **no shared Python package** for behavior — each service gets its own loader. Constants (enum values, env var names, toggle keys) live in `packages/service-contracts` so a typo in one service produces a mismatch we catch at lint time.

Three profiles:

- `saas_multi_tenant` — many orgs, our infra.
- `single_tenant_managed` — one org, our infra (e.g. a customer who paid us to host them in a dedicated VPC).
- `single_tenant_self_hosted` — one org, customer infra (Helm or Compose).

### 2.2 Schema changes

None. Pure config.

### 2.3 Endpoints

- `GET /v1/health` (extend existing) — adds `deployment_profile` and `feature_toggles_hash` (sha256 of the resolved toggles object, for ops dashboards to detect drift).

### 2.4 Code changes

**New shared constants** — `packages/service-contracts/src/copilot_service_contracts/deployment_profile.py`:

```python
ENV_DEPLOYMENT_PROFILE = "ENTERPRISE_DEPLOYMENT_PROFILE"

PROFILE_SAAS_MULTI_TENANT = "saas_multi_tenant"
PROFILE_SINGLE_TENANT_MANAGED = "single_tenant_managed"
PROFILE_SINGLE_TENANT_SELF_HOSTED = "single_tenant_self_hosted"

ALLOWED_PROFILES = frozenset({
    PROFILE_SAAS_MULTI_TENANT,
    PROFILE_SINGLE_TENANT_MANAGED,
    PROFILE_SINGLE_TENANT_SELF_HOSTED,
})

# Toggle keys (stable strings; loader returns a frozen dataclass with these as attrs)
TOGGLE_DEV_AUTH_BYPASS_ALLOWED = "dev_auth_bypass_allowed"
TOGGLE_REQUIRE_KMS_TOKEN_VAULT = "require_kms_token_vault"
TOGGLE_REQUIRE_FIELD_LEVEL_ENCRYPTION = "require_field_level_encryption"
TOGGLE_SIEM_EXPORT_REQUIRED = "siem_export_required"
TOGGLE_ENFORCE_RLS = "enforce_rls"
TOGGLE_ALLOW_SELF_SIGNUP = "allow_self_signup"
TOGGLE_ALLOW_VENDOR_TELEMETRY = "allow_vendor_telemetry"
TOGGLE_ALLOW_EMBEDDED_PROVIDER_KEYS = "allow_embedded_provider_keys"
TOGGLE_DEFAULT_RETENTION_DAYS = "default_retention_days"
```

**New per-service modules** (identical surface, three files):

- `services/ai-backend/src/agent_runtime/deployment/profile.py`
- `services/backend/src/backend_app/deployment_profile.py`
- `services/backend-facade/src/backend_facade/deployment_profile.py`

Each exposes a frozen `DeploymentFeatureToggles` Pydantic model with these fields and a `load_profile() -> tuple[Profile, DeploymentFeatureToggles]` function. The toggle defaults per profile:

| Toggle                         | saas_multi_tenant | single_tenant_managed | single_tenant_self_hosted |
| ------------------------------ | ----------------- | --------------------- | ------------------------- |
| dev_auth_bypass_allowed        | false             | false                 | false                     |
| require_kms_token_vault        | true              | true                  | true                      |
| require_field_level_encryption | false             | true                  | true                      |
| siem_export_required           | true              | true                  | true                      |
| enforce_rls                    | true              | true                  | true                      |
| allow_self_signup              | true              | false                 | false                     |
| allow_vendor_telemetry         | true              | false                 | false                     |
| allow_embedded_provider_keys   | true              | true                  | false                     |
| default_retention_days         | 365               | (customer)            | (customer)                |

Dev override: when `FACADE_ENVIRONMENT=development` AND `ENTERPRISE_DEPLOYMENT_PROFILE` is unset, default to `saas_multi_tenant` with `dev_auth_bypass_allowed=true`. **All other production paths fail closed.**

**Wire-in points (this PR adds the loader call but does not yet branch on toggles — that comes in later PRs):**

- [services/ai-backend/src/runtime_api/app.py](../../services/ai-backend/src/runtime_api/app.py) startup hook
- [services/ai-backend/src/runtime_worker/**main**.py](../../services/ai-backend/src/runtime_worker/__main__.py)
- [services/ai-backend/src/runtime_worker/loop.py](../../services/ai-backend/src/runtime_worker/loop.py)
- [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py) startup hook
- [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py) startup hook

The dev-bypass check at [services/backend-facade/src/backend_facade/auth.py:76-77](../../services/backend-facade/src/backend_facade/auth.py#L76-L77) becomes:

```python
if not toggles.dev_auth_bypass_allowed:
    raise EnterpriseAuthError("dev auth bypass disabled by deployment profile")
if os.getenv("DEV_AUTH_BYPASS") == "true" and ...:
    ...
```

### 2.5 Trust model & failure semantics

- Loader is called **once at startup**, result cached on `app.state.deployment` (FastAPI) or a module-level singleton (worker). No reload at request time.
- Unknown profile → process refuses to start (`SystemExit(78)`).
- Conflicting env vars (e.g. profile says `dev_auth_bypass_allowed=false` but `DEV_AUTH_BYPASS=true` is set) → process refuses to start with an error naming both vars.

### 2.6 Tenant isolation

N/A directly — this PR doesn't touch tenant boundaries. But the toggle table includes `enforce_rls` which **C5** consumes.

### 2.7 Observability

- Startup log line at INFO: `"Enterprise deployment: profile=<...> toggles_hash=<sha256[:8]>"`.
- `/v1/health` extended.
- Prometheus gauge `enterprise_deployment_info{profile=…}` set to 1 (use the well-known "info gauge" pattern).

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Setting `ENTERPRISE_DEPLOYMENT_PROFILE=saas_multi_tenant` (or unset in dev) preserves current behavior end-to-end.
- [ ] Setting `ENTERPRISE_DEPLOYMENT_PROFILE=single_tenant_managed` AND `DEV_AUTH_BYPASS=true` causes startup to fail with a single-line error naming both env vars.
- [ ] Setting `ENTERPRISE_DEPLOYMENT_PROFILE=invalid` causes startup to fail with an error listing the three valid values.
- [ ] `/v1/health` includes `deployment_profile` field on each of the three services.
- [ ] `docs/deployment/profiles.md` exists and documents the toggle matrix.

### 3.2 Test plan

**Unit tests (per service):**

- `test_load_profile_each_value` — each of the three valid profiles loads with the expected toggle defaults.
- `test_unknown_profile_fails_closed` — `ENTERPRISE_DEPLOYMENT_PROFILE=garbage` raises typed error.
- `test_missing_in_production_fails_closed` — unset profile + `FACADE_ENVIRONMENT=production` raises.
- `test_default_in_dev_is_saas_multi_tenant` — unset + `FACADE_ENVIRONMENT=development` returns saas profile.
- `test_dev_bypass_conflict` — single*tenant*\* + `DEV_AUTH_BYPASS=true` raises with both env vars in error message.

**Integration:**

- Boot each of the three services with each of the three profiles via subprocess; assert startup line and `/v1/health` response.

### 3.3 Compliance evidence produced

- CLAUDE.md §Compliance "Treat caller-supplied identity as untrusted unless from verified session/token" — strengthened by failing closed when bypass would be allowed.
- Foundational for every later "controls counted only when code+config+tests support it" claim.

### 3.4 Rollout plan

Pure additive; default profile preserves current behavior. Roll out behind no flag because no code yet branches on toggles. Production deploys begin setting `ENTERPRISE_DEPLOYMENT_PROFILE=saas_multi_tenant` explicitly within 1 release.

### 3.5 Backout plan

Revert the PR. Process startup returns to ignoring the env var.

### 3.6 Definition of done

- [ ] Three loader modules + shared constants land.
- [ ] All three services log the profile on startup and expose it on `/v1/health`.
- [ ] All unit + integration tests pass.
- [ ] `docs/deployment/profiles.md` written.
- [ ] Production deploy YAML/Helm placeholder values updated to set the env var explicitly.

---

## 4. Critical files

- New: `packages/service-contracts/src/copilot_service_contracts/deployment_profile.py`
- New: `services/ai-backend/src/agent_runtime/deployment/profile.py`
- New: `services/backend/src/backend_app/deployment_profile.py`
- New: `services/backend-facade/src/backend_facade/deployment_profile.py`
- Modify: [services/ai-backend/src/runtime_api/app.py](../../services/ai-backend/src/runtime_api/app.py)
- Modify: [services/backend/src/backend_app/app.py](../../services/backend/src/backend_app/app.py)
- Modify: [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py)
- Modify: [services/backend-facade/src/backend_facade/auth.py:76-77](../../services/backend-facade/src/backend_facade/auth.py#L76-L77)
- New: `docs/deployment/profiles.md`
