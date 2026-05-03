# Backend Facade

Product-facing API (`backend_facade/`). The single HTTP surface that web, Mac, and Windows apps are allowed to call. Forwards `/v1/*` to `backend` (MCP / skills / OAuth) and `ai-backend` (conversations, runs, events, approvals).

## Before changing behavior

Read this service's `README.md`, `ARCHITECTURE.md`, and `TESTING.md` first.

## Boundaries (hard)

- Forwards over **HTTP** to backend services. Never import `services/backend/src` or `services/ai-backend/src`. Use this service's own `.venv` — never add a sibling to `PYTHONPATH`.
- Keep `/internal/v1/*` off the facade. The facade exposes only the app-facing public surface. If a spec changes the boundary, update the spec first.
- Do not put AI orchestration logic here. The facade is a thin product API — orchestration lives in `ai-backend`.
- Do not put tenant auth ownership, billing/admin state, or product persistence here. That belongs in `backend`.

## Public contracts

Update [packages/api-types](../../packages/api-types) when app-facing payloads or routes change. The facade's surface **is** the public contract.

## Auth (untrusted input)

- `DEV_AUTH_BYPASS=true` is honored only when `FACADE_ENVIRONMENT=development`. Uses `FACADE_DEV_ORG_ID` / `FACADE_DEV_USER_ID`.
- Production fails closed if `ENTERPRISE_AUTH_SECRET` or `ENTERPRISE_SERVICE_TOKEN` is missing.
- Treat caller-supplied identity, role, scope, tenant, org, and user as untrusted unless derived from a verified session, token, mTLS identity, or IdP claim.

## Request path

Browser → Vite proxy (or nginx in prod) → `backend-facade:8200` → `backend:8100` (MCP / skills / OAuth) or `ai-backend:8000` (conversations, runs, events, approvals).
