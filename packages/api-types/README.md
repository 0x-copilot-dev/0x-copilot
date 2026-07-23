# API Types

`@0x-copilot/api-types` contains shared TypeScript contracts for
app-facing API payloads, runtime events, and public route data consumed by
frontend code.

The package is currently hand-maintained. Treat it as a contract stewardship
package, not as a runtime client or business-logic module.

## Consumers

- `apps/frontend/src/api/*`
- Frontend feature models that need typed API payloads
- Future generated clients or contract tests

## Ownership

Server-side validation currently lives in Pydantic models owned by the service
that implements the route:

- `services/backend/src/backend_app/contracts.py` for MCP and skills routes.
- `services/ai-backend/src/runtime_api/schemas/` and runtime contracts for
  agent routes, events, approvals, and streaming payloads.
- `services/ai-backend/src/runtime_api/schemas/local_models.py` for the
  local-model status, catalog, runtime-control, and pull-stream payloads
  (`src/localModels.ts` mirrors it; see `SPEC.md` for the gating rules).
- `services/backend-facade` owns the product route surface and may later own
  explicit facade response models if it starts shaping responses.

## Change Rules

- Do not add business logic or HTTP behavior here.
- Keep names aligned with server contracts unless there is a documented app
  compatibility reason to differ.
- Add optional fields only when the server actually may omit them.
- Treat removals, enum narrowing, and required-field additions as breaking
  contract changes.
- Update `SPEC.md` and relevant service docs when public payload semantics
  change.

## Checks

```bash
npm run typecheck --workspace @0x-copilot/api-types
```

When route contracts change, also run typecheck for frontend consumers.
