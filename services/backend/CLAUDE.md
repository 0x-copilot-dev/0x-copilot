# Backend Service

Core backend (`backend_app/`). Today owns: MCP registration, OAuth state, token vault, user skills, audit events. Target home for: tenants, IdP integration, permissions, product persistence, admin workflows, jobs.

## Before changing behavior

Read this service's `README.md`, `ARCHITECTURE.md`, and `TESTING.md` first.

## Boundaries

- `/internal/v1/*` is consumed only by `ai-backend` (MCP cards, client sessions, RPC proxy, skill bundles). It is **not** exposed via `backend-facade`.
- App-facing routes (anything reachable from the browser) go through `backend-facade`. Never let an app call `backend` directly.
- This service must not import `services/ai-backend/src` or `services/backend-facade/src`. Cross-service work is HTTP only.
- Use this service's own `.venv`. Never add a sibling service to `PYTHONPATH`.

## Public contracts

Update [packages/api-types](../../packages/api-types) when public app-facing payloads or routes change. `/internal/v1/*` is not mirrored to api-types.

## Auth

- Dev sessions go through the W0.1 dev IdP (`POST /v1/dev/identity/mint`), only registered when `BACKEND_ENVIRONMENT=development`. The mint signs a real HMAC bearer with `ENTERPRISE_AUTH_SECRET` so the verification path is shared with production. There is no `DEV_AUTH_BYPASS` shortcut. Production fails closed without `ENTERPRISE_AUTH_SECRET` and `ENTERPRISE_SERVICE_TOKEN`.
- With `ENTERPRISE_SERVICE_TOKEN` set, internal callers must also send `x-enterprise-org-id` and `x-enterprise-user-id`.
- Treat caller-supplied identity, role, scope, tenant as untrusted unless derived from a verified session, token, mTLS identity, or IdP claim.

## MCP

- OAuth: discovery + dynamic client registration when supported; per-server pre-registered client fields (`client_id`, `client_secret`, `scope`, `authorization_endpoint`, `token_endpoint`) when not.
- Secrets stored via `TokenVault`. The local adapter is dev-only — production must inject a managed adapter and a persistent MCP registry store.

## Audit logging

Audit logging is a compliance control. Never call it complete if the adapter is no-op, in-memory only, mutable without controls, or not exportable to customer SIEM.
