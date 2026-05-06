# Cluster: API layer (`src/api`)

**Path:** `apps/frontend/src/api/`  
**Last reviewed:** 2026-05-06

## Scope

HTTP/SSE clients and shared request helpers: [`http.ts`](../../../apps/frontend/src/api/http.ts), [`config.ts`](../../../apps/frontend/src/api/config.ts), [`agentApi.ts`](../../../apps/frontend/src/api/agentApi.ts), [`authApi.ts`](../../../apps/frontend/src/api/authApi.ts), [`mcpApi.ts`](../../../apps/frontend/src/api/mcpApi.ts), [`meApi.ts`](../../../apps/frontend/src/api/meApi.ts), [`workspaceApi.ts`](../../../apps/frontend/src/api/workspaceApi.ts), [`skillsApi.ts`](../../../apps/frontend/src/api/skillsApi.ts), [`sessionApi.ts`](../../../apps/frontend/src/api/sessionApi.ts), [`useResource.ts`](../../../apps/frontend/src/api/useResource.ts).

## Candidate dead code

_**RESOLVED at `a78bfc0`.**_ `sessionApi.ts` (with `getSessionIdentity`) was deleted; the comment in [`authApi.ts`](../../../apps/frontend/src/api/authApi.ts) that referenced it was removed in the same pass. `AuthContext` continues to use `fetchCurrentSession` from `authApi`.

## ts-prune noise (likely intentional)

| Symbol                              | Notes                                                                              |
| ----------------------------------- | ---------------------------------------------------------------------------------- |
| `UnauthorizedError`, `newRequestId` | `http.ts` — module-internal or thin error surface.                                 |
| `ResourceState`                     | `useResource.ts` — type export consumed implicitly by callers typing hook results. |

## Smells

- **ARCHITECTURE.md drift** — [`ARCHITECTURE.md`](../../../apps/frontend/ARCHITECTURE.md) may still mention `sessionApi` loading identity before chat mounts; implementation uses AuthContext + `authApi` ([`AuthContext.tsx`](../../../apps/frontend/src/features/auth/AuthContext.tsx)). Align the doc when touching this area.
