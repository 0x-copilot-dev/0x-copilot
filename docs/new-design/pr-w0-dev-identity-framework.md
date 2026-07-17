# PR W0.1 — Dev Identity Framework + Auth Consolidation

> **Status:** Draft (PRD + Spec + Architecture)
> **Owner:** `services/backend` (dev IdP) · `services/backend-facade` (bearer verify, no bypass) · `services/ai-backend` (one `Depends`) · `apps/frontend` (persona switcher) · tests (pytest + Vitest fixtures)
> **Size:** **M**. Net code change ≈ even (new dev IdP balanced by deleted bypass + duplicate auth paths). One new YAML fixture. Zero migrations. Zero new event types. Zero changes to streaming.
> **Reads alongside:** [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md) · [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) · [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md) · [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md)
> **Sibling docs:**
> – [PR W0.2 — Facade No-Content Forwarder](./pr-w0-facade-no-content-forwarder.md) (separate small fix)
> – Lands before any further W1/W2/W3 follow-ups; replaces dev assumptions baked into `pr-1.3`, `pr-1.5`, `pr-3.1`, `pr-3.2`.

---

## 0 · TL;DR

`DEV_AUTH_BYPASS=true` injects one hardcoded persona (`org_123`/`user_123`) at the facade for every request. It also forks the auth path: ai-backend has **two** identity helpers — a query-param fallback for legacy routes and a header-only helper for newer routes (drafts, sources, subagents). The new helper short-circuits to `None` in dev because no service token is configured, and new routes silently 400. That's how Bug 1 (`org_id and user_id are required`) reached `main`.

This PR replaces the bypass with a small env-gated **dev identity issuer** that mints **real** HMAC bearers — same token shape, same verification code, dev and prod. Personas are seeded from a YAML fixture (≥2 orgs × ≥3 personas). All ai-backend routes go through one `Annotated[RuntimeIdentity, Depends(get_identity)]`. New routes can no longer forget to authenticate; cross-org and role testing is one fixture call.

Bug 1 is closed by construction: there is exactly one auth path, and `Depends` is non-optional.

LoC: ≈ +280 (dev IdP, persona file, identity Depends, FE switcher, two fixtures) and ≈ −300 (DEV_AUTH_BYPASS branches, `scoped_identity`, query-param identity boilerplate). Net ≈ 0.

---

## 1 · PRD

### 1.1 Problem

| Symptom                                                                                                                           | Root cause                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /v1/agent/conversations/{cid}/sources` → **400 `org_id and user_id are required`** in dev. Same for `/subagents`, `/drafts`. | ai-backend's [`RuntimeServiceAuthenticator.trusted_identity_from_request`](../../services/ai-backend/src/runtime_api/auth.py) returns `None` when no `ENTERPRISE_SERVICE_TOKEN` is configured AND the header is absent. The facade in dev sends an _empty_ service-token header, which trips the same branch. New routes (PR 1.3 drafts, PR 1.5 workspace feeds) raise on `None`; legacy routes fall back to query params via `RuntimeApiRoutes.scoped_identity`. Two paths, only one of them reaches the new routes. |
| Every dev request runs as `org_123 / user_123`.                                                                                   | `FACADE_DEV_ORG_ID` / `FACADE_DEV_USER_ID` are hardcoded env vars. Cross-org isolation, admin-vs-member, and connector-scope variations cannot be exercised without restarting the stack with different env vars.                                                                                                                                                                                                                                                                                                     |
| The bypass is a separate code path from prod auth.                                                                                | `FacadeAuthenticator._development_identity` returns a synthetic `AuthenticatedIdentity` _without_ going through bearer verification. Any bug in the bypass (e.g. an empty service-token header on the way out) hides until a new route depends on the verified path.                                                                                                                                                                                                                                                  |
| Anyone who reaches the facade port in dev is the admin.                                                                           | Identity is asserted by env, not derived from a token. There's no per-tester credential.                                                                                                                                                                                                                                                                                                                                                                                                                              |

### 1.2 Goals

1. **One auth path, dev and prod.** Bearer in → `AuthenticatedIdentity` out. Same code, same secrets, same shape.
2. **Multi-persona / multi-org dev.** A YAML fixture seeds at least two orgs (`acme`, `contoso`) and at least three personas (member, admin, cross-org admin).
3. **Dev IdP** mints real HMAC bearers via `POST /v1/dev/identity/mint`, lists available personas via `GET /v1/dev/personas`. Both routes are registered **only** when `BACKEND_ENVIRONMENT=development`.
4. **One ai-backend identity dependency.** Every HTTP route takes `identity: Identity` (`Annotated[RuntimeIdentity, Depends(get_identity)]`). The legacy `scoped_identity` query-param helper and the `trusted_identity_from_request → None` branch are deleted.
5. **Pytest fixture** `as_persona(slug) → AsyncClient` for backend / facade / ai-backend integration tests. **Vitest fixture** `asPersona(slug)` for component tests that mount AuthContext.
6. **Frontend dev persona switcher** in the topbar UserCard, visible iff `import.meta.env.DEV`. Selecting a persona mints a fresh bearer and soft-reloads.
7. **Zero new event types. Zero migrations. Zero streaming changes.** Compatible with PR 1.1, 1.3, 1.4, 1.5, 3.1, 3.2 in any merge order.

### 1.3 Non-goals

- A full OIDC dev IdP (Keycloak, ory/hydra). Heavy and unnecessary — we already have HMAC bearers.
- Production auth provider work. The existing `/v1/auth/oidc/*`, `/v1/auth/saml/*`, `/v1/auth/login`, MFA flows are untouched.
- Per-persona MFA / step-up dev paths. Dev personas are minted as already-MFA-satisfied; the prod step-up gate continues to exercise its own E2E suite via real session login.
- A UI for managing personas. The YAML file **is** the admin surface.
- Database seeding by default. The YAML fixture lives in memory; a `make seed-dev` target opts into Postgres seeding only when `RUNTIME_STORE_BACKEND=postgres`.
- Concurrent multi-persona in one tab. Switching reloads. Two tabs ≈ two personas via `localStorage` per-tab key.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                              | Verified by                                |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| AC-1  | `DEV_AUTH_BYPASS`, `FACADE_DEV_ORG_ID`, `FACADE_DEV_USER_ID` are removed from `services/backend-facade`. The facade always verifies a real bearer.                                                                     | grep + facade unit test                    |
| AC-2  | `services/backend` exposes `POST /v1/dev/identity/mint` and `GET /v1/dev/personas` only when `BACKEND_ENVIRONMENT=development`. Production routes return `404`.                                                        | route-registration test                    |
| AC-3  | The dev-minted bearer verifies through facade's existing `FacadeAuthenticator.verify_identity_token` without modification.                                                                                             | facade integration test                    |
| AC-4  | `RuntimeServiceAuthenticator.trusted_identity_from_request` no longer returns `None`. Missing identity headers always raise `401`.                                                                                     | ai-backend unit test                       |
| AC-5  | Every ai-backend HTTP route takes `identity: Identity` via `Depends(get_identity)`. The `scoped_identity` helper and the `org_id` / `user_id` query parameters across legacy routes are deleted.                       | grep + ruff custom check                   |
| AC-6  | `GET /v1/agent/conversations/{cid}/sources`, `/subagents`, `/drafts` return `200` in dev with a persona-minted bearer.                                                                                                 | curl + integration test                    |
| AC-7  | Pytest `as_persona` fixture works for at least `sarah_acme`, `marcus_admin`, `alex_contoso_admin`.                                                                                                                     | `tests/integration/test_dev_identity.py`   |
| AC-8  | Cross-org isolation: `sarah_acme` creates a chat; `alex_contoso_admin` returns `404` on `GET /v1/agent/conversations/{cid}`.                                                                                           | `tests/integration/test_dev_identity.py`   |
| AC-9  | The frontend persona switcher renders only in `import.meta.env.DEV` and is absent from production builds.                                                                                                              | Vitest component test                      |
| AC-10 | Vitest `asPersona` fixture returns a fetch wrapper with the correct `Authorization` header for component tests that consume `AuthContext`.                                                                             | `apps/frontend/src/test/asPersona.test.ts` |
| AC-11 | Streaming end-to-end: `POST /v1/agent/runs` as `marcus_admin`, replay events, all event kinds (`run_queued → run_started → model_call_started → model_delta → final_response → run_completed`) flow through unchanged. | `tests/e2e/test_run_with_persona.py`       |
| AC-12 | A prod-build CI step asserts `/v1/dev/*` is a 404 when `BACKEND_ENVIRONMENT=production`.                                                                                                                               | CI                                         |

### 1.5 Risks / mitigations

| Risk                                                                                    | Mitigation                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/v1/dev/*` accidentally enabled in prod.                                               | Routes are added to the FastAPI router **inside** an `if os.environ.get("BACKEND_ENVIRONMENT","").lower() == "development":` guard. Prod-build CI asserts 404. The `make prod` target already refuses `DEV_AUTH_BYPASS=true` — we extend it to also assert `BACKEND_ENVIRONMENT=production`. |
| Removing `scoped_identity` breaks tests that pass `org_id` / `user_id` as query params. | The pytest fixture is the migration: every test moves to `as_persona`. Query-param identity passing is removed in the same PR — there is no transitional period.                                                                                                                             |
| Dev YAML loaded once at import, leaving stale state when the file is edited.            | The persona loader checks file `mtime` on each `/v1/dev/personas` call and reloads if changed. Acceptable for dev.                                                                                                                                                                           |
| FE persona-switch flicker if bearer expiry races a render.                              | Dev bearers carry far-future `exp` (1 year). The switch flow is `mint → store → soft reload`, not a live swap.                                                                                                                                                                               |
| Persona file checked into the repo includes a "spoofable" identity.                     | Dev-only: routes don't exist in prod, secret is dev-only, payload doesn't grant production access. The YAML lives next to the rest of the dev fixtures.                                                                                                                                      |
| `ENTERPRISE_AUTH_SECRET` accidentally identical between dev and prod.                   | `make prod` already requires `ENTERPRISE_AUTH_SECRET` to be set explicitly. We extend it: prod-build CI rejects literal `dev-only-` prefixes. The default dev secret is `dev-only-not-for-prod`.                                                                                             |

### 1.6 Unit testing requirements

- **backend dev IdP** — route env-gating, persona YAML schema validation, mint return shape, list-personas response shape, mtime reload.
- **facade** — bearer verify path unchanged; explicit assertion that `DEV_AUTH_BYPASS=true` is no longer honored (the env var is read nowhere).
- **ai-backend** — `get_identity` raises `401` on missing headers, returns `RuntimeIdentity` on valid headers; cross-org service-layer scoping unchanged.
- **pytest fixture** — mint flow against running backend; cross-org isolation; admin-role propagation.
- **frontend** — persona switcher renders iff `import.meta.env.DEV`, mints new bearer on selection, persists last persona in `localStorage`.
- **Vitest `asPersona`** — returns a `fetch` wrapper that includes the `Authorization` header; usable from `AuthContext`-aware components.

### 1.7 User stories

| As…                          | I want…                                                                 | So that…                                                                                   |
| ---------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| an engineer on `make dev`    | to flip from "Sarah" to "Marcus the admin" without restarting the stack | I can verify admin-only flows (PR 1.2.1 override, PR 1.6 workspace defaults) in seconds    |
| a test author                | one fixture that returns a pre-authenticated client for any persona     | tests assert cross-org isolation and per-role behavior without scaffolding                 |
| a future-me adding a route   | the auth check to be one line that's impossible to forget               | new routes can't reintroduce Bug 1 by re-implementing the same broken pattern              |
| an SRE running prod-build CI | the dev IdP routes to be physically absent from the production image    | a misconfigured env var can't expose a "mint any identity" endpoint to the public internet |

---

## 2 · Spec

### 2.1 Architecture (one diagram)

```
┌────────────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ make dev               │    │ pytest           │    │ vitest           │
│ FE boot:               │    │ as_persona       │    │ asPersona        │
│  no bearer? →          │    │ fixture          │    │ fixture          │
│  GET /v1/dev/personas  │    └────────┬─────────┘    └────────┬─────────┘
│  POST /v1/dev/identity │             │                       │
│       /mint            │             │                       │
│  store bearer          │             │                       │
└──────────┬─────────────┘             │                       │
           │                            │                       │
           └────────────────────────────┴───────────────────────┘
                                        │
                                        ▼   POST /v1/dev/identity/mint
                          ┌──────────────────────────────────────┐
                          │ services/backend                     │
                          │  /v1/dev/* routes (env-gated)        │
                          │                                      │
                          │  PersonaDirectory.load(YAML)         │
                          │  TokenMinter.mint(persona) ─────────┐│
                          │   reuses FacadeAuthenticator's HMAC ││
                          └──────────────────────────────────────┘│
                                                                  │ bearer
                                                                  ▼
                          ┌──────────────────────────────────────┐
                          │ services/backend-facade              │
                          │  FacadeAuthenticator.verify_token    │
                          │   → AuthenticatedIdentity            │
                          │   → service_headers() to upstream    │
                          └────────────────┬─────────────────────┘
                                           │ x-enterprise-org-id, etc.
                                           ▼
                          ┌──────────────────────────────────────┐
                          │ services/ai-backend                  │
                          │  Depends(get_identity) → Identity    │
                          │  used by EVERY route                 │
                          └──────────────────────────────────────┘
```

### 2.2 New files

| Path                                                              | Purpose                                                                            | Approx LoC |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ---------- |
| `services/backend/src/backend_app/dev_idp/__init__.py`            | Package marker.                                                                    | 1          |
| `services/backend/src/backend_app/dev_idp/personas.py`            | `Persona` model + `PersonaDirectory.load(path)`.                                   | 60         |
| `services/backend/src/backend_app/dev_idp/routes.py`              | `/v1/dev/personas`, `/v1/dev/identity/mint`. Env-gated registration helper.        | 80         |
| `services/backend/dev_personas.yaml`                              | Two orgs, three personas — see §2.4.                                               | 30         |
| `services/ai-backend/src/runtime_api/identity.py`                 | `RuntimeIdentity` re-export + `get_identity` Depends + `Identity` Annotated alias. | 30         |
| `apps/frontend/src/features/auth/devPersonaSwitcher.tsx`          | Topbar dropdown, `import.meta.env.DEV`-gated.                                      | 80         |
| `apps/frontend/src/test/asPersona.ts`                             | Vitest fixture.                                                                    | 30         |
| `services/backend/tests/integration/test_dev_idp.py`              | Mint, list, env-gating, mtime reload.                                              | 80         |
| `services/backend-facade/tests/integration/test_dev_personas.py`  | Cross-org isolation, role propagation through facade.                              | 80         |
| `services/ai-backend/tests/unit/runtime_api/test_get_identity.py` | Depends raises 401 on missing headers; valid headers return `RuntimeIdentity`.     | 50         |

**New code total:** ≈ 520 LoC including tests, ≈ 280 LoC excluding tests.

### 2.3 Files removed / shrunk

| Path                                                    | Change                                                                                                                                               | LoC delta |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `services/backend-facade/src/backend_facade/auth.py`    | Delete `_development_identity`, `_is_dev_auth_bypass_enabled`, all `DEV_AUTH_BYPASS` branches in `authenticate_request`.                             | −60       |
| `services/ai-backend/src/runtime_api/auth.py`           | Delete the `elif not supplied: return None` branch in `trusted_identity_from_request`. Function now always returns or raises.                        | −5        |
| `services/ai-backend/src/runtime_api/http/routes.py`    | Delete `RuntimeApiRoutes.scoped_identity` and remove `org_id`/`user_id` query parameters from every legacy route. Replace with `identity: Identity`. | −180      |
| `services/ai-backend/src/runtime_api/http/workspace.py` | Delete `_scoped_identity`, replace with `identity: Identity`.                                                                                        | −15       |
| `services/ai-backend/src/runtime_api/http/drafts.py`    | Same shape as workspace.py.                                                                                                                          | −15       |
| `Makefile`                                              | Drop `DEV_AUTH_BYPASS=true`, `FACADE_DEV_ORG_ID`, `FACADE_DEV_USER_ID` from the `dev:` target. Add `ENTERPRISE_AUTH_SECRET=dev-only-not-for-prod`.   | −3 / +1   |

**Removed code total:** ≈ 280 LoC.

### 2.4 Persona YAML (`services/backend/dev_personas.yaml`)

```yaml
# Dev-only persona directory. Loaded only when BACKEND_ENVIRONMENT=development.
# Edit and save — /v1/dev/personas reloads on file mtime change.
orgs:
  - id: org_acme
    slug: acme
    display_name: ACME Inc.
  - id: org_contoso
    slug: contoso
    display_name: Contoso Ltd.

personas:
  - slug: sarah_acme
    org_id: org_acme
    user_id: usr_sarah
    display_name: Sarah Chen
    primary_email: sarah@acme.test
    roles: [employee]
    permission_scopes: [runtime:use]

  - slug: marcus_admin
    org_id: org_acme
    user_id: usr_marcus
    display_name: Marcus Johnson
    primary_email: marcus@acme.test
    roles: [admin]
    permission_scopes: [runtime:use, users:admin]

  - slug: alex_contoso_admin
    org_id: org_contoso
    user_id: usr_alex
    display_name: Alex Rivera
    primary_email: alex@contoso.test
    roles: [admin]
    permission_scopes: [runtime:use, users:admin]
```

### 2.5 Pydantic / TS contracts

```python
# services/backend/src/backend_app/dev_idp/personas.py
from pydantic import BaseModel, Field

class DevOrg(BaseModel):
    id: str
    slug: str
    display_name: str

class DevPersona(BaseModel):
    slug: str
    org_id: str
    user_id: str
    display_name: str
    primary_email: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ("runtime:use",)

class PersonaDirectory(BaseModel):
    orgs: tuple[DevOrg, ...]
    personas: tuple[DevPersona, ...]

    @classmethod
    def load(cls, path: Path) -> "PersonaDirectory":
        ...  # yaml.safe_load + validate
```

```python
# services/backend/src/backend_app/dev_idp/routes.py
class MintRequest(BaseModel):
    persona_slug: str

class MintResponse(BaseModel):
    bearer: str
    expires_at: datetime
    identity: DevPersonaIdentity   # subset of DevPersona shown to FE

class PersonaListResponse(BaseModel):
    personas: tuple[DevPersonaSummary, ...]
```

TypeScript mirror in `packages/api-types/src/dev.ts`:

```ts
export interface DevPersonaSummary {
  slug: string;
  display_name: string;
  org_slug: string;
  roles: string[];
}

export interface DevMintResponse {
  bearer: string;
  expires_at: string;
  identity: {
    org_id: string;
    user_id: string;
    display_name: string;
    roles: string[];
    permission_scopes: string[];
  };
}
```

### 2.6 Dev IdP routes

```
POST /v1/dev/identity/mint
  body: { "persona_slug": "marcus_admin" }
  200:  MintResponse
  404:  persona slug not in directory
  503:  ENTERPRISE_AUTH_SECRET not configured

GET  /v1/dev/personas
  200:  PersonaListResponse
```

Both registered iff `BACKEND_ENVIRONMENT=development`. Otherwise the FastAPI app does **not** include the router — they 404, not 401.

### 2.7 ai-backend identity dependency

```python
# services/ai-backend/src/runtime_api/identity.py
from typing import Annotated
from fastapi import Depends, Request
from runtime_api.auth import RuntimeServiceAuthenticator, TrustedRequestIdentity

RuntimeIdentity = TrustedRequestIdentity   # alias; same dataclass, clearer name

async def get_identity(request: Request) -> RuntimeIdentity:
    return RuntimeServiceAuthenticator.require_identity(request)

Identity = Annotated[RuntimeIdentity, Depends(get_identity)]
```

`require_identity` is the renamed (and simplified) successor to `trusted_identity_from_request` — it never returns `None`, always returns `RuntimeIdentity` or raises `HTTPException(401)`.

### 2.8 Route migration pattern

Before:

```python
@classmethod
async def get_messages(
    cls,
    request: Request,
    conversation_id: str,
    org_id: str | None = Query(None, min_length=1),
    user_id: str | None = Query(None, min_length=1),
    limit: int = 50,
) -> MessageListResponse:
    org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
    return await cls.service(request).list_messages(
        org_id=org_id, user_id=user_id, conversation_id=conversation_id, limit=limit,
    )
```

After:

```python
@classmethod
async def get_messages(
    cls,
    request: Request,
    conversation_id: str,
    identity: Identity,
    limit: int = 50,
) -> MessageListResponse:
    return await cls.service(request).list_messages(
        org_id=identity.org_id,
        user_id=identity.user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
```

Boilerplate per route shrinks by 4 lines. There is **no** path through which a route can omit the identity argument and still be invoked.

### 2.9 Pytest fixture

```python
# services/ai-backend/tests/conftest.py (and analogous in backend / facade)
import os, httpx, pytest

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8100")
FACADE_URL  = os.environ.get("FACADE_URL",  "http://127.0.0.1:8200")

@pytest.fixture
def as_persona():
    """Return an httpx.AsyncClient pre-authenticated as the named dev persona."""
    async def _factory(slug: str) -> httpx.AsyncClient:
        async with httpx.AsyncClient(base_url=BACKEND_URL) as backend:
            mint = await backend.post("/v1/dev/identity/mint", json={"persona_slug": slug})
            mint.raise_for_status()
            bearer = mint.json()["bearer"]
        return httpx.AsyncClient(
            base_url=FACADE_URL,
            headers={"authorization": f"Bearer {bearer}"},
        )
    return _factory
```

Cross-org test:

```python
async def test_cross_org_isolation(as_persona):
    sarah   = await as_persona("sarah_acme")
    contoso = await as_persona("alex_contoso_admin")
    cid = (await sarah.post("/v1/agent/conversations", json={"title": "x"})).json()["conversation_id"]
    resp = await contoso.get(f"/v1/agent/conversations/{cid}")
    assert resp.status_code == 404
```

### 2.10 Vitest fixture

```ts
// apps/frontend/src/test/asPersona.ts
const BACKEND = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8100";

export async function asPersona(slug: string): Promise<typeof fetch> {
  const r = await fetch(`${BACKEND}/v1/dev/identity/mint`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ persona_slug: slug }),
  });
  if (!r.ok) throw new Error(`mint ${slug} failed: ${r.status}`);
  const { bearer } = await r.json();
  return ((input: RequestInfo | URL, init?: RequestInit) =>
    fetch(input, {
      ...init,
      headers: { ...(init?.headers ?? {}), authorization: `Bearer ${bearer}` },
    })) as typeof fetch;
}
```

### 2.11 Frontend persona switcher

- Component `apps/frontend/src/features/auth/DevPersonaSwitcher.tsx`.
- Renders only if `import.meta.env.DEV`. Production tree-shakes via the existing build profile.
- Mounted inside `UserCard`'s footer (PR 2.2 surface).
- On mount: fetch `GET /v1/dev/personas` once; cache in component state.
- On select: `POST /v1/dev/identity/mint` → write `bearer` and `persona_slug` to `localStorage` → `window.location.reload()`.
- Default persona on first load: `localStorage.persona_slug ?? "sarah_acme"`.

```tsx
// shape; full impl ~80 LoC
export function DevPersonaSwitcher() {
  if (!import.meta.env.DEV) return null;
  const [personas, setPersonas] = useState<DevPersonaSummary[]>([]);
  const current = localStorage.getItem("persona_slug") ?? "sarah_acme";
  useEffect(() => {
    fetch("/v1/dev/personas")
      .then((r) => r.json())
      .then((d) => setPersonas(d.personas));
  }, []);
  async function pick(slug: string) {
    const r = await fetch("/v1/dev/identity/mint", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ persona_slug: slug }),
    });
    const { bearer } = await r.json();
    localStorage.setItem("persona_bearer", bearer);
    localStorage.setItem("persona_slug", slug);
    window.location.reload();
  }
  return (
    <select value={current} onChange={(e) => pick(e.target.value)}>
      {personas.map((p) => (
        <option key={p.slug} value={p.slug}>
          {p.display_name} · {p.org_slug} · {p.roles.join(",")}
        </option>
      ))}
    </select>
  );
}
```

### 2.12 AuthContext bootstrap (FE)

The existing `AuthContext` becomes the single source of truth for the bearer:

1. On mount, read `localStorage.persona_bearer`. If present and non-expired, use it.
2. If absent and `import.meta.env.DEV`, mint a default persona (`sarah_acme`) and store it.
3. On `401` from any request, clear `persona_bearer` and re-mint with the same `persona_slug`.

In production, step 2 is skipped — the user goes through the real `/v1/auth/login` flow as today.

### 2.13 Make targets

```makefile
dev: check-local-env check-provider-key
    @echo "Starting 0xCopilot dev stack"
    ...
    BACKEND_ENVIRONMENT=development \
    ENTERPRISE_AUTH_SECRET=$${ENTERPRISE_AUTH_SECRET:-dev-only-not-for-prod} \
    .venv/bin/python -m uvicorn ...

dev-bearer:                                           # convenience CLI helper
    @curl -sS -X POST http://127.0.0.1:8100/v1/dev/identity/mint \
        -H 'content-type: application/json' \
        -d "{\"persona_slug\":\"$(or $(PERSONA),sarah_acme)\"}" | jq -r .bearer

seed-dev:                                             # opt-in Postgres seed
    @test "$$RUNTIME_STORE_BACKEND" = "postgres" || (echo "RUNTIME_STORE_BACKEND must be postgres" && exit 1)
    cd services/backend && PYTHONPATH=src:../../packages/service-contracts/src \
        .venv/bin/python -m backend_app.dev_idp.seed dev_personas.yaml
```

### 2.14 Security

- **Env-gating** is the only thing keeping dev IdP off prod. The router is added to `app` inside an `if BACKEND_ENVIRONMENT == "development":` guard. Prod-build CI verifies a `404` from `/v1/dev/identity/mint` against the production image.
- **Secret hygiene**: prod-build CI rejects any `ENTERPRISE_AUTH_SECRET` whose value starts with `dev-only-`.
- **No mint auth required in dev**: the dev IdP is the dev escape hatch. Anyone with access to the dev backend port can mint any persona. This is by design — dev backends are not exposed to networks beyond loopback / VPN.
- **Token shape** matches prod exactly: HMAC over a JSON payload with `org_id`, `user_id`, `roles`, `permission_scopes`, `connector_scopes`, `iat`, `exp`. No new claim types. `sid` claim is omitted in dev (acceptable when `REQUIRE_SESSION_BINDING` is false; documented).
- **Trust boundary unchanged** between facade and ai-backend: same `service_headers()` pattern, same `RuntimeServiceAuthenticator` verification. Just no `None` short-circuit.

### 2.15 Edge cases

| Case                                    | Behavior                                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Persona slug not in YAML                | `POST /v1/dev/identity/mint` → 404 with `{detail: "persona_slug not found"}`                           |
| `ENTERPRISE_AUTH_SECRET` unset in dev   | Mint endpoint → 503; `make dev` sets a default value upstream so this is rarely hit                    |
| Bearer expired                          | Facade returns 401 → AuthContext re-mints with the cached `persona_slug`                               |
| YAML edited while running               | `mtime` checked on each `/v1/dev/personas` request; reloaded if changed                                |
| Concurrent persona switch in two tabs   | Last-write-wins on `localStorage`. Acceptable in dev. Per-tab persona is a possible follow-up.         |
| Pytest test asks for an unknown persona | `as_persona("nonexistent")` raises `httpx.HTTPStatusError(404)` immediately. No silent default.        |
| `BACKEND_ENVIRONMENT` unset             | Treated as production. `/v1/dev/*` not registered. (Same fail-closed posture as `FACADE_ENVIRONMENT`.) |

### 2.16 Test plan

- **Backend** (`services/backend/tests/integration/test_dev_idp.py`)
  - `test_personas_endpoint_lists_yaml_personas`
  - `test_mint_returns_signed_bearer_for_known_persona`
  - `test_mint_404s_unknown_persona`
  - `test_routes_absent_in_production_environment` (env override → reload app)
  - `test_yaml_reload_on_mtime_change`

- **Facade** (`services/backend-facade/tests/integration/test_dev_personas.py`)
  - `test_minted_bearer_verifies_through_facade`
  - `test_dev_auth_bypass_env_var_is_no_longer_honored`
  - `test_cross_org_get_returns_404`
  - `test_admin_role_propagates_to_ai_backend_via_service_headers`

- **ai-backend** (`services/ai-backend/tests/unit/runtime_api/test_get_identity.py`)
  - `test_get_identity_raises_401_when_org_header_missing`
  - `test_get_identity_returns_runtime_identity_for_valid_headers`
  - `test_workspace_routes_use_identity_dependency` (FastAPI route inspection)
  - `test_drafts_routes_use_identity_dependency`
  - `test_legacy_routes_no_longer_accept_query_param_identity` (regression)

- **End-to-end**
  - `tests/e2e/test_run_with_persona.py` — full `POST /v1/agent/runs` flow as `marcus_admin`; assert all event kinds replay; assert `/sources` and `/subagents` succeed.
  - `tests/e2e/test_cross_org_isolation_full_flow.py` — `sarah_acme` creates conversation, run, citations; `alex_contoso_admin` gets 404 on every read.

- **Frontend**
  - `apps/frontend/src/features/auth/DevPersonaSwitcher.test.tsx` — renders only when `import.meta.env.DEV`; mints on selection; persists in `localStorage`.
  - `apps/frontend/src/test/asPersona.test.ts` — fixture mints + returns authed fetch.

---

## 3 · Architecture

### 3.1 Where this lives in the system

| Concern                            | Owner                     | Why                                                                                                        |
| ---------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Persona directory + bearer minting | `services/backend`        | Identity ownership lives in `backend` per the service-boundary spec.                                       |
| Bearer verification                | `services/backend-facade` | Already has `FacadeAuthenticator.verify_identity_token` — unchanged.                                       |
| Identity dependency                | `services/ai-backend`     | One `Depends(get_identity)` used by every route — replaces two divergent helpers.                          |
| Persona switcher UI                | `apps/frontend`           | Dev-only, behind `import.meta.env.DEV` so production builds tree-shake it away.                            |
| Pytest fixture                     | each service's `tests/`   | Fixture is small enough that duplicating it in three `conftest.py` files is cheaper than a shared package. |
| Vitest fixture                     | `apps/frontend/src/test/` | Co-located with FE component tests.                                                                        |

### 3.2 Streaming impact — explicitly **none**

The streaming handshake (`GET /v1/agent/runs/{id}/stream?after_sequence=N`, `sequence_no` ordering, replay-after-sequence semantics, projection of `event_type`/`activity_kind`/`status`) is untouched.

The single change is: the stream route's auth dependency is now `Depends(get_identity)` instead of an inline header read. Verified identity reaches the SSE handler exactly as before; the loop is unchanged.

Concretely:

- `services/ai-backend/src/runtime_api/http/runs.py` — stream + events handlers replace inline `org_id`/`user_id` query parameters with `identity: Identity`.
- `runtime_worker` — untouched. It reads identity from the persisted run row, not from the inbound request.
- `agent_runtime/execution/*` — untouched.
- `runtime_events` table schema, `runtime_citations`, `runtime_async_tasks` — untouched.

### 3.3 Agent harness impact — none

- `agent_runtime/capabilities/middleware/permissions.py` (capability gating) reads identity off the run record, which is set at conversation/run create time. The conversation create handler already pulls `identity.org_id` / `identity.user_id` — that's where they end up persisted. Migrating to `Identity` doesn't change what gets persisted.
- Subagent dispatch (`agent_runtime/delegation/subagents/`) — untouched.
- Memory, tool execution, MCP loaders — untouched.

### 3.4 Database schema impact — none

No new tables, no migrations, no column changes. `runtime_audit_log`, `runtime_events`, `runtime_citations`, `runtime_async_tasks`, `conversations`, `conversation_connector_overrides`, `workspace_defaults`, `retention_policies` — all unchanged.

The persona YAML lives **outside** the database in dev. The opt-in `make seed-dev` target writes personas to `organizations`, `users`, and `org_memberships` (existing tables from the identity migrations) — not new schema.

### 3.5 Why this is small

| What we add                                                | Approx LoC |
| ---------------------------------------------------------- | ---------- |
| `dev_idp/personas.py` (Pydantic + YAML loader)             | 60         |
| `dev_idp/routes.py` (mint + list + env-gated registration) | 80         |
| `dev_personas.yaml` (fixture)                              | 30         |
| `runtime_api/identity.py` (Depends + alias)                | 30         |
| FE persona switcher component                              | 80         |
| pytest fixture (one helper, copied into three conftests)   | 30         |
| Vitest fixture                                             | 30         |
| **Total new (excluding tests)**                            | **≈ 340**  |
| Tests (unit + integration + e2e + Vitest)                  | ≈ 240      |
| **Total new (with tests)**                                 | **≈ 580**  |

| What we delete                                                                           | Approx LoC |
| ---------------------------------------------------------------------------------------- | ---------- |
| Facade `_development_identity` + `_is_dev_auth_bypass_enabled` + bypass branches         | 60         |
| ai-backend `RuntimeApiRoutes.scoped_identity` helper                                     | 25         |
| ai-backend route boilerplate (`org_id` / `user_id` query params + scoped_identity calls) | 180        |
| ai-backend `trusted_identity_from_request → None` branch + callers                       | 15         |
| Makefile / env / docs cleanup                                                            | 10         |
| **Total removed**                                                                        | **≈ 290**  |

**Net code change (excluding tests):** ≈ +50 LoC.
**Net code change (with tests):** ≈ +290 LoC.

### 3.6 No third-party / new middleware

We deliberately do **not** add:

| Considered                                       | Rejected because                                                                                                                                                                     |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `fastapi-users`, `fastapi-cloudauth`             | Production identity stack already exists. Dev IdP needs ~140 LoC; pulling in a framework would dwarf what we save.                                                                   |
| `python-jose`, `pyjwt`                           | Token format is already HMAC over a JSON envelope (`FacadeAuthenticator.verify_identity_token`). Reusing it keeps signing + verification symmetric and avoids a second token format. |
| Keycloak / ory hydra dev container               | Requires Docker boot for `make dev`, defeats `make dev`'s zero-infra promise.                                                                                                        |
| `httpx-auth`                                     | Test fixture is one function; importing `httpx-auth` is 5x the LoC of the fixture itself.                                                                                            |
| OpenAPI `oauth2_password` flow                   | Not how prod auth works; misleading dev affordance.                                                                                                                                  |
| FastAPI `Security(...)` + `OAuth2PasswordBearer` | Not how prod auth works; misleading dev affordance.                                                                                                                                  |

We **do** reuse:

- `FacadeAuthenticator.sign_identity_token` from `services/backend-facade/src/backend_facade/auth.py` — the dev IdP imports nothing from facade (boundary rule), so the **same** HMAC/sign helper is duplicated as `services/backend/src/backend_app/dev_idp/_sign.py` (~15 LoC). The duplication is acceptable per service boundaries; the alternative is a new shared package, which the rules call out as worse-than-duplication for primitives this small.
- Existing `copilot_service_contracts.headers` constants (already in `packages/service-contracts`) — the natural home.
- Existing `AuthenticatedIdentity` / `TrustedRequestIdentity` shapes — no new types.
- Existing FastAPI `Depends` + `Annotated` patterns.

### 3.7 DRY — what consolidates

| Before                                                                               | After                                                        |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| Two helpers (`scoped_identity`, `trusted_identity_from_request`) doing the same job. | One Depends (`get_identity`).                                |
| Two code paths (query params + headers) reaching legacy routes.                      | One code path (verified bearer → headers).                   |
| Two ways to "be" a dev user (env-injected bypass, or no token at all).               | One way (mint a bearer).                                     |
| ~40 routes each repeating `org_id: str \| None = Query(...)` boilerplate.            | ~40 routes each take `identity: Identity`.                   |
| Two test patterns (some tests pass org_id as query, others as header).               | One pattern (`as_persona` fixture returns an authed client). |

### 3.8 Sequence — Sarah opens the app in `make dev`

```
1. FE boot → AuthContext.localStorage["persona_bearer"]?
   └── empty → fetch GET /v1/dev/personas (proxied through facade → backend)
       └── pick localStorage["persona_slug"] ?? "sarah_acme"
       └── POST /v1/dev/identity/mint { persona_slug: "sarah_acme" }
           └── backend dev_idp signs HMAC bearer with ENTERPRISE_AUTH_SECRET
       └── store bearer + slug in localStorage

2. Subsequent FE request: GET /v1/agent/conversations
   ├── facade verifies bearer via FacadeAuthenticator.verify_identity_token
   │   → AuthenticatedIdentity(org_acme, usr_sarah, [employee], [runtime:use], {})
   ├── facade attaches service_headers() → forwards to ai-backend
   └── ai-backend Depends(get_identity) → RuntimeIdentity(org_acme, usr_sarah, ...)
       → service.list_conversations(org_id=org_acme, user_id=usr_sarah)
```

### 3.9 Sequence — Sarah switches to Marcus in dev

```
1. UserCard's DevPersonaSwitcher dropdown → "Marcus Johnson · acme · admin"
2. POST /v1/dev/identity/mint { persona_slug: "marcus_admin" }
3. localStorage["persona_bearer"] = newBearer
   localStorage["persona_slug"]   = "marcus_admin"
4. window.location.reload()
5. AuthContext.localStorage["persona_bearer"] hits → no re-mint
6. Subsequent requests are now (org_acme, usr_marcus, [admin], [runtime:use, users:admin])
7. Marcus can hit PATCH /v1/agent/conversations/{cid}/connectors with admin override (PR 1.2.1)
```

### 3.10 Sequence — pytest cross-org isolation

```
sarah   = await as_persona("sarah_acme")           # mints bearer #1
contoso = await as_persona("alex_contoso_admin")   # mints bearer #2

cid = (await sarah.post("/v1/agent/conversations", json={"title":"x"})).json()["conversation_id"]
# facade(bearer #1) → ai-backend(org=acme, user=sarah) → INSERT conversation row with org_id=org_acme

resp = await contoso.get(f"/v1/agent/conversations/{cid}")
# facade(bearer #2) → ai-backend(org=contoso, user=alex)
# service.get_conversation filters WHERE org_id=org_contoso → no row → 404

assert resp.status_code == 404
```

### 3.11 Edge cases

| Case                                                                | Behavior                                                                                                                                                                                                       |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Multiple browser tabs with different personas                       | Each tab reads `localStorage` once at boot; switching in one tab affects all tabs only after reload of those tabs. Acceptable.                                                                                 |
| Persona deleted from YAML while bearer is in `localStorage`         | Bearer remains valid until `exp`. Subsequent requests succeed (bearer is signed, not directory-checked at request time). On 401, AuthContext attempts to re-mint and gets 404 — falls back to default persona. |
| YAML schema invalid                                                 | `PersonaDirectory.load` raises `ValidationError`; backend startup fails fast. Not a runtime hazard.                                                                                                            |
| Two personas with the same `slug`                                   | Pydantic / YAML loader rejects on load (uniqueness validator).                                                                                                                                                 |
| `ENTERPRISE_AUTH_SECRET` rotated mid-session                        | Existing bearers fail verification → AuthContext re-mints. Acceptable.                                                                                                                                         |
| `BACKEND_ENVIRONMENT=development` + `FACADE_ENVIRONMENT=production` | Inconsistent — facade rejects bearers minted with the dev secret because facade reads its own env. Documented in `make` validation.                                                                            |

### 3.12 Rollout

Single PR. Land all of it together — partial removal of `DEV_AUTH_BYPASS` would leave `make dev` half-working.

Pre-merge:

- All existing tests migrate to `as_persona`.
- Prod-build CI verifies `/v1/dev/*` returns 404 in `BACKEND_ENVIRONMENT=production`.
- Prod-build CI rejects `ENTERPRISE_AUTH_SECRET` values starting with `dev-only-`.

Post-merge:

- Update root `CLAUDE.md`, `services/backend/CLAUDE.md`, `services/backend-facade/CLAUDE.md`, `apps/frontend/CLAUDE.md` to remove all references to `DEV_AUTH_BYPASS`, `FACADE_DEV_ORG_ID`, `FACADE_DEV_USER_ID`.
- Update `README.md` quickstart: replace "set `OPENAI_API_KEY` and run `make dev`" with the same plus a one-liner about the persona switcher.
- Note in `services/ai-backend/docs/specs/` any spec that referenced `scoped_identity`.

### 3.13 Open questions (small)

| #   | Question                                                                                                        | Default                                                                |
| --- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Q-1 | Should the dev IdP support `?roles=...&permission_scopes=...` overrides on `/mint` for ad-hoc capability tests? | **No** — the YAML is the source of truth. Add a persona instead.       |
| Q-2 | Should we also seed `org_memberships` rows so PR 4.x admin-membership tests have realistic data?                | **Yes**, in the `make seed-dev` target only.                           |
| Q-3 | Per-tab persona (use `sessionStorage` instead of `localStorage`)?                                               | **No** — requires more careful auth context wiring; revisit if needed. |
| Q-4 | Should the persona switcher show the role badge inline ("Sarah Chen · employee", "Marcus Johnson · admin")?     | **Yes** — no extra cost, and it's the whole point.                     |

---

## 4 · Acceptance checklist

- [ ] `DEV_AUTH_BYPASS` / `FACADE_DEV_ORG_ID` / `FACADE_DEV_USER_ID` deleted from `services/backend-facade`.
- [ ] `services/backend/dev_personas.yaml` committed with 2 orgs × 3 personas.
- [ ] `POST /v1/dev/identity/mint` and `GET /v1/dev/personas` registered iff `BACKEND_ENVIRONMENT=development`.
- [ ] Prod-build CI asserts both routes are absent in production images.
- [ ] `services/ai-backend/src/runtime_api/identity.py` exists; exports `Identity = Annotated[RuntimeIdentity, Depends(get_identity)]`.
- [ ] Every ai-backend HTTP route uses `identity: Identity`.
- [ ] `RuntimeApiRoutes.scoped_identity` helper deleted; `org_id`/`user_id` query parameters removed from every legacy route.
- [ ] `RuntimeServiceAuthenticator.trusted_identity_from_request` no longer returns `None`.
- [ ] `as_persona` pytest fixture lives in each service's `conftest.py` and works against `make dev`.
- [ ] `asPersona` Vitest fixture lives in `apps/frontend/src/test/`.
- [ ] `DevPersonaSwitcher` mounts inside `UserCard`; renders only in `import.meta.env.DEV`.
- [ ] Cross-org isolation E2E test green.
- [ ] `/sources`, `/subagents`, `/drafts` succeed in dev with persona-minted bearer (this is the original Bug 1 closure).
- [ ] Streaming run end-to-end with persona bearer; all event kinds present.
- [ ] CLAUDE.md (root, backend, facade, frontend) updated.
- [ ] README dev-quickstart updated.

---

## 5 · References

- [`services/backend/CLAUDE.md`](../../services/backend/CLAUDE.md) — auth ownership, untrusted inputs.
- [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — capability exposure, untrusted inputs, streaming model.
- [`services/backend-facade/CLAUDE.md`](../../services/backend-facade/CLAUDE.md) — `DEV_AUTH_BYPASS` (to be deleted), facade auth.
- [`services/backend-facade/src/backend_facade/auth.py`](../../services/backend-facade/src/backend_facade/auth.py) — existing HMAC sign/verify helpers (reused for dev IdP).
- [`services/ai-backend/src/runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py) — existing `trusted_identity_from_request` (simplified by this PR).
- [`services/ai-backend/src/runtime_api/http/routes.py`](../../services/ai-backend/src/runtime_api/http/routes.py) — legacy `scoped_identity` (deleted by this PR).
- [`services/ai-backend/src/runtime_api/http/workspace.py`](../../services/ai-backend/src/runtime_api/http/workspace.py) — PR 1.5 routes; clean rewrite via `Identity`.
- [`services/ai-backend/src/runtime_api/http/drafts.py`](../../services/ai-backend/src/runtime_api/http/drafts.py) — PR 1.3 routes; clean rewrite via `Identity`.
- [`packages/service-contracts/src/copilot_service_contracts/headers.py`](../../packages/service-contracts/src/copilot_service_contracts/headers.py) — header constants used by both signing and verification.
- [PR 1.5 — Subagent + workspace pane data feeds](./pr-1.5-subagent-discovery-workspace-feeds.md) — first PR that introduced the broken pattern.
- [PR 1.3 — Draft artifact](./pr-1.3-draft-artifact.md) — second PR with the same pattern.
- [PR 1.6 — Workspace defaults + conversation lifecycle](./pr-1.6-workspace-defaults-conversation-lifecycle.md) — DELETE 500 (separate fix in PR W0.2).
