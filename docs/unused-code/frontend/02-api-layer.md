# Cluster: API layer (`src/api`)

**Path:** `apps/frontend/src/api/`  
**Last reviewed:** 2026-05-06

## Scope

HTTP/SSE clients and shared request helpers: [`http.ts`](../../../apps/frontend/src/api/http.ts), [`config.ts`](../../../apps/frontend/src/api/config.ts), [`agentApi.ts`](../../../apps/frontend/src/api/agentApi.ts), [`authApi.ts`](../../../apps/frontend/src/api/authApi.ts), [`mcpApi.ts`](../../../apps/frontend/src/api/mcpApi.ts), [`meApi.ts`](../../../apps/frontend/src/api/meApi.ts), [`workspaceApi.ts`](../../../apps/frontend/src/api/workspaceApi.ts), [`skillsApi.ts`](../../../apps/frontend/src/api/skillsApi.ts), [`sessionApi.ts`](../../../apps/frontend/src/api/sessionApi.ts), [`useResource.ts`](../../../apps/frontend/src/api/useResource.ts).

## Candidate dead code

| Symbol               | File                                                            | Severity                   | Evidence                                                                           |
| -------------------- | --------------------------------------------------------------- | -------------------------- | ---------------------------------------------------------------------------------- |
| `getSessionIdentity` | [`sessionApi.ts`](../../../apps/frontend/src/api/sessionApi.ts) | **High confidence unused** | No imports elsewhere in `apps/frontend` (ripgrep). Calls legacy `GET /v1/session`. |

[`authApi.ts`](../../../apps/frontend/src/api/authApi.ts) documents that `sessionApi` is distinct from `/v1/auth/*` and implies eventual consolidation once AuthContext fully replaces legacy bootstrap — [`AuthContext.tsx`](../../../apps/frontend/src/features/auth/AuthContext.tsx) uses `fetchCurrentSession` from `authApi`, not `getSessionIdentity`.

### Recommended follow-up

- **Remove or wire** `getSessionIdentity`: either delete [`sessionApi.ts`](../../../apps/frontend/src/api/sessionApi.ts) if no runtime caller is planned, or replace remaining conceptual references in docs with the auth session path only.
- If `/v1/session` must remain for a specific integration, add a single caller and tests; otherwise remove the endpoint usage from the client to avoid confusion.

## ts-prune noise (likely intentional)

| Symbol                              | Notes                                                                              |
| ----------------------------------- | ---------------------------------------------------------------------------------- |
| `UnauthorizedError`, `newRequestId` | `http.ts` — module-internal or thin error surface.                                 |
| `ResourceState`                     | `useResource.ts` — type export consumed implicitly by callers typing hook results. |

## Smells

- **Dual session story** — Comments in `authApi.ts` vs `sessionApi.ts` describe overlapping “who am I?” responsibilities. Until one path wins, new contributors may add duplicate bootstrap logic.
- **ARCHITECTURE.md drift** — [`ARCHITECTURE.md`](../../../apps/frontend/ARCHITECTURE.md) still mentions `sessionApi` loading identity before chat mounts; implementation uses AuthContext + `authApi` ([`AuthContext.tsx`](../../../apps/frontend/src/features/auth/AuthContext.tsx)). Consider aligning docs with code when touching this area.

## Confidence

**High** on `getSessionIdentity` being unused in the tree at this revision; **medium** that a future branch might intend to call it (grep before deleting).
