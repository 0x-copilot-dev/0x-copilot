# PRD-05 — Web host registration + flagged Run route (Wave 1)

**Goal:** end the desktop-only asymmetry. The web app registers the same renderer stack at bootstrap (tier-1 + archetypes + tier-3) and mounts the real `RunDestination` for the `run` slug behind a feature flag, replacing the legacy `ChatScreen` only when the flag is on.

**Depends on:** PRD-01 (and benefits from 03/04 at runtime, but compiles against published package APIs — safe to run in parallel). **Scope:** `apps/frontend` only.

**Coordination note:** the frontend-parity-v3 effort also touches web `run`. This PRD is deliberately minimal — registration + a flagged route — so it composes with (not blocks) that effort. If parity has already mounted `RunDestination` on web by execution time, this PRD reduces to the registration module + flag removal review.

## Scope — files

| File | Change |
|---|---|
| `src/app/registerSurfaces.ts` | NEW — mirrors `apps/desktop/renderer/bootstrap.tsx:44-45`: `registerGenericStructuredDiff()` then `registerSurfaceRenderers()` (which now includes archetypes per PRD-03). Idempotent; called once from app bootstrap. NO Tier2Bridge (desktop-only IPC; web tier-2 arrives in PRD-10) |
| `src/app/App.tsx` | EXTEND — import + call `registerSurfaces()` at module init; `run` slug dispatch: `if (flags.runCockpitWeb) render <RunRoute/> else <ChatScreen/>` (legacy path byte-identical when flag off) |
| `src/features/run/RunRoute.tsx` | NEW — the web binder for `RunDestination`, following the existing `features/*/Route.tsx` pattern: builds/injects the web Transport port + KeyValueStore + DeploymentProfile providers exactly as the other routes do, mounts `RunDestination` full-bleed. Duplicate the minimal projection glue from the desktop binder (`apps/desktop/renderer/destinationBinders.tsx` run case) — duplication across binders is the documented pattern (`apps/*→apps/*` is a hard boundary; do NOT import from desktop) |
| flag plumbing | EXTEND — add `runCockpitWeb` to the app's existing feature-flag mechanism (find it; if none exists, a `localStorage`-read constant module in the app layer is acceptable — this is app code, not chat-surface, so `localStorage` is legal here). Default OFF |

## Acceptance criteria

1. `npm run typecheck --workspace @0x-copilot/frontend` + `npm run build --workspace @0x-copilot/frontend` green.
2. Flag OFF: `run` renders the legacy `ChatScreen` — snapshot/behavioral test proving no regression.
3. Flag ON (test env): `run` mounts `RunDestination`; a seeded event array containing a PRD-01 fixture envelope renders the Record archetype in the center pane (integration test with MockTransport).
4. `registerSurfaces()` double-invocation safe (registry replace semantics — assert no duplicate adapters).
5. Web has NO Tier2Bridge reference and no IPC imports.

## Non-goals / guardrails

- No visual redesign of web chrome; no parity work beyond the mount.
- Do not modify `packages/chat-surface` or `packages/surface-renderers` (if the mount reveals a missing export, flag it back to PRD-03/04 — do not deep-import `src/`).
- Do not enable the flag by default; flipping it is a product decision after Wave-1 verification.
