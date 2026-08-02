# Backend Service

Core backend (`backend_app/`). Today owns: MCP registration, OAuth state, token vault, user skills, audit events. Target home for: tenants, IdP integration, permissions, product persistence, admin workflows, jobs.

## Before changing behavior

Read [docs/README.md](docs/README.md) to find the relevant doc, then read it before implementing.
Architecture, features, guides, and reference docs are the source of truth.

## Boundaries

- `/internal/v1/*` is consumed only by `ai-backend` (MCP cards, client sessions, RPC proxy, skill bundles). It is **not** exposed via `backend-facade`.
- App-facing routes (anything reachable from the browser) go through `backend-facade`. Never let an app call `backend` directly.
- This service must not import `services/ai-backend/src` or `services/backend-facade/src`. Cross-service work is HTTP only.
- Use this service's own `.venv`. Never add a sibling service to `PYTHONPATH`.

### Concerns migrating here from `ai-backend`

A boundary audit ([docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md](../../docs/audit/ai-backend-smells/BOUNDARY-AUDIT.md)) found ~36k LOC in `ai-backend` whose target home is this service — billing/pricing/usage rollups, tenant + workspace admin CRUD, product persistence (sharing, inbox, todos, notifications, model catalog), one-shot migrations, eval/promotion tooling, and a duplicate audit stream + logging contract. Expect these to arrive incrementally.

Two rules when accepting one:

- **Serve policy as a run-start snapshot, not a per-call endpoint.** `ai-backend` enforces policy inside the graph loop; a per-tool-call hop to this service would put a network round-trip on the hottest path. Follow `/internal/v1/policies/tool-use`: one fetch per run, enforced in-process there, facts POSTed back afterwards.
- **Own the authoring/storage/admin half only.** The enforcement point stays in the runtime by design — see the PDP/PEP rule in the root [CLAUDE.md](../../CLAUDE.md).

## Public contracts

Update [packages/api-types](../../packages/api-types) when public app-facing payloads or routes change. `/internal/v1/*` is not mirrored to api-types.

## Auth

- Dev sessions go through the W0.1 dev IdP (`POST /v1/dev/identity/mint`), only registered when `BACKEND_ENVIRONMENT=development`. The mint signs a real HMAC bearer with `ENTERPRISE_AUTH_SECRET` so the verification path is shared with production. There is no `DEV_AUTH_BYPASS` shortcut. Production fails closed without `ENTERPRISE_AUTH_SECRET` and `ENTERPRISE_SERVICE_TOKEN`.
- With `ENTERPRISE_SERVICE_TOKEN` set, internal callers must also send `x-enterprise-org-id` and `x-enterprise-user-id`.
- Treat caller-supplied identity, role, scope, tenant as untrusted unless derived from a verified session, token, mTLS identity, or IdP claim.
- For curl / Postman recipes (mint a bearer, hit `/v1/me/profile`, etc.), see [`docs/dev-testing.md`](../../docs/dev-testing.md). The facade re-exposes `/v1/dev/personas` and `/v1/dev/identity/mint` so non-browser callers stay on the public surface.

## MCP

- OAuth: discovery + dynamic client registration when supported; per-server pre-registered client fields (`client_id`, `client_secret`, `scope`, `authorization_endpoint`, `token_endpoint`) when not.
- Secrets stored via `TokenVault`. The local adapter is dev-only — production must inject a managed adapter and a persistent MCP registry store.

## Audit logging

Audit logging is a compliance control. Never call it complete if the adapter is no-op, in-memory only, mutable without controls, or not exportable to customer SIEM.
