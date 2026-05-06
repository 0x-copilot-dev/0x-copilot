# Cluster: Authentication

**Path:** `apps/frontend/src/features/auth/`  
**Last reviewed:** 2026-05-06

## Scope

- [`AuthContext.tsx`](../../../apps/frontend/src/features/auth/AuthContext.tsx) — session machine, bearer storage, workspace pick, unauthorized handler wiring.
- Screens: [`LoginScreen.tsx`](../../../apps/frontend/src/features/auth/LoginScreen.tsx), [`MfaPrompt.tsx`](../../../apps/frontend/src/features/auth/MfaPrompt.tsx).
- Dev-only: [`devIdp.ts`](../../../apps/frontend/src/features/auth/devIdp.ts) (tree-shaken from production builds per comments).

## Unused / ts-prune signals

Most exported types (`AuthStatus`, `AuthState`, `AuthContextValue`, …) report `(used in module)` — they exist for React context typing and external consumption via `useAuth()`.

| Area              | Assessment                                                                                                                                      |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `DevMintResponse` | `devIdp.ts` — typing for dev mint flow; production bundle should not reference runtime paths (verify build analyzer if tightening bundle size). |

No **unused modules** were identified under `features/auth/` at this revision.

## Smells

- **Bearer storage policy** — Comments describe `localStorage` opt-in vs in-memory defaults; ensure product docs match implemented behavior for regulated deployments (workspace rule: treat identity as untrusted unless verified — align UX copy with actual token handling).

## Confidence

**Low** for dead code in this cluster; **high** that the main maintenance risk is cross-layer session documentation, not orphan files.
