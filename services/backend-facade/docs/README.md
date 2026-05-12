# Backend Facade — Knowledge Base

Agent-first documentation for `services/backend-facade`. Every node answers one question
and links to adjacent nodes. Read this file first; all other paths branch from here.

## What this service does

`backend-facade` is the single HTTP surface that browsers, desktop apps, and API clients
call. It is a thin proxy — no persistence, no AI orchestration, no tenant auth ownership.
It runs on `:8200` in dev and behind nginx/ingress in production.

**What it does:**

- Verifies HMAC-signed bearer tokens (or routes `atlas_pk_*` API keys to the backend verify endpoint)
- Touches the backend session store (LRU-cached 30s) to get the canonical identity
- Injects service-to-service auth headers (`X-Enterprise-Service-Token`, `x-enterprise-org-id`, `x-enterprise-user-id`, roles, scopes)
- Routes `/v1/mcp/*`, `/v1/skills/*`, `/v1/api-keys/*` → `backend:8100`
- Routes `/v1/agent/*`, `/v1/usage/*`, `/v1/budgets/*`, `/v1/retention/*` → `ai-backend:8000`
- Merges `GET /v1/skills` from both backend and ai-backend into one list

**What it does NOT do:**

- Expose `/internal/v1/*` routes
- Store any state
- Run AI orchestration
- Own auth infrastructure (session creation, token minting, IdP integration)

## Navigation

| Question                                                     | Read                                                                 |
| ------------------------------------------------------------ | -------------------------------------------------------------------- |
| How is the facade code organised? What does each module own? | [architecture/00-system-map.md](architecture/00-system-map.md)       |
| How does a request route to backend vs ai-backend?           | [architecture/01-routing.md](architecture/01-routing.md)             |
| How does bearer verification and session touch work?         | [architecture/02-auth-identity.md](architecture/02-auth-identity.md) |
| How does `GET /v1/audit` merge backend + ai-backend streams? | [features/audit-merge.md](features/audit-merge.md)                   |
| How do deployment profiles and feature toggles work?         | [features/deployment-profiles.md](features/deployment-profiles.md)   |
| Full `/v1/*` route surface with upstream targets             | [reference/api-surface.md](reference/api-surface.md)                 |
| All environment variables                                    | [reference/env-vars.md](reference/env-vars.md)                       |
