# Environment variables

All env vars the frontend reads, where they come from, and what they
default to.

See also:

- [`vite.config.ts`](../../vite.config.ts)
- [features/observability.md](../features/observability.md) — `__BUILD_SHA__`
  and `__DEPLOY_ENV__` ride into OTEL resource attributes

---

## Build-time (Vite defines)

These are baked into the bundle by `vite.config.ts` at build time. They are
**not** available at runtime — the running container has no access to the
build-time `process.env`.

| Define           | Sourced from                     | Default         | Used by                                     |
| ---------------- | -------------------------------- | --------------- | ------------------------------------------- |
| `__BUILD_SHA__`  | `process.env.GIT_SHA`            | `"dev"`         | OTEL `service.version` resource attr        |
| `__DEPLOY_ENV__` | `process.env.DEPLOY_ENVIRONMENT` | `"development"` | OTEL `deployment.environment` resource attr |

CI must set both for production builds:

```bash
GIT_SHA=$(git rev-parse HEAD) \
DEPLOY_ENVIRONMENT=production \
npm run build --workspace @0x-copilot/frontend
```

---

## Build-time (Vite `VITE_*` env)

Read at build time via `import.meta.env`. Forwarded into the bundle if
prefixed `VITE_`.

| Var                   | Default     | Used by                                                       |
| --------------------- | ----------- | ------------------------------------------------------------- |
| `VITE_DEFAULT_ORG_ID` | `"org_123"` | `LoginScreen` fallback when the URL doesn't carry an org slug |

In SaaS deploys the org is parsed from the subdomain; in single-tenant
deploys this is hardcoded at build time. SaaS = `"org_123"` placeholder is
intentionally awful so a misconfigured build is obvious in dev.

---

## Dev-server env (Vite proxy)

These are read by `vite.config.ts` while `npm run dev` is running. They do
**not** affect production builds.

| Var                  | Default                 | Effect                                                                        |
| -------------------- | ----------------------- | ----------------------------------------------------------------------------- |
| `BACKEND_FACADE_URL` | `http://127.0.0.1:8200` | Where the Vite proxy forwards `/v1/*` (i.e. which facade instance to talk to) |

Set this when running the frontend against a Docker-deployed facade:

```bash
BACKEND_FACADE_URL=http://localhost:8080 \
  npm run dev --workspace @0x-copilot/frontend
```

---

## Runtime env (`import.meta.env.DEV` / `.PROD` / `.MODE`)

Vite always sets these:

| Flag                   | When                                                           |
| ---------------------- | -------------------------------------------------------------- |
| `import.meta.env.DEV`  | `npm run dev` (local Vite server)                              |
| `import.meta.env.PROD` | `npm run build` (`vite build`)                                 |
| `import.meta.env.MODE` | `"development"` / `"production"` / custom mode (`-- --mode=…`) |

Used to gate dev-only code paths so production builds tree-shake them.
See [features/dev-idp.md](../features/dev-idp.md) for the canonical use:
every dev-IdP caller is guarded with `if (!import.meta.env.DEV) return null;`.

---

## localStorage keys (effectively runtime config)

Not env vars, but values the app persists between sessions:

| Key                           | Owner         | Purpose                                   |
| ----------------------------- | ------------- | ----------------------------------------- |
| `enterprise.auth.bearer`      | `AuthContext` | Bearer when `persistBearer=true`          |
| `enterprise.dev.persona_slug` | `devIdp`      | Currently selected dev persona (dev only) |

`<AuthProvider persistBearer={false}>` opts out of bearer persistence for
deploy profiles (e.g. single-tenant bank) where browser-local bearers are
disallowed.

---

## Server-side env (read by the **facade**, not the FE)

These belong to `services/backend-facade` and are listed here only because
they affect what the frontend sees:

- `BACKEND_ENVIRONMENT` — when `development`, the facade registers
  `/v1/dev/*` routes the frontend's dev-IdP module calls.
- `ENTERPRISE_AUTH_SECRET` — every bearer the FE attaches is verified
  against this secret on the facade.
- `ENTERPRISE_DEPLOYMENT_PROFILE` — drives the facade's feature toggles
  (e.g. `allow_self_signup`).

See [`services/backend-facade/docs/reference/env-vars.md`](../../../../services/backend-facade/docs/reference/env-vars.md) for the full server-side list.
